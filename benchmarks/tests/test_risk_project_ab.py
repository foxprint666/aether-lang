from __future__ import annotations

import sys
import unittest
from pathlib import Path


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS / "risk_scoring"))

from risk_project_ab import run_ab  # noqa: E402


class RiskProjectABTest(unittest.TestCase):
    def test_aether_risk_build_matches_quality_with_less_generation(self) -> None:
        result = run_ab()
        summary = result["summary"]

        self.assertIs(summary["raw_success"], True)
        self.assertIs(summary["aether_success"], True)
        self.assertEqual(summary["aether_accuracy"], summary["raw_accuracy"])
        self.assertEqual(summary["aether_recall"], summary["raw_recall"])
        self.assertGreater(summary["generated_token_savings_pct"], 70)


if __name__ == "__main__":
    unittest.main()
