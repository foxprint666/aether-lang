def normalize_words(text: str) -> list[str]:
    """Return lower-cased words, preserving apostrophes inside words."""
    import re

    return re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text.lower())

def term_frequencies(text: str) -> dict[str, int]:
    """Count normalized word frequencies."""
    counts: dict[str, int] = {}
    for word in normalize_words(text):
        counts[word] = counts.get(word, 0) + 1
    return counts

def top_terms(text: str, limit: int = 5) -> list[tuple[str, int]]:
    """Return the most common words, tie-breaking alphabetically."""
    if limit < 1:
        return []
    counts = term_frequencies(text)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:limit]

def reading_time_minutes(text: str, words_per_minute: int = 200) -> float:
    """Estimate reading time in minutes, rounded to two decimals."""
    if words_per_minute <= 0:
        raise ValueError("words_per_minute must be positive")
    word_count = len(normalize_words(text))
    return round(word_count / words_per_minute, 2)

def summarize_text(text: str, *, top_n: int = 5, words_per_minute: int = 200) -> dict[str, object]:
    """Return a compact deterministic summary for agent-readable text analysis."""
    words = normalize_words(text)
    return {
        "characters": len(text),
        "words": len(words),
        "unique_words": len(set(words)),
        "reading_time_minutes": reading_time_minutes(text, words_per_minute),
        "top_terms": top_terms(text, top_n),
    }