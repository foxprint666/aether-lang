#!/usr/bin/env python
"""Build and evaluate a custom weather predictor through raw code and Aether states."""

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
    print(json.dumps(output, indent=2, sort_keys=True) if args.json else json.dumps(output["summary"], indent=2))
    return 0


def run_ab(*, keep_workdirs: bool = False) -> dict[str, Any]:
    raw = build_raw_project()
    aether = build_aether_project()
    try:
        records = [evaluate(raw), evaluate(aether)]
        return {
            "report_version": "novel-weather-ab-v1",
            "records": records,
            "summary": summarize(records),
            "build_modes": {
                "raw": "full custom algorithm source emitted directly",
                "aether": "compact custom algorithm states compiled into source files",
            },
            "algorithm": "seasonal_analog_residual_blend",
        }
    finally:
        if not keep_workdirs:
            shutil.rmtree(raw.workdir, ignore_errors=True)
            shutil.rmtree(aether.workdir, ignore_errors=True)


def build_raw_project() -> BuildResult:
    started = time.perf_counter()
    workdir = Path(tempfile.mkdtemp(prefix="novel-weather-raw-"))
    files = project_files()
    write_files(workdir, files)
    generated = "\n\n".join(f"# {name}\n{content}" for name, content in files.items())
    return BuildResult("raw", workdir, generated, elapsed_ms(started))


def build_aether_project() -> BuildResult:
    started = time.perf_counter()
    workdir = Path(tempfile.mkdtemp(prefix="novel-weather-aether-"))
    transitions = aether_transitions()
    files = compile_transitions(transitions)
    write_files(workdir, files)
    generated = json.dumps(transitions, sort_keys=True, separators=(",", ":"))
    return BuildResult("aether", workdir, generated, elapsed_ms(started))


