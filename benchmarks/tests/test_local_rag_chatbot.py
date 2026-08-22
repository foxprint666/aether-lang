from __future__ import annotations

import sys
from pathlib import Path


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS / "rag"))

from local_rag_chatbot import evaluate, load_questions, load_sections  # noqa: E402


def test_aether_rag_preserves_quality_while_reducing_context() -> None:
    sections = load_sections(BENCHMARKS / "rag" / "corpus")
    questions = load_questions(BENCHMARKS / "rag" / "questions.json")

    result = evaluate(sections, questions)
    summary = result["summary"]

    assert summary["aether"]["quality_score"] >= summary["raw"]["quality_score"]
    assert summary["efficiency"]["context_token_savings_pct"] > 40
    assert summary["efficiency"]["total_token_savings_pct"] > 25

