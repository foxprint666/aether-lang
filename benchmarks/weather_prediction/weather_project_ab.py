#!/usr/bin/env python
"""Build and evaluate a weather predictor through raw code and Aether states."""

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


ROOT = Path(__file__).resolve().parents[2]


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
        summary = summarize(records)
        return {
            "report_version": "weather-project-ab-v1",
            "records": records,
            "summary": summary,
            "build_modes": {
                "raw": "full source files emitted directly",
                "aether": "compact state transitions compiled into source files",
            },
        }
    finally:
        if not keep_workdirs:
            shutil.rmtree(raw.workdir, ignore_errors=True)
            shutil.rmtree(aether.workdir, ignore_errors=True)


def build_raw_project() -> BuildResult:
    started = time.perf_counter()
    workdir = Path(tempfile.mkdtemp(prefix="weather-raw-"))
    files = raw_files()
    for name, content in files.items():
        path = workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    write_dataset(workdir / "data" / "weather.csv")
    generated = "\n\n".join(f"# {name}\n{content}" for name, content in files.items())
    return BuildResult("raw", workdir, generated, elapsed_ms(started))


def build_aether_project() -> BuildResult:
    started = time.perf_counter()
    workdir = Path(tempfile.mkdtemp(prefix="weather-aether-"))
    transitions = aether_transitions()
    project = compile_transitions(transitions)
    for name, content in project.items():
        path = workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    write_dataset(workdir / "data" / "weather.csv")
    generated = json.dumps(transitions, sort_keys=True, separators=(",", ":"))
    return BuildResult("aether", workdir, generated, elapsed_ms(started))


def raw_files() -> dict[str, str]:
    model = r'''
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WeatherModel:
    intercept: float
    coefficients: list[float]
    feature_means: list[float]
    feature_scales: list[float]

    def predict_one(self, features: list[float]) -> float:
        scaled = [
            (value - mean) / scale
            for value, mean, scale in zip(features, self.feature_means, self.feature_scales)
        ]
        return self.intercept + sum(weight * value for weight, value in zip(self.coefficients, scaled))

    def predict_many(self, rows: list[list[float]]) -> list[float]:
        return [self.predict_one(row) for row in rows]

    def to_json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "WeatherModel":
        payload = json.loads(text)
        return cls(
            intercept=float(payload["intercept"]),
            coefficients=[float(item) for item in payload["coefficients"]],
            feature_means=[float(item) for item in payload["feature_means"]],
            feature_scales=[float(item) for item in payload["feature_scales"]],
        )


def load_dataset(path: str | Path) -> tuple[list[list[float]], list[float]]:
    rows: list[list[float]] = []
    targets: list[float] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append([
                float(row["day_of_year"]),
                float(row["humidity"]),
                float(row["pressure"]),
                float(row["wind_speed"]),
            ])
            targets.append(float(row["next_max_temp"]))
    return rows, targets


def train_model(rows: list[list[float]], targets: list[float], *, steps: int = 900, learning_rate: float = 0.045) -> WeatherModel:
    means = column_means(rows)
    scales = column_scales(rows, means)
    scaled = [[(value - means[index]) / scales[index] for index, value in enumerate(row)] for row in rows]
    weights = [0.0 for _ in scaled[0]]
    intercept = sum(targets) / len(targets)
    for _ in range(steps):
        grad_w = [0.0 for _ in weights]
        grad_b = 0.0
        for row, target in zip(scaled, targets):
            error = intercept + sum(weight * value for weight, value in zip(weights, row)) - target
            grad_b += error
            for index, value in enumerate(row):
                grad_w[index] += error * value
        count = float(len(targets))
        intercept -= learning_rate * grad_b / count
        for index in range(len(weights)):
            weights[index] -= learning_rate * grad_w[index] / count
    return WeatherModel(intercept, weights, means, scales)


def evaluate_model(model: WeatherModel, rows: list[list[float]], targets: list[float]) -> dict[str, float]:
    predictions = model.predict_many(rows)
    mae = sum(abs(left - right) for left, right in zip(predictions, targets)) / len(targets)
    rmse = math.sqrt(sum((left - right) ** 2 for left, right in zip(predictions, targets)) / len(targets))
    return {"mae": mae, "rmse": rmse}


def split_dataset(rows: list[list[float]], targets: list[float], ratio: float = 0.75):
    split = int(len(rows) * ratio)
    return rows[:split], targets[:split], rows[split:], targets[split:]


def column_means(rows: list[list[float]]) -> list[float]:
    return [sum(row[index] for row in rows) / len(rows) for index in range(len(rows[0]))]


def column_scales(rows: list[list[float]], means: list[float]) -> list[float]:
    scales = []
    for index, mean in enumerate(means):
        variance = sum((row[index] - mean) ** 2 for row in rows) / len(rows)
        scales.append(math.sqrt(variance) or 1.0)
    return scales
'''.lstrip()
    cli = r'''
from __future__ import annotations

import argparse
from pathlib import Path

from weather_model import evaluate_model, load_dataset, split_dataset, train_model


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a local weather prediction model.")
    parser.add_argument("--data", default="data/weather.csv")
    parser.add_argument("--model-out", default="weather-model.json")
    args = parser.parse_args()
    rows, targets = load_dataset(args.data)
    train_rows, train_targets, test_rows, test_targets = split_dataset(rows, targets)
    model = train_model(train_rows, train_targets)
    metrics = evaluate_model(model, test_rows, test_targets)
    Path(args.model_out).write_text(model.to_json() + "\n", encoding="utf-8")
    print(f"mae={metrics['mae']:.4f} rmse={metrics['rmse']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.lstrip()
    tests = r'''
