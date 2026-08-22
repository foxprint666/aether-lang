from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "benchmarks" / "run_unseen_agent_ab.py"
AGENT = ROOT / "benchmarks" / "agents" / "unseen_smoke_agent.py"
EXPERIMENT_ID = "unseen-agent-smoke-test"
RAW_RESULT = ROOT / "benchmarks" / "results" / "raw" / f"{EXPERIMENT_ID}.json"
CSV_RESULT = ROOT / "benchmarks" / "results" / "processed" / f"{EXPERIMENT_ID}.csv"


class UnseenAgentABTest(unittest.TestCase):
    def test_smoke_agent_ab_records_success_and_savings(self) -> None:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--experiment-id",
                    EXPERIMENT_ID,
                    "--raw-command",
                    sys.executable,
                    str(AGENT),
                    "--aether-command",
                    sys.executable,
                    str(AGENT),
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(RAW_RESULT.read_text(encoding="utf-8"))
            summary = payload["summary"]
            self.assertEqual(summary["by_arm"]["raw_full_file"]["success_rate"], 1.0)
            self.assertEqual(summary["by_arm"]["aether_patch"]["success_rate"], 1.0)
            self.assertGreater(summary["aether_output_token_savings_pct"], 20)
        finally:
            RAW_RESULT.unlink(missing_ok=True)
            CSV_RESULT.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
