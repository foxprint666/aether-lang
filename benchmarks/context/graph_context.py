#!/usr/bin/env python
"""Lightweight code-graph extraction for benchmark context scoping."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    start_line: int
    end_line: int
    text: str


def source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def build_raw_packet(task: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "context_strategy": "raw_source",
        "task_id": task["task_id"],
        "language": task["language"],
        "repository": task["repository"],
        "source_file": task["source_file"],
        "description": task["description"],
        "source_sha256": source_sha256(source),
        "source": source,
    }


def build_graph_packet(task: dict[str, Any], source: str) -> dict[str, Any]:
    symbols = extract_symbols(source, task["language"])
    edges = import_edges(source, task["language"])
    selected = select_symbols(task["description"], symbols)
    return {
        "context_strategy": "graph_scoped",
        "task_id": task["task_id"],
        "language": task["language"],
        "repository": task["repository"],
        "source_file": task["source_file"],
        "description": task["description"],
        "source_sha256": source_sha256(source),
        "graph": {
            "node_count": len(symbols),
            "edge_count": len(edges),
            "selected_names": [symbol.name for symbol in selected],
        },
        "selected_symbols": [
            {
                "name": symbol.name,
                "kind": symbol.kind,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "text": symbol.text,
            }
            for symbol in selected
        ],
    }


def extract_symbols(source: str, language: str) -> list[Symbol]:
    if language == "python":
        return extract_python_symbols(source)
    if language == "javascript":
        return extract_javascript_symbols(source)
    raise ValueError(f"Unsupported graph context language: {language}")


def extract_python_symbols(source: str) -> list[Symbol]:
    tree = ast.parse(source)
    lines = source.splitlines()
    symbols: list[Symbol] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = int(getattr(node, "end_lineno", node.lineno))
            symbols.append(Symbol(
                name=node.name,
                kind="class" if isinstance(node, ast.ClassDef) else "function",
                start_line=int(node.lineno),
                end_line=end,
                text="\n".join(lines[node.lineno - 1:end]),
            ))
    return sorted(symbols, key=lambda item: (item.start_line, item.name))


def extract_javascript_symbols(source: str) -> list[Symbol]:
    lines = source.splitlines()
    symbols: list[Symbol] = []
    class_ranges: list[tuple[str, int, int]] = []
    for index, line in enumerate(lines, start=1):
        match = re.search(r"\bclass\s+([A-Za-z_$][\w$]*)", line)
        if match:
            end = block_end(lines, index)
            class_ranges.append((match.group(1), index, end))
            symbols.append(Symbol(match.group(1), "class", index, end, "\n".join(lines[index - 1:end])))
        match = re.search(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", line)
        if match:
            end = block_end(lines, index)
            symbols.append(Symbol(match.group(1), "function", index, end, "\n".join(lines[index - 1:end])))
        match = re.search(r"\b(?:export\s+default\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", line)
        if match and all(existing.name != match.group(1) or existing.start_line != index for existing in symbols):
            end = block_end(lines, index)
            symbols.append(Symbol(match.group(1), "function", index, end, "\n".join(lines[index - 1:end])))

    for class_name, start, end in class_ranges:
        for index in range(start + 1, end):
            line = lines[index - 1]
            match = re.match(r"\s*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", line)
            if match and match.group(1) not in {"if", "for", "while", "switch", "catch"}:
                method_end = block_end(lines, index)
                symbols.append(Symbol(
                    match.group(1),
                    "method",
                    index,
                    method_end,
                    "\n".join(lines[index - 1:method_end]),
                ))
    return sorted(symbols, key=lambda item: (item.start_line, item.name, item.kind))


def block_end(lines: list[str], start_line: int) -> int:
    depth = 0
    started = False
    for index in range(start_line, len(lines) + 1):
        line = lines[index - 1]
        depth += line.count("{")
        if "{" in line:
            started = True
        depth -= line.count("}")
        if started and depth <= 0:
            return index
    return start_line


def import_edges(source: str, language: str) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    if language == "python":
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    edges.append({"kind": "imports", "target": alias.name})
            elif isinstance(node, ast.ImportFrom) and node.module:
                edges.append({"kind": "imports", "target": node.module})
    elif language == "javascript":
        for match in re.finditer(r"\bimport\b[^;]*\bfrom\s+['\"]([^'\"]+)['\"]", source):
            edges.append({"kind": "imports", "target": match.group(1)})
    return edges


def select_symbols(description: str, symbols: list[Symbol]) -> list[Symbol]:
    tokens = {token.lower() for token in re.findall(r"[A-Za-z_$][\w$]*", description)}
    exact = [symbol for symbol in symbols if symbol.name.lower() in tokens]
    if exact:
        return exact
    fuzzy = [
        symbol for symbol in symbols
        if any(part and part in tokens for part in re.split(r"[_$]", symbol.name.lower()))
    ]
    if fuzzy:
        return fuzzy[:3]
    return symbols[:1]


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