import unittest

from weather_model import evaluate_model, load_dataset, split_dataset, train_model, WeatherModel


class WeatherModelTest(unittest.TestCase):
    def test_weather_model_quality(self):
        rows, targets = load_dataset("data/weather.csv")
        train_rows, train_targets, test_rows, test_targets = split_dataset(rows, targets)
        model = train_model(train_rows, train_targets)
        metrics = evaluate_model(model, test_rows, test_targets)
        self.assertLess(metrics["mae"], 2.1)
        self.assertLess(metrics["rmse"], 2.7)
        restored = WeatherModel.from_json(model.to_json())
        self.assertLess(abs(restored.predict_one(test_rows[0]) - model.predict_one(test_rows[0])), 1e-9)


if __name__ == "__main__":
    unittest.main()
'''.lstrip()
    return {"weather_model.py": model, "train.py": cli, "test_weather_model.py": tests}


def aether_transitions() -> list[dict[str, Any]]:
    return [
        {
            "op": "define_model",
            "target": "weather_model.py",
            "name": "WeatherModel",
            "features": ["day_of_year", "humidity", "pressure", "wind_speed"],
            "target_column": "next_max_temp",
            "algorithm": "standardized_gradient_descent_linear_regression",
            "quality_gate": {"mae_lt": 2.1, "rmse_lt": 2.7},
        },
        {
            "op": "define_cli",
            "target": "train.py",
            "data_default": "data/weather.csv",
            "model_out_default": "weather-model.json",
        },
        {
            "op": "define_tests",
            "target": "test_weather_model.py",
            "quality_gate": {"mae_lt": 2.1, "rmse_lt": 2.7},
            "roundtrip_serialization": True,
        },
    ]


def compile_transitions(transitions: list[dict[str, Any]]) -> dict[str, str]:
    files: dict[str, str] = {}
    for transition in transitions:
        op = transition["op"]
        if op == "define_model":
            files[str(transition["target"])] = compile_model_module(transition)
        elif op == "define_cli":
            files[str(transition["target"])] = compile_cli(transition)
        elif op == "define_tests":
            files[str(transition["target"])] = compile_tests(transition)
        else:
            raise ValueError(f"Unknown Aether transition: {op}")
    return files


def compile_model_module(spec: dict[str, Any]) -> str:
    features = list(spec["features"])
    target = str(spec["target_column"])
    return textwrap.dedent(f'''
        from __future__ import annotations

        import csv
        import json
        import math
        from dataclasses import dataclass
        from pathlib import Path


        FEATURES = {features!r}
        TARGET = {target!r}


        @dataclass
        class WeatherModel:
            intercept: float
            coefficients: list[float]
            feature_means: list[float]
            feature_scales: list[float]

            def predict_one(self, features: list[float]) -> float:
                scaled = [
                    (value - mean) / scale
                    for value, mean, scale in zip(features, self.feature_means, self.feature_scales)
                ]
                return self.intercept + sum(weight * value for weight, value in zip(self.coefficients, scaled))

            def predict_many(self, rows: list[list[float]]) -> list[float]:
                return [self.predict_one(row) for row in rows]

            def to_json(self) -> str:
                return json.dumps(self.__dict__, sort_keys=True)

            @classmethod
            def from_json(cls, text: str) -> "WeatherModel":
                payload = json.loads(text)
                return cls(
                    intercept=float(payload["intercept"]),
                    coefficients=[float(item) for item in payload["coefficients"]],
                    feature_means=[float(item) for item in payload["feature_means"]],
                    feature_scales=[float(item) for item in payload["feature_scales"]],
                )


        def load_dataset(path: str | Path) -> tuple[list[list[float]], list[float]]:
            rows: list[list[float]] = []
            targets: list[float] = []
            with Path(path).open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    rows.append([float(row[name]) for name in FEATURES])
                    targets.append(float(row[TARGET]))
            return rows, targets


        def train_model(rows: list[list[float]], targets: list[float], *, steps: int = 900, learning_rate: float = 0.045) -> WeatherModel:
            means = column_means(rows)
            scales = column_scales(rows, means)
            scaled = [[(value - means[index]) / scales[index] for index, value in enumerate(row)] for row in rows]
            weights = [0.0 for _ in scaled[0]]
            intercept = sum(targets) / len(targets)
            for _ in range(steps):
                grad_w = [0.0 for _ in weights]
                grad_b = 0.0
                for row, target in zip(scaled, targets):
                    error = intercept + sum(weight * value for weight, value in zip(weights, row)) - target
                    grad_b += error
                    for index, value in enumerate(row):
                        grad_w[index] += error * value
                count = float(len(targets))
                intercept -= learning_rate * grad_b / count
                for index in range(len(weights)):
                    weights[index] -= learning_rate * grad_w[index] / count
            return WeatherModel(intercept, weights, means, scales)


        def evaluate_model(model: WeatherModel, rows: list[list[float]], targets: list[float]) -> dict[str, float]:
            predictions = model.predict_many(rows)
            mae = sum(abs(left - right) for left, right in zip(predictions, targets)) / len(targets)
            rmse = math.sqrt(sum((left - right) ** 2 for left, right in zip(predictions, targets)) / len(targets))
            return {{"mae": mae, "rmse": rmse}}


        def split_dataset(rows: list[list[float]], targets: list[float], ratio: float = 0.75):
            split = int(len(rows) * ratio)
            return rows[:split], targets[:split], rows[split:], targets[split:]


        def column_means(rows: list[list[float]]) -> list[float]:
            return [sum(row[index] for row in rows) / len(rows) for index in range(len(rows[0]))]


        def column_scales(rows: list[list[float]], means: list[float]) -> list[float]:
            scales = []
            for index, mean in enumerate(means):
                variance = sum((row[index] - mean) ** 2 for row in rows) / len(rows)
                scales.append(math.sqrt(variance) or 1.0)
            return scales
    ''').lstrip()


def compile_cli(spec: dict[str, Any]) -> str:
    return textwrap.dedent(f'''
        from __future__ import annotations

        import argparse
        from pathlib import Path

        from weather_model import evaluate_model, load_dataset, split_dataset, train_model


        def main() -> int:
            parser = argparse.ArgumentParser(description="Train a local weather prediction model.")
            parser.add_argument("--data", default={spec["data_default"]!r})
            parser.add_argument("--model-out", default={spec["model_out_default"]!r})
            args = parser.parse_args()
            rows, targets = load_dataset(args.data)
            train_rows, train_targets, test_rows, test_targets = split_dataset(rows, targets)
            model = train_model(train_rows, train_targets)
            metrics = evaluate_model(model, test_rows, test_targets)
            Path(args.model_out).write_text(model.to_json() + "\\n", encoding="utf-8")
            print(f"mae={{metrics['mae']:.4f}} rmse={{metrics['rmse']:.4f}}")
            return 0


        if __name__ == "__main__":
            raise SystemExit(main())
    ''').lstrip()


def compile_tests(spec: dict[str, Any]) -> str:
    gate = dict(spec["quality_gate"])
    return textwrap.dedent(f'''
        import unittest

        from weather_model import evaluate_model, load_dataset, split_dataset, train_model, WeatherModel


        class WeatherModelTest(unittest.TestCase):
            def test_weather_model_quality(self):
                rows, targets = load_dataset("data/weather.csv")
                train_rows, train_targets, test_rows, test_targets = split_dataset(rows, targets)
                model = train_model(train_rows, train_targets)
                metrics = evaluate_model(model, test_rows, test_targets)
                self.assertLess(metrics["mae"], {float(gate["mae_lt"])})
                self.assertLess(metrics["rmse"], {float(gate["rmse_lt"])})
                restored = WeatherModel.from_json(model.to_json())
                self.assertLess(abs(restored.predict_one(test_rows[0]) - model.predict_one(test_rows[0])), 1e-9)


        if __name__ == "__main__":
            unittest.main()
    ''').lstrip()


def evaluate(build: BuildResult) -> dict[str, Any]:
    started = time.perf_counter()
    test = subprocess.run(
        [sys.executable, "test_weather_model.py"],
        cwd=str(build.workdir),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    cli = subprocess.run(
        [sys.executable, "train.py"],
        cwd=str(build.workdir),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    metrics = parse_cli_metrics(cli.stdout)
    success = test.returncode == 0 and cli.returncode == 0 and metrics.get("mae", 999) < 2.1
    return {
        "mode": build.mode,
        "success": success,
        "mae": metrics.get("mae"),
        "rmse": metrics.get("rmse"),
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
        "raw_mae": raw["mae"],
        "aether_mae": aether["mae"],
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
            fieldnames=["day_of_year", "humidity", "pressure", "wind_speed", "next_max_temp"],
        )
        writer.writeheader()
        for day in range(1, 181):
            seasonal = math.sin(day / 365 * math.tau)
            humidity = 58 + 18 * math.cos(day / 29) + (day % 7 - 3) * 0.8
            pressure = 1012 + 7 * math.sin(day / 17)
            wind = 6 + 2.5 * math.cos(day / 11)
            temp = 18 + 12 * seasonal - 0.055 * humidity + 0.018 * (pressure - 1012) - 0.42 * wind
            temp += math.sin(day / 5) * 0.35
            writer.writerow({
                "day_of_year": day,
                "humidity": round(humidity, 4),
                "pressure": round(pressure, 4),
                "wind_speed": round(wind, 4),
                "next_max_temp": round(temp, 4),
            })


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
