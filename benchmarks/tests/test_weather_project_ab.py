from __future__ import annotations

import sys
import unittest
from pathlib import Path


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS / "weather_prediction"))

from weather_project_ab import run_ab  # noqa: E402


class WeatherProjectABTest(unittest.TestCase):
    def test_aether_weather_build_matches_quality_with_less_generation(self) -> None:
        result = run_ab()
        summary = result["summary"]

        self.assertIs(summary["raw_success"], True)
        self.assertIs(summary["aether_success"], True)
        self.assertLessEqual(summary["aether_mae"], summary["raw_mae"] + 0.05)
        self.assertGreater(summary["generated_token_savings_pct"], 50)


if __name__ == "__main__":
    unittest.main()
