#!/usr/bin/env python
"""OpenAI-backed provider command for benchmark agent runs.

This script is designed to be called through `command_agent.py`.
It reads a task descriptor from stdin and emits the provider envelope expected
by the benchmark runner.

Required environment:
  OPENAI_API_KEY
  AETHER_OPENAI_MODEL

Optional environment:
  AETHER_OPENAI_BASE_URL
  AETHER_OPENAI_COST_PER_1M_INPUT
  AETHER_OPENAI_COST_PER_1M_OUTPUT
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.openai.com/v1"


def main() -> int:
    load_local_env()
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("AETHER_OPENAI_MODEL")
    if not api_key:
        print("OPENAI_API_KEY is required.", file=sys.stderr)
        return 2
    if not model:
        print("AETHER_OPENAI_MODEL is required.", file=sys.stderr)
        return 2

    descriptor = json.load(sys.stdin)
    started = time.perf_counter()
    try:
        response = call_responses_api(api_key, model, descriptor)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"OpenAI API error {exc.code}: {body}", file=sys.stderr)
        return 1

    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    payload = parse_model_json(response)
    patch = payload.get("patch") if isinstance(payload.get("patch"), dict) else payload
    if not isinstance(patch, dict):
        print("OpenAI response did not contain a patch object.", file=sys.stderr)
        return 1

    usage = normalize_usage(response.get("usage", {}), model, latency_ms)
    print(json.dumps({"patch": patch, "usage": usage}, sort_keys=True))
    return 0


def call_responses_api(api_key: str, model: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    base_url = os.environ.get("AETHER_OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    body = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are an Aether benchmark coding agent. "
                    "Return only JSON matching the requested schema. "
                    "Do not include prose outside the JSON object."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "instruction": "Create an Aether patch for this benchmark task.",
                    "task": descriptor,
                    "constraints": [
                        "Use source_file as target.file unless the task explicitly requires a different safety test.",
                        "Do not rewrite the whole file.",
                        "Return compact Aether patch JSON only."
                    ],
                    "required_output": "JSON object with a patch field containing Aether patch JSON.",
                }, sort_keys=True),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "aether_benchmark_patch",
                "strict": False,
                "schema": {
                    "type": "object",
                    "properties": {
                        "patch": {"type": "object"}
                    },
                    "required": ["patch"],
                    "additionalProperties": True,
                },
            }
        },
    }
    request = urllib.request.Request(
        f"{base_url}/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_model_json(response: dict[str, Any]) -> dict[str, Any]:
    if isinstance(response.get("output_text"), str):
        return json.loads(response["output_text"])

    chunks: list[str] = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if not chunks:
        raise ValueError("OpenAI response did not include text output.")
    return json.loads("".join(chunks))


def normalize_usage(raw_usage: dict[str, Any], model: str, latency_ms: float) -> dict[str, Any]:
    input_tokens = raw_usage.get("input_tokens")
    output_tokens = raw_usage.get("output_tokens")
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
        input_rate = float(os.environ["AETHER_OPENAI_COST_PER_1M_INPUT"])
        output_rate = float(os.environ["AETHER_OPENAI_COST_PER_1M_OUTPUT"])
    except (KeyError, ValueError):
        return None
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    return round((input_tokens / 1_000_000 * input_rate) + (output_tokens / 1_000_000 * output_rate), 8)


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
