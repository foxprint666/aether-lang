#!/usr/bin/env python
"""OpenRouter-backed provider command for benchmark agent runs.

This script is designed to be called through `command_agent.py`.
It reads a task descriptor from stdin and emits the provider envelope expected
by the benchmark runner.

Required environment:
  OPENROUTER_API_KEY
  AETHER_OPENROUTER_MODEL

Optional environment:
  AETHER_OPENROUTER_BASE_URL
  AETHER_OPENROUTER_REFERER
  AETHER_OPENROUTER_TITLE
  AETHER_OPENROUTER_RETRIES
  AETHER_OPENROUTER_RETRY_SLEEP_CAP
  AETHER_OPENROUTER_COST_PER_1M_INPUT
  AETHER_OPENROUTER_COST_PER_1M_OUTPUT
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def main() -> int:
    load_local_env()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("AETHER_OPENROUTER_MODEL")
    if not api_key:
        print("OPENROUTER_API_KEY is required.", file=sys.stderr)
        return 2
    if not model:
        print("AETHER_OPENROUTER_MODEL is required.", file=sys.stderr)
        return 2

    descriptor = json.load(sys.stdin)
    started = time.perf_counter()
    try:
        response = call_chat_completions(api_key, model, descriptor)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"OpenRouter API error {exc.code}: {body}", file=sys.stderr)
        return 1

    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    payload = parse_model_json(response)
    patch = payload.get("patch") if isinstance(payload.get("patch"), dict) else payload
    if not isinstance(patch, dict):
        print("OpenRouter response did not contain a patch object.", file=sys.stderr)
        return 1
    patch, normalization_changes = normalize_patch(patch, descriptor, model)

    usage = normalize_usage(response.get("usage", {}), model, latency_ms)
    print(json.dumps({
        "patch": patch,
        "agent": {
            "provider_normalized": bool(normalization_changes),
            "provider_normalization_changes": normalization_changes,
            "raw_provider": "openrouter",
        },
        "usage": usage,
    }, sort_keys=True))
    return 0


def call_chat_completions(api_key: str, model: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    base_url = os.environ.get("AETHER_OPENROUTER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    retries = int_env("AETHER_OPENROUTER_RETRIES", 2)
    sleep_cap = float_env("AETHER_OPENROUTER_RETRY_SLEEP_CAP", 30.0)
    prompt = {
        "instruction": "Create an Aether patch for this benchmark task.",
        "task": descriptor,
        "constraints": [
            "Use source_file as target.file unless the task explicitly requires a different safety test.",
            "For sensitive_path failure tasks, target .env so Aether safety validation can reject it.",
            "Do not rewrite the whole file.",
            "A valid modify_function patch uses action=modify_function, target.file, target.symbol, target.symbol_type=function, changes.operation=replace_body, and changes.payload.",
            "Return compact JSON only.",
        ],
        "required_output": "JSON object with a patch field containing Aether patch JSON.",
    }
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an Aether benchmark coding agent. "
                    "Return only JSON matching the requested schema. "
                    "Do not include prose outside the JSON object."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, sort_keys=True)},
        ],
        "response_format": {"type": "json_object"},
    }
    url = f"{base_url}/chat/completions"
    encoded = json.dumps(body).encode("utf-8")
    last_error: urllib.error.HTTPError | None = None
    for attempt in range(1, retries + 2):
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=build_headers(api_key),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            if (
                exc.code not in {429, 500, 502, 503, 504}
                or non_retryable_quota(body_text)
                or attempt >= retries + 1
            ):
                raise BufferedHTTPError(exc, body_text) from exc
            last_error = BufferedHTTPError(exc, body_text)
            delay = min(retry_delay_seconds(body_text, exc.headers, attempt), sleep_cap)
            print(f"OpenRouter transient error {exc.code}; retrying in {delay:.3f}s.", file=sys.stderr)
            time.sleep(delay)
    if last_error is not None:
        raise last_error
    raise RuntimeError("OpenRouter request failed without an HTTP response.")


def build_headers(api_key: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = os.environ.get("AETHER_OPENROUTER_REFERER")
    title = os.environ.get("AETHER_OPENROUTER_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return headers


class BufferedHTTPError(urllib.error.HTTPError):
    def __init__(self, source: urllib.error.HTTPError, body: str) -> None:
        super().__init__(source.url, source.code, source.reason, source.headers, source.fp)
        self.body = body

    def read(self, amt: int | None = None) -> bytes:  # type: ignore[override]
        data = self.body.encode("utf-8")
        return data if amt is None else data[:amt]


def retry_delay_seconds(body: str, headers: Any, attempt: int) -> float:
    retry_after = headers.get("Retry-After") if headers else None
    if retry_after:
        try:
            return float(retry_after) + 0.5
        except ValueError:
            pass
    match = re.search(r"retry (?:after|in) ([0-9.]+)s", body, re.IGNORECASE)
    if match:
        return float(match.group(1)) + 0.5
    match = re.search(r"retry (?:after|in) ([0-9.]+)ms", body, re.IGNORECASE)
    if match:
        return float(match.group(1)) / 1000 + 0.5
    return min(2.0 ** attempt, 30.0)


def non_retryable_quota(body: str) -> bool:
    lowered = body.lower()
    return (
        "per day" in lowered
        or "daily" in lowered
        or "free-models-per-day" in lowered
        or "insufficient credits" in lowered
    )


def normalize_patch(patch: dict[str, Any], descriptor: dict[str, Any], model: str) -> tuple[dict[str, Any], list[str]]:
    normalized = dict(patch)
    normalization_changes: list[str] = []
    normalized.setdefault("schema_version", descriptor.get("patch_schema", {}).get("schema_version", "1.0"))
    if not is_uuid4(str(normalized.get("patch_id", ""))):
        normalized["patch_id"] = str(uuid4())
        normalization_changes.append("patch_id")
    if descriptor.get("failure_type") == "sensitive_path":
        if normalized.get("action") != "update_import":
            normalization_changes.append("action")
        normalized["action"] = "update_import"

    target = normalized.get("target")
    if not isinstance(target, dict):
        target = {}
        normalization_changes.append("target")
    if descriptor.get("failure_type") == "sensitive_path":
        if target.get("file") != ".env":
            normalization_changes.append("target.file")
        target["file"] = ".env"
    else:
        if "file" not in target:
            normalization_changes.append("target.file")
            target["file"] = descriptor.get("source_file")
    if normalized.get("action") == "modify_function":
        if "symbol_type" not in target:
            normalization_changes.append("target.symbol_type")
            target["symbol_type"] = "function"
        if "symbol" not in target:
            normalization_changes.append("target.symbol")
            target["symbol"] = infer_symbol(descriptor)
    normalized["target"] = {key: value for key, value in target.items() if value is not None}

    changes = normalized.get("changes")
    if not isinstance(changes, dict):
        changes = {}
        normalization_changes.append("changes")
    if descriptor.get("failure_type") == "sensitive_path":
        if "operation" not in changes:
            normalization_changes.append("changes.operation")
            changes["operation"] = "add_import"
        if "imports" not in changes:
            normalization_changes.append("changes.imports")
            changes["imports"] = ["import os"]
    elif normalized.get("action") == "modify_function":
        if "operation" not in changes:
            normalization_changes.append("changes.operation")
            changes["operation"] = "replace_body"
        if "payload" not in changes:
            normalization_changes.append("changes.payload")
            changes["payload"] = infer_payload(descriptor)
    normalized["changes"] = changes

    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        normalization_changes.append("metadata")
    metadata = sanitize_metadata(metadata, normalization_changes)
    metadata.setdefault("agent_id", "openrouter-provider")
    metadata.setdefault("model", model)
    metadata.setdefault("intent", str(descriptor.get("description", ""))[:500])
    metadata.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    normalized["metadata"] = metadata
    return normalized, normalization_changes


def sanitize_metadata(metadata: dict[str, Any], normalization_changes: list[str]) -> dict[str, Any]:
    allowed = {"agent_id", "model", "intent", "created_at"}
    extra = sorted(set(metadata) - allowed)
    if extra:
        normalization_changes.extend(f"metadata.{key}_removed" for key in extra)
    return {key: value for key, value in metadata.items() if key in allowed}


def is_uuid4(value: str) -> bool:
    return bool(re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
        value,
    ))


def infer_symbol(descriptor: dict[str, Any]) -> str | None:
    acceptance = descriptor.get("acceptance") if isinstance(descriptor.get("acceptance"), dict) else {}
    for value in acceptance.get("expected_content") or []:
        match = re.search(r"\b(?:def|function)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", str(value))
        if match:
            return match.group(1)
    source = str(descriptor.get("source", ""))
    for pattern in [r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("]:
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    return None


def infer_payload(descriptor: dict[str, Any]) -> str | None:
    acceptance = descriptor.get("acceptance") if isinstance(descriptor.get("acceptance"), dict) else {}
    for value in acceptance.get("expected_content") or []:
        text = str(value)
        if "return " in text:
            return text[text.index("return "):].strip()
    return None


def parse_model_json(response: dict[str, Any]) -> dict[str, Any]:
    chunks: list[str] = []
    for choice in response.get("choices", []):
        message = choice.get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
    if not chunks:
        raise ValueError("OpenRouter response did not include message content.")
    return json.loads(extract_json_text("".join(chunks)))


def extract_json_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return stripped


def normalize_usage(raw_usage: dict[str, Any], model: str, latency_ms: float) -> dict[str, Any]:
    input_tokens = raw_usage.get("prompt_tokens")
    output_tokens = raw_usage.get("completion_tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tool_calls": 0,
        "latency_ms": latency_ms,
        "cost_usd": estimate_cost(input_tokens, output_tokens),
        "model": model,
    }


def estimate_cost(input_tokens: Any, output_tokens: Any) -> float | None:
    try:
        input_rate = float(os.environ["AETHER_OPENROUTER_COST_PER_1M_INPUT"])
        output_rate = float(os.environ["AETHER_OPENROUTER_COST_PER_1M_OUTPUT"])
    except (KeyError, ValueError):
        return None
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    return round((input_tokens / 1_000_000 * input_rate) + (output_tokens / 1_000_000 * output_rate), 8)


def int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    raise SystemExit(main())
