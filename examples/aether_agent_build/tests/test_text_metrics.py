def test_summarize_text_counts_and_terms():
    from text_metrics import summarize_text

    summary = summarize_text("Aether heals code. Aether saves tokens.", top_n=2)
    assert summary["characters"] == 39
    assert summary["words"] == 6
    assert summary["unique_words"] == 5
    assert summary["top_terms"] == [("aether", 2), ("code", 1)]

def test_reading_time_rejects_invalid_speed():
    import pytest
    from text_metrics import reading_time_minutes

    with pytest.raises(ValueError):
        reading_time_minutes("hello world", words_per_minute=0)