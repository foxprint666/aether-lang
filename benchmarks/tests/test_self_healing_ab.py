from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "benchmarks" / "run_self_healing_ab.py"
ANALYSIS = ROOT / "benchmarks" / "analysis" / "self_healing_ab_evidence.py"
EXPERIMENT_ID = "self-healing-ab-test"
RAW_RESULT = ROOT / "benchmarks" / "results" / "raw" / f"{EXPERIMENT_ID}.json"
CSV_RESULT = ROOT / "benchmarks" / "results" / "processed" / f"{EXPERIMENT_ID}.csv"
PUBLIC_JSON = ROOT / "benchmarks" / "results" / "public" / f"{EXPERIMENT_ID}.json"


class SelfHealingABTest(unittest.TestCase):
    def test_self_healing_loop_records_repair_safety_and_savings(self) -> None:
        try:
            result = subprocess.run(
                [sys.executable, str(RUNNER), "--experiment-id", EXPERIMENT_ID],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(RAW_RESULT.read_text(encoding="utf-8"))
            summary = payload["summary"]
            self.assertEqual(summary["by_arm"]["raw_healing"]["repair_success_rate"], 1.0)
            self.assertEqual(summary["by_arm"]["aether_healing"]["repair_success_rate"], 1.0)
            self.assertEqual(summary["by_arm"]["aether_healing"]["safety_success_rate"], 1.0)
            self.assertGreater(summary["aether_output_token_savings_pct"], 20)

            analyzed = subprocess.run(
                [
                    sys.executable,
                    str(ANALYSIS),
                    str(RAW_RESULT),
                    "--json-output",
                    str(PUBLIC_JSON),
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(analyzed.returncode, 0, analyzed.stderr + analyzed.stdout)
            report = json.loads(PUBLIC_JSON.read_text(encoding="utf-8"))
            self.assertIs(report["self_healing_gate"]["passed"], True)
        finally:
            RAW_RESULT.unlink(missing_ok=True)
            CSV_RESULT.unlink(missing_ok=True)
            PUBLIC_JSON.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
