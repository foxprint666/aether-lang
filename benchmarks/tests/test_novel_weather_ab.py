from __future__ import annotations

import sys
import unittest
from pathlib import Path


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS / "novel_weather"))

from novel_weather_ab import run_ab  # noqa: E402


class NovelWeatherABTest(unittest.TestCase):
    def test_aether_custom_algorithm_matches_quality_with_less_generation(self) -> None:
        result = run_ab()
        summary = result["summary"]

        self.assertIs(summary["raw_success"], True)
        self.assertIs(summary["aether_success"], True)
        self.assertLessEqual(summary["aether_mae"], summary["raw_mae"] + 0.01)
        self.assertGreater(summary["generated_token_savings_pct"], 50)


if __name__ == "__main__":
    unittest.main()
