#!/usr/bin/env python
"""Local raw-vs-Aether RAG chatbot and evaluator.

The raw path retrieves large text chunks. The Aether path compiles the corpus into
section/fact states, plans over those states, and answers from compact evidence.
No network or model API is required; this is a deterministic benchmark harness.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "benchmarks" / "rag" / "corpus"
DEFAULT_QUESTIONS = ROOT / "benchmarks" / "rag" / "questions.json"
STOPWORDS = {
    "a", "an", "and", "are", "as", "be", "by", "for", "from", "how", "in",
    "is", "it", "name", "of", "or", "over", "should", "that", "the", "to",
    "two", "versus", "what", "when", "where", "why", "with",
}


@dataclass(frozen=True)
class Section:
    doc: str
    heading: str
    anchor: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.doc}#{self.anchor}"


@dataclass(frozen=True)
class RetrievalResult:
    answer: str
    citations: list[str]
    context: str
    mode: str
    latency_ms: float
    state_count: int


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", help="Ask one local RAG question.")
    parser.add_argument("--mode", choices=("raw", "aether", "compare"), default="compare")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--eval", action="store_true", help="Run the bundled QA benchmark.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sections = load_sections(args.corpus)
    if args.eval:
        output = evaluate(sections, load_questions(args.questions))
    elif args.question:
        output = ask(sections, args.question, args.mode)
    else:
        parser.error("Provide a question or --eval")
    rendered = json.dumps(output, indent=2, sort_keys=True) if args.json else render(output)
    print(rendered)
    return 0


def ask(sections: list[Section], question: str, mode: str) -> dict[str, Any]:
    if mode == "compare":
        raw = answer_raw(sections, question)
        aether = answer_aether(sections, question)
        return {
            "question": question,
            "raw": result_metrics(raw),
            "aether": result_metrics(aether),
            "efficiency": efficiency(result_metrics(raw), result_metrics(aether)),
        }
    result = answer_raw(sections, question) if mode == "raw" else answer_aether(sections, question)
    return {"question": question, mode: result_metrics(result)}


def evaluate(sections: list[Section], questions: list[dict[str, Any]]) -> dict[str, Any]:
    records = []
    for item in questions:
        raw = answer_raw(sections, item["question"])
        aether = answer_aether(sections, item["question"])
        records.append({
            "id": item["id"],
            "question": item["question"],
            "raw": score(raw, item),
            "aether": score(aether, item),
        })
    return {
        "report_version": "local-rag-ab-v1",
        "questions": len(records),
        "records": records,
        "summary": summarize(records),
    }


def answer_raw(sections: list[Section], question: str) -> RetrievalResult:
    started = time.perf_counter()
    query = token_set(question)
    chunks = make_raw_chunks(sections, size=3)
    ranked = rank([(citation, text) for citation, text in chunks], query)[:2]
    context = "\n\n".join(text for _, text in ranked)
    answer = synthesize_answer(question, ranked, max_sentences=4)
    citations = [citation for citation, _ in ranked]
    return RetrievalResult(answer, citations, context, "raw", elapsed_ms(started), len(chunks))


def answer_aether(sections: list[Section], question: str) -> RetrievalResult:
    started = time.perf_counter()
    query = token_set(question)
    states = compile_fact_states(sections)
    section_plan = rank(
        [(section.citation, f"{section.heading}: {section.text}") for section in sections],
        query,
    )[:1]
    planned_sections = {citation for citation, _ in section_plan}
    section_facts = [
        item for item in states
        if base_citation(item[0]) in planned_sections
    ]
    ranked = unique_ranked(section_facts + rank(states, query), query)[:5]
    context = "\n".join(text for _, text in ranked)
    answer = synthesize_answer(question, ranked, max_sentences=5)
    citations = [citation for citation, _ in ranked]
    return RetrievalResult(answer, citations, context, "aether", elapsed_ms(started), len(states))


def compile_fact_states(sections: list[Section]) -> list[tuple[str, str]]:
    states = []
    for section in sections:
        for index, sentence in enumerate(sentences(section.text), start=1):
            if len(token_set(sentence)) < 3:
                continue
            states.append((f"{section.citation}:s{index}", f"{section.heading}: {sentence}"))
    return states


def make_raw_chunks(sections: list[Section], size: int) -> list[tuple[str, str]]:
    chunks = []
    for offset in range(0, len(sections), size):
        selected = sections[offset:offset + size]
        citation = "+".join(section.citation for section in selected)
        text = "\n".join(f"{section.heading}\n{section.text}" for section in selected)
        chunks.append((citation, text))
    return chunks


def rank(items: list[tuple[str, str]], query: set[str]) -> list[tuple[str, str]]:
    return [
        item for _, item in sorted(
            ((score_text(text, query), (citation, text)) for citation, text in items),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if score_text(item[1], query) > 0
    ]


def unique_ranked(items: list[tuple[str, str]], query: set[str]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    ranked = []
    for citation, text in rank(items, query):
        key = f"{citation}\0{text}"
        if key not in seen:
            seen.add(key)
            ranked.append((citation, text))
    return ranked


def score_text(text: str, query: set[str]) -> float:
    terms = token_set(text)
    overlap = len(query & terms)
    if overlap == 0:
        return 0.0
    return overlap / math.sqrt(max(len(terms), 1))


def synthesize_answer(question: str, evidence: list[tuple[str, str]], max_sentences: int) -> str:
    selected: list[str] = []
    query = token_set(question)
    for _, text in evidence:
        ranked = sorted(sentences(text), key=lambda sentence: score_text(sentence, query), reverse=True)
        for sentence in ranked:
            if sentence and sentence not in selected:
                selected.append(sentence)
            if len(selected) >= max_sentences:
                return " ".join(selected)
    if selected:
        return " ".join(selected)
    return "I do not have enough local evidence to answer."


def score(result: RetrievalResult, question: dict[str, Any]) -> dict[str, Any]:
    metrics = result_metrics(result)
    answer_lower = result.answer.lower()
    required = [term.lower() for term in question.get("required_terms", [])]
    citations = {
        base_citation(part)
        for citation in result.citations
        for part in citation.split("+")
    }
    expected = set(question.get("expected_citations", []))
    term_hits = sum(term in answer_lower for term in required)
    citation_hits = len(citations & expected)
    quality = 0.0
    if required:
        quality += 0.8 * term_hits / len(required)
    if expected:
        quality += 0.2 * min(1.0, citation_hits / len(expected))
    metrics.update({
        "quality_score": round(quality, 6),
        "required_terms_hit": term_hits,
        "required_terms_total": len(required),
        "expected_citation_hit": citation_hits,
        "expected_citation_total": len(expected),
    })
    return metrics


def result_metrics(result: RetrievalResult) -> dict[str, Any]:
    return {
        "mode": result.mode,
        "answer": result.answer,
        "citations": result.citations,
        "context_tokens": count_tokens(result.context),
        "output_tokens": count_tokens(result.answer),
        "context_bytes": len(result.context.encode("utf-8")),
        "output_bytes": len(result.answer.encode("utf-8")),
        "latency_ms": round(result.latency_ms, 6),
        "state_count": result.state_count,
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    raw = aggregate(records, "raw")
    aether = aggregate(records, "aether")
    return {
        "raw": raw,
        "aether": aether,
        "efficiency": efficiency(raw, aether),
    }


def aggregate(records: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    selected = [record[mode] for record in records]
    return {
        "quality_score": round(sum(item["quality_score"] for item in selected) / len(selected), 6),
        "context_tokens": sum(item["context_tokens"] for item in selected),
        "output_tokens": sum(item["output_tokens"] for item in selected),
        "total_tokens": sum(item["context_tokens"] + item["output_tokens"] for item in selected),
        "latency_ms": round(sum(item["latency_ms"] for item in selected), 6),
        "expected_citation_rate": round(
            sum(item["expected_citation_hit"] for item in selected)
            / max(sum(item["expected_citation_total"] for item in selected), 1),
            6,
        ),
    }


def efficiency(raw: dict[str, Any], aether: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_delta": round(aether.get("quality_score", 0) - raw.get("quality_score", 0), 6),
        "context_token_savings_pct": pct(raw["context_tokens"], aether["context_tokens"]),
        "output_token_savings_pct": pct(raw["output_tokens"], aether["output_tokens"]),
        "total_token_savings_pct": pct(raw.get("total_tokens", raw["context_tokens"] + raw["output_tokens"]), aether.get("total_tokens", aether["context_tokens"] + aether["output_tokens"])),
        "latency_savings_pct": pct(raw["latency_ms"], aether["latency_ms"]),
    }


def load_sections(directory: Path) -> list[Section]:
    sections: list[Section] = []
    for path in sorted(directory.glob("*.md")):
        current_heading = path.stem
        current_lines: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                append_section(sections, path.name, current_heading, current_lines)
                current_heading = line[3:].strip()
                current_lines = []
            elif not line.startswith("# "):
                current_lines.append(line)
        append_section(sections, path.name, current_heading, current_lines)
    return [section for section in sections if section.text.strip()]


def append_section(sections: list[Section], doc: str, heading: str, lines: list[str]) -> None:
    text = "\n".join(line for line in lines if line.strip()).strip()
    if text:
        sections.append(Section(doc, heading, anchor(heading), text))


def load_questions(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("questions")
    if not isinstance(values, list):
        raise ValueError(f"Expected questions list in {path}")
    return values


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")) if part.strip()]


def token_set(text: str) -> set[str]:
    return {token for token in re.findall(r"[A-Za-z0-9_.%*-]+", text.lower()) if token not in STOPWORDS}


def count_tokens(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_.%*-]+|[^\sA-Za-z0-9_]", text))


def pct(left: float, right: float) -> float | None:
    if left == 0:
        return None
    return round((left - right) / left * 100, 6)


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def anchor(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def base_citation(citation: str) -> str:
    return citation.split("+", 1)[0].rsplit(":s", 1)[0]


def render(value: dict[str, Any]) -> str:
    if "summary" in value:
        summary = value["summary"]
        return (
            f"Raw quality: {summary['raw']['quality_score']}\n"
            f"Aether quality: {summary['aether']['quality_score']}\n"
            f"Context token savings: {summary['efficiency']['context_token_savings_pct']}%\n"
            f"Total token savings: {summary['efficiency']['total_token_savings_pct']}%"
        )
    return json.dumps(value, indent=2, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