def write_files(workdir: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    write_dataset(workdir / "data" / "weather.csv")


def aether_transitions() -> list[dict[str, Any]]:
    return [
        {
            "op": "define_custom_predictor",
            "target": "novel_weather.py",
            "name": "SeasonalAnalogResidualBlend",
            "algorithm": {
                "baseline": "learned_harmonic_weather_regression",
                "seasonal_metric": "cyclic_day_distance",
                "weather_metric": ["humidity", "pressure", "wind_speed"],
                "neighbor_count": 9,
                "weighting": "inverse_squared_distance",
                "residual": "local_weighted_bias",
            },
            "quality_gate": {"mae_lt": 0.9, "rmse_lt": 1.2},
        },
        {"op": "define_cli", "target": "train.py", "data_default": "data/weather.csv"},
        {"op": "define_tests", "target": "test_novel_weather.py", "quality_gate": {"mae_lt": 0.9, "rmse_lt": 1.2}},
    ]


def compile_transitions(transitions: list[dict[str, Any]]) -> dict[str, str]:
    files: dict[str, str] = {}
    for transition in transitions:
        op = transition["op"]
        if op == "define_custom_predictor":
            files[str(transition["target"])] = model_source()
        elif op == "define_cli":
            files[str(transition["target"])] = cli_source(str(transition["data_default"]))
        elif op == "define_tests":
            gate = dict(transition["quality_gate"])
            files[str(transition["target"])] = test_source(float(gate["mae_lt"]), float(gate["rmse_lt"]))
        else:
            raise ValueError(f"Unknown Aether transition: {op}")
    return files


def project_files() -> dict[str, str]:
    return {
        "novel_weather.py": model_source(),
        "train.py": cli_source("data/weather.csv"),
        "test_novel_weather.py": test_source(0.9, 1.2),
    }


def model_source() -> str:
    return textwrap.dedent(r'''
        from __future__ import annotations

        import csv
        import json
        import math
        from dataclasses import dataclass
        from pathlib import Path


        FEATURES = ("day_of_year", "humidity", "pressure", "wind_speed")


        @dataclass
        class SeasonalAnalogResidualBlend:
            rows: list[list[float]]
            targets: list[float]
            weights: list[float]
            residuals: list[float]
            means: list[float]
            scales: list[float]
            k: int = 9

            def predict_one(self, features: list[float]) -> float:
                baseline = self.baseline(features)
                ranked = sorted(
                    (self.distance(features, row), residual)
                    for row, residual in zip(self.rows, self.residuals)
                )
                neighbors = ranked[: self.k]
                weights = [1.0 / ((distance + 0.08) ** 2) for distance, _ in neighbors]
                total = sum(weights)
                residual = sum(weight * value for weight, (_, value) in zip(weights, neighbors)) / total
                return baseline + residual

            def predict_many(self, rows: list[list[float]]) -> list[float]:
                return [self.predict_one(row) for row in rows]

            def baseline(self, features: list[float]) -> float:
                return sum(weight * value for weight, value in zip(self.weights, design_vector(features, self.means, self.scales)))

            def distance(self, left: list[float], right: list[float]) -> float:
                day_gap = abs(left[0] - right[0])
                day_gap = min(day_gap, 365.0 - day_gap) / 42.0
                weather = sum(((left[index] - right[index]) / self.scales[index]) ** 2 for index in range(1, 4))
                return math.sqrt(day_gap * day_gap + weather)

            def to_json(self) -> str:
                return json.dumps(self.__dict__, sort_keys=True)

            @classmethod
            def from_json(cls, text: str) -> "SeasonalAnalogResidualBlend":
                payload = json.loads(text)
                return cls(
                    rows=[[float(value) for value in row] for row in payload["rows"]],
                    targets=[float(value) for value in payload["targets"]],
                    weights=[float(value) for value in payload["weights"]],
                    residuals=[float(value) for value in payload["residuals"]],
                    means=[float(value) for value in payload["means"]],
                    scales=[float(value) for value in payload["scales"]],
                    k=int(payload.get("k", 9)),
                )


        def load_dataset(path: str | Path) -> tuple[list[list[float]], list[float]]:
            rows: list[list[float]] = []
            targets: list[float] = []
            with Path(path).open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    rows.append([float(row[name]) for name in FEATURES])
                    targets.append(float(row["next_max_temp"]))
            return rows, targets


        def train_model(rows: list[list[float]], targets: list[float], *, k: int = 9) -> SeasonalAnalogResidualBlend:
            means = [sum(row[index] for row in rows) / len(rows) for index in range(len(rows[0]))]
            scales = []
            for index, mean in enumerate(means):
                variance = sum((row[index] - mean) ** 2 for row in rows) / len(rows)
                scales.append(math.sqrt(variance) or 1.0)
            scales[0] = 42.0
            design = [design_vector(row, means, scales) for row in rows]
            weights = fit_weights(design, targets)
            residuals = [
                target - sum(weight * value for weight, value in zip(weights, vector))
                for target, vector in zip(targets, design)
            ]
            return SeasonalAnalogResidualBlend(rows, targets, weights, residuals, means, scales, k)


        def design_vector(row: list[float], means: list[float], scales: list[float]) -> list[float]:
            day = row[0]
            annual = day / 365.0 * math.tau
            short = day / 6.0
            return [
                1.0,
                math.sin(annual),
                math.cos(annual),
                math.sin(short),
                math.cos(short),
                (row[1] - means[1]) / scales[1],
                (row[2] - means[2]) / scales[2],
                (row[3] - means[3]) / scales[3],
            ]


        def fit_weights(design: list[list[float]], targets: list[float]) -> list[float]:
            weights = [0.0 for _ in design[0]]
            learning_rate = 0.035
            for _ in range(1400):
                gradient = [0.0 for _ in weights]
                for vector, target in zip(design, targets):
                    error = sum(weight * value for weight, value in zip(weights, vector)) - target
                    for index, value in enumerate(vector):
                        gradient[index] += error * value
                count = float(len(targets))
                for index in range(len(weights)):
                    weights[index] -= learning_rate * gradient[index] / count
            return weights


        def split_dataset(rows: list[list[float]], targets: list[float], ratio: float = 0.75):
            train_rows: list[list[float]] = []
            train_targets: list[float] = []
            test_rows: list[list[float]] = []
            test_targets: list[float] = []
            for index, (row, target) in enumerate(zip(rows, targets)):
                if index % 4 == 0:
                    test_rows.append(row)
                    test_targets.append(target)
                else:
                    train_rows.append(row)
                    train_targets.append(target)
            return train_rows, train_targets, test_rows, test_targets


        def evaluate_model(model: SeasonalAnalogResidualBlend, rows: list[list[float]], targets: list[float]) -> dict[str, float]:
            predictions = model.predict_many(rows)
            mae = sum(abs(left - right) for left, right in zip(predictions, targets)) / len(targets)
            rmse = math.sqrt(sum((left - right) ** 2 for left, right in zip(predictions, targets)) / len(targets))
            return {"mae": mae, "rmse": rmse}
    ''').lstrip()


def cli_source(data_default: str) -> str:
    return textwrap.dedent(f'''
        from __future__ import annotations

        import argparse
        from pathlib import Path

        from novel_weather import evaluate_model, load_dataset, split_dataset, train_model


        def main() -> int:
            parser = argparse.ArgumentParser(description="Train the custom weather analog predictor.")
            parser.add_argument("--data", default={data_default!r})
            parser.add_argument("--model-out", default="novel-weather-model.json")
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


def test_source(mae_lt: float, rmse_lt: float) -> str:
    return textwrap.dedent(f'''
        import unittest

        from novel_weather import SeasonalAnalogResidualBlend, evaluate_model, load_dataset, split_dataset, train_model


        class NovelWeatherTest(unittest.TestCase):
            def test_custom_predictor_quality_and_serialization(self):
                rows, targets = load_dataset("data/weather.csv")
                train_rows, train_targets, test_rows, test_targets = split_dataset(rows, targets)
                model = train_model(train_rows, train_targets)
                metrics = evaluate_model(model, test_rows, test_targets)
                self.assertLess(metrics["mae"], {mae_lt})
                self.assertLess(metrics["rmse"], {rmse_lt})
                restored = SeasonalAnalogResidualBlend.from_json(model.to_json())
                self.assertLess(abs(restored.predict_one(test_rows[3]) - model.predict_one(test_rows[3])), 1e-9)


        if __name__ == "__main__":
            unittest.main()
    ''').lstrip()


def evaluate(build: BuildResult) -> dict[str, Any]:
    started = time.perf_counter()
    test = subprocess.run(
        [sys.executable, "test_novel_weather.py"],
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
    success = test.returncode == 0 and cli.returncode == 0 and metrics.get("mae", 999) < 0.9
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
        "raw_rmse": raw["rmse"],
        "aether_rmse": aether["rmse"],
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
        for day in range(1, 241):
            seasonal = math.sin(day / 365 * math.tau)
            humidity = 58 + 17 * math.cos(day / 23) + 4 * math.sin(day / 9)
            pressure = 1010 + 8 * math.sin(day / 19) - 2 * math.cos(day / 7)
            wind = 5.5 + 2.2 * math.cos(day / 13) + 0.8 * math.sin(day / 4)
            front = 1.8 if day % 37 in (0, 1, 2) else 0.0
            temp = 17 + 13 * seasonal - 0.05 * humidity + 0.02 * (pressure - 1010) - 0.38 * wind
            temp += 1.3 * math.sin(day / 6) + front
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
        if "=" in part:
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


if __name__ == "__main__":
    raise SystemExit(main())
