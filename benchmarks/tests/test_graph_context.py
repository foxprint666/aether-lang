from __future__ import annotations

import sys
from pathlib import Path


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))

from context.graph_context import build_graph_packet, build_raw_packet, extract_symbols  # noqa: E402


def test_python_graph_selects_named_function() -> None:
    source = """
def alpha():
    return 1

def beta(value):
    return value + 1
""".strip()
    task = {
        "task_id": "one",
        "language": "python",
        "repository": "repo",
        "source_file": "x.py",
        "description": "Change beta so it handles zero.",
    }

    packet = build_graph_packet(task, source)

    assert [item["name"] for item in packet["selected_symbols"]] == ["beta"]
    assert packet["graph"]["node_count"] == 2


def test_javascript_graph_extracts_class_methods() -> None:
    source = """
class Queue {
  enqueue(value) {
    return value;
  }

  peek() {
    return undefined;
  }
}
""".strip()

    symbols = extract_symbols(source, "javascript")

    assert {symbol.name for symbol in symbols} >= {"Queue", "enqueue", "peek"}


def test_javascript_graph_selects_generator_method_from_member_reference() -> None:
    source = """
class Queue {
  * drain() {
    yield 1;
  }
}
""".strip()
    task = {
        "task_id": "one",
        "language": "javascript",
        "repository": "repo",
        "source_file": "index.js",
        "description": "Extend Queue.drain with an optional nonnegative limit.",
    }

    packet = build_graph_packet(task, source)

    assert [item["name"] for item in packet["selected_symbols"]] == ["drain"]
    assert packet["selected_symbols"][0]["kind"] == "method"


def test_graph_packet_can_be_smaller_than_raw_packet_for_focused_context() -> None:
    source = "\n".join([
        "def target():",
        "    return 1",
        "",
        *[f"def filler_{index}():\n    return {index}" for index in range(200)],
    ])
    task = {
        "task_id": "one",
        "language": "python",
        "repository": "repo",
        "source_file": "x.py",
        "description": "Update target to return 2.",
    }

    raw = str(build_raw_packet(task, source))
    graph = str(build_graph_packet(task, source))

    assert len(graph) < len(raw)
