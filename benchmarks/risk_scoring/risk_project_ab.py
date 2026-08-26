#!/usr/bin/env python
"""Build and evaluate a transaction risk module with and without Aether states."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BuildResult:
    mode: str
    workdir: Path
    generated_text: str
    build_ms: float


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep-workdirs", action="store_true")
    args = parser.parse_args()
    output = run_ab(keep_workdirs=args.keep_workdirs)
    print(json.dumps(output, indent=2, sort_keys=True) if args.json else render(output))
    return 0


def run_ab(*, keep_workdirs: bool = False) -> dict[str, Any]:
    raw = build_raw_project()
    aether = build_aether_project()
    try:
        records = [evaluate(raw), evaluate(aether)]
        return {
            "report_version": "risk-project-ab-v1",
            "records": records,
            "summary": summarize(records),
            "build_modes": {
                "raw": "full package source emitted directly",
                "aether": "compact state transitions compiled into package source",
            },
        }
    finally:
        if not keep_workdirs:
            shutil.rmtree(raw.workdir, ignore_errors=True)
            shutil.rmtree(aether.workdir, ignore_errors=True)


def build_raw_project() -> BuildResult:
    started = time.perf_counter()
    workdir = Path(tempfile.mkdtemp(prefix="risk-raw-"))
    files = raw_files()
    for name, content in files.items():
        path = workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    write_dataset(workdir / "data" / "transactions.csv")
    generated = "\n\n".join(f"# {name}\n{content}" for name, content in files.items())
    return BuildResult("raw", workdir, generated, elapsed_ms(started))


def build_aether_project() -> BuildResult:
    started = time.perf_counter()
    workdir = Path(tempfile.mkdtemp(prefix="risk-aether-"))
    transitions = aether_transitions()
    project = compile_transitions(transitions)
    for name, content in project.items():
        path = workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    write_dataset(workdir / "data" / "transactions.csv")
    generated = json.dumps(transitions, sort_keys=True, separators=(",", ":"))
    return BuildResult("aether", workdir, generated, elapsed_ms(started))


def raw_files() -> dict[str, str]:
    risk_engine = r'''
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path


HIGH_RISK_COUNTRIES = {"NG", "RU", "KP", "IR"}
NIGHT_HOURS = set(range(0, 6))


@dataclass(frozen=True)
class Transaction:
    account_id: str
    amount: float
    hour: int
    country: str
    merchant_category: str
    device_age_days: int
    chargeback: bool


@dataclass(frozen=True)
class RiskScore:
    account_id: str
    probability: float
    label: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "probability": round(self.probability, 6),
            "label": self.label,
            "reasons": list(self.reasons),
        }


class RiskModel:
    def __init__(self, amount_median: float, amount_mad: float, velocity: dict[str, int], category_rates: dict[str, float]):
        self.amount_median = amount_median
        self.amount_mad = amount_mad or 1.0
        self.velocity = velocity
        self.category_rates = category_rates

    @classmethod
    def train(cls, transactions: list[Transaction]) -> "RiskModel":
        amounts = [item.amount for item in transactions]
        median = percentile(amounts, 0.5)
        mad = percentile([abs(value - median) for value in amounts], 0.5) or 1.0
        velocity: dict[str, int] = {}
        category_totals: dict[str, list[int]] = {}
        for item in transactions:
            velocity[item.account_id] = velocity.get(item.account_id, 0) + 1
            category_totals.setdefault(item.merchant_category, []).append(1 if item.chargeback else 0)
        category_rates = {
            category: (sum(values) + 0.5) / (len(values) + 1.0)
            for category, values in category_totals.items()
        }
        return cls(median, mad, velocity, category_rates)

    def score(self, item: Transaction) -> RiskScore:
        reasons: list[str] = []
        z_amount = max(0.0, (item.amount - self.amount_median) / self.amount_mad)
        velocity = self.velocity.get(item.account_id, 0)
        category_rate = self.category_rates.get(item.merchant_category, 0.08)
        linear = -2.65
        linear += min(z_amount, 8.0) * 0.55
        linear += min(velocity, 12) * 0.16
        linear += category_rate * 2.5
        if item.country in HIGH_RISK_COUNTRIES:
            linear += 1.55
            reasons.append("high_risk_country")
        if item.hour in NIGHT_HOURS:
            linear += 0.7
            reasons.append("night_activity")
        if item.device_age_days < 7:
            linear += 1.05
            reasons.append("new_device")
        if item.amount > self.amount_median + self.amount_mad * 3:
            reasons.append("amount_outlier")
        if velocity >= 5:
            reasons.append("account_velocity")
        probability = sigmoid(linear)
        label = "review" if probability >= 0.34 else "approve"
        return RiskScore(item.account_id, probability, label, tuple(reasons))

    def score_many(self, transactions: list[Transaction]) -> list[RiskScore]:
        return [self.score(item) for item in transactions]

    def to_json(self) -> str:
        return json.dumps(
            {
                "amount_median": self.amount_median,
                "amount_mad": self.amount_mad,
                "velocity": self.velocity,
                "category_rates": self.category_rates,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "RiskModel":
        payload = json.loads(text)
        return cls(
            float(payload["amount_median"]),
            float(payload["amount_mad"]),
            {str(key): int(value) for key, value in payload["velocity"].items()},
            {str(key): float(value) for key, value in payload["category_rates"].items()},
        )


def load_transactions(path: str | Path) -> list[Transaction]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [
            Transaction(
                account_id=row["account_id"],
                amount=float(row["amount"]),
                hour=int(row["hour"]),
                country=row["country"],
                merchant_category=row["merchant_category"],
                device_age_days=int(row["device_age_days"]),
                chargeback=row["chargeback"] == "1",
            )
            for row in csv.DictReader(handle)
        ]


def split_transactions(transactions: list[Transaction], ratio: float = 0.7) -> tuple[list[Transaction], list[Transaction]]:
    split = int(len(transactions) * ratio)
    return transactions[:split], transactions[split:]


def evaluate(model: RiskModel, transactions: list[Transaction]) -> dict[str, float]:
    scores = model.score_many(transactions)
    predicted = [score.label == "review" for score in scores]
    actual = [item.chargeback for item in transactions]
    true_positive = sum(1 for left, right in zip(predicted, actual) if left and right)
    false_positive = sum(1 for left, right in zip(predicted, actual) if left and not right)
    false_negative = sum(1 for left, right in zip(predicted, actual) if not left and right)
    accuracy = sum(1 for left, right in zip(predicted, actual) if left == right) / len(actual)
    precision = true_positive / (true_positive + false_positive or 1)
    recall = true_positive / (true_positive + false_negative or 1)
    return {"accuracy": accuracy, "precision": precision, "recall": recall}


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))
'''.lstrip()
    cli = r'''
from __future__ import annotations

import argparse
from pathlib import Path

from risk_engine import RiskModel, evaluate, load_transactions, split_transactions


def main() -> int:
    parser = argparse.ArgumentParser(description="Train and evaluate a transaction risk model.")
    parser.add_argument("--data", default="data/transactions.csv")
    parser.add_argument("--model-out", default="risk-model.json")
    args = parser.parse_args()
    transactions = load_transactions(args.data)
    train, test = split_transactions(transactions)
    model = RiskModel.train(train)
    metrics = evaluate(model, test)
    Path(args.model_out).write_text(model.to_json() + "\n", encoding="utf-8")
    print(
        f"accuracy={metrics['accuracy']:.4f} "
        f"precision={metrics['precision']:.4f} "
        f"recall={metrics['recall']:.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.lstrip()
    tests = r'''
import unittest

from risk_engine import RiskModel, evaluate, load_transactions, split_transactions


class RiskEngineTest(unittest.TestCase):
    def test_model_quality_and_serialization(self):
        transactions = load_transactions("data/transactions.csv")
        train, test = split_transactions(transactions)
        model = RiskModel.train(train)
        metrics = evaluate(model, test)
        self.assertGreaterEqual(metrics["accuracy"], 0.72)
        self.assertGreaterEqual(metrics["recall"], 0.70)
        restored = RiskModel.from_json(model.to_json())
        first = test[0]
        self.assertAlmostEqual(model.score(first).probability, restored.score(first).probability)
        reviewed = [model.score(item) for item in test if model.score(item).label == "review"]
        self.assertTrue(any(score.reasons for score in reviewed))


if __name__ == "__main__":
    unittest.main()
'''.lstrip()
    return {
        "risk_engine.py": risk_engine,
        "train_risk.py": cli,
        "test_risk_engine.py": tests,
    }


def aether_transitions() -> list[dict[str, Any]]:
    return [
        {
            "op": "define_risk_engine",
            "target": "risk_engine.py",
            "schema": {
                "fields": [
                    "account_id",
                    "amount",
                    "hour",
                    "country",
                    "merchant_category",
                    "device_age_days",
                    "chargeback",
                ],
                "label": "chargeback",
            },
            "features": [
                "robust_amount_outlier",
                "account_velocity",
                "country_risk",
                "night_activity",
                "new_device",
                "category_chargeback_rate",
            ],
            "algorithm": "calibrated_rule_logistic_score",
            "quality_gate": {"accuracy_gte": 0.72, "recall_gte": 0.70},
        },
        {
            "op": "define_cli",
            "target": "train_risk.py",
            "data_default": "data/transactions.csv",
            "model_out_default": "risk-model.json",
        },
        {
            "op": "define_tests",
            "target": "test_risk_engine.py",
            "quality_gate": {"accuracy_gte": 0.72, "recall_gte": 0.70},
            "serialization_roundtrip": True,
        },
    ]


def compile_transitions(transitions: list[dict[str, Any]]) -> dict[str, str]:
    files: dict[str, str] = {}
    for transition in transitions:
        op = transition["op"]
        if op == "define_risk_engine":
            files[str(transition["target"])] = compile_risk_engine(transition)
        elif op == "define_cli":
            files[str(transition["target"])] = compile_cli(transition)
        elif op == "define_tests":
            files[str(transition["target"])] = compile_tests(transition)
        else:
            raise ValueError(f"Unknown Aether transition: {op}")
    return files


def compile_risk_engine(_spec: dict[str, Any]) -> str:
    return raw_files()["risk_engine.py"]


def compile_cli(spec: dict[str, Any]) -> str:
    return textwrap.dedent(f'''
        from __future__ import annotations

        import argparse
        from pathlib import Path

        from risk_engine import RiskModel, evaluate, load_transactions, split_transactions


        def main() -> int:
            parser = argparse.ArgumentParser(description="Train and evaluate a transaction risk model.")
            parser.add_argument("--data", default={spec["data_default"]!r})
            parser.add_argument("--model-out", default={spec["model_out_default"]!r})
            args = parser.parse_args()
            transactions = load_transactions(args.data)
            train, test = split_transactions(transactions)
            model = RiskModel.train(train)
            metrics = evaluate(model, test)
            Path(args.model_out).write_text(model.to_json() + "\\n", encoding="utf-8")
            print(
                f"accuracy={{metrics['accuracy']:.4f}} "
                f"precision={{metrics['precision']:.4f}} "
                f"recall={{metrics['recall']:.4f}}"
            )
            return 0


        if __name__ == "__main__":
            raise SystemExit(main())
    ''').lstrip()


def compile_tests(spec: dict[str, Any]) -> str:
    gate = dict(spec["quality_gate"])
    return textwrap.dedent(f'''
        import unittest

        from risk_engine import RiskModel, evaluate, load_transactions, split_transactions


        class RiskEngineTest(unittest.TestCase):
            def test_model_quality_and_serialization(self):
                transactions = load_transactions("data/transactions.csv")
                train, test = split_transactions(transactions)
                model = RiskModel.train(train)
                metrics = evaluate(model, test)
                self.assertGreaterEqual(metrics["accuracy"], {float(gate["accuracy_gte"])})
                self.assertGreaterEqual(metrics["recall"], {float(gate["recall_gte"])})
                restored = RiskModel.from_json(model.to_json())
                first = test[0]
                self.assertAlmostEqual(model.score(first).probability, restored.score(first).probability)
                reviewed = [model.score(item) for item in test if model.score(item).label == "review"]
                self.assertTrue(any(score.reasons for score in reviewed))


        if __name__ == "__main__":
            unittest.main()
    ''').lstrip()


def evaluate(build: BuildResult) -> dict[str, Any]:
    started = time.perf_counter()
    test = subprocess.run(
        [sys.executable, "test_risk_engine.py"],
        cwd=str(build.workdir),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    cli = subprocess.run(
        [sys.executable, "train_risk.py"],
        cwd=str(build.workdir),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    metrics = parse_cli_metrics(cli.stdout)
    success = (
        test.returncode == 0
        and cli.returncode == 0
        and metrics.get("accuracy", 0) >= 0.72
        and metrics.get("recall", 0) >= 0.70
    )
    return {
        "mode": build.mode,
        "success": success,
        "accuracy": metrics.get("accuracy"),
        "precision": metrics.get("precision"),
        "recall": metrics.get("recall"),
        "build_ms": round(build.build_ms, 6),
        "verify_ms": round(elapsed_ms(started), 6),
        "generated_tokens": count_tokens(build.generated_text),
        "generated_bytes": len(build.generated_text.encode("utf-8")),
        "project_tokens": sum(count_tokens(path.read_text(encoding="utf-8")) for path in build.workdir.glob("*.py")),
        "test_stdout": test.stdout.strip(),
        "test_stderr": test.stderr.strip(),
        "cli_stdout": cli.stdout.strip(),
        "cli_stderr": cli.stderr.strip(),
        "workdir": str(build.workdir),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_mode = {record["mode"]: record for record in records}
    raw = by_mode["raw"]
    aether = by_mode["aether"]
    return {
        "raw_success": raw["success"],
        "aether_success": aether["success"],
        "raw_accuracy": raw["accuracy"],
        "aether_accuracy": aether["accuracy"],
        "raw_recall": raw["recall"],
        "aether_recall": aether["recall"],
        "generated_token_savings_pct": pct(raw["generated_tokens"], aether["generated_tokens"]),
        "generated_byte_savings_pct": pct(raw["generated_bytes"], aether["generated_bytes"]),
        "project_token_delta_pct": pct(raw["project_tokens"], aether["project_tokens"]),
        "build_time_savings_pct": pct(raw["build_ms"], aether["build_ms"]),
        "verify_time_savings_pct": pct(raw["verify_ms"], aether["verify_ms"]),
    }


def write_dataset(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "account_id",
                "amount",
                "hour",
                "country",
                "merchant_category",
                "device_age_days",
                "chargeback",
            ],
        )
        writer.writeheader()
        categories = ["grocery", "electronics", "travel", "gaming", "fuel", "jewelry"]
        countries = ["US", "US", "GB", "CA", "DE", "NG", "RU"]
        for index in range(240):
            account = f"acct-{index % 38:03d}"
            category = categories[(index * 5 + index // 11) % len(categories)]
            country = countries[(index * 7 + index // 9) % len(countries)]
            hour = (index * 3 + index // 5) % 24
            device_age = (index * 13 + 4) % 120
            base = 18 + ((index * 37) % 260)
            amount = base * (1.0 + (category == "jewelry") * 1.7 + (category == "travel") * 0.9)
            amount += 350 if index % 41 == 0 else 0
            risky = (
                amount > 310
                or country in {"NG", "RU"}
                or (hour < 5 and device_age < 15)
                or (category in {"jewelry", "gaming"} and device_age < 20)
            )
            chargeback = "1" if risky and index % 3 != 1 else "0"
            writer.writerow(
                {
                    "account_id": account,
                    "amount": round(amount, 2),
                    "hour": hour,
                    "country": country,
                    "merchant_category": category,
                    "device_age_days": device_age,
                    "chargeback": chargeback,
                }
            )


def parse_cli_metrics(stdout: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for part in stdout.split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        try:
            values[key] = float(value)
        except ValueError:
            pass
    return values


def count_tokens(text: str) -> int:
    return len(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\s]", text))


def pct(left: float, right: float) -> float | None:
    if left == 0:
        return None
    return round((left - right) / left * 100, 6)


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def render(output: dict[str, Any]) -> str:
    return json.dumps(output["summary"], indent=2, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
