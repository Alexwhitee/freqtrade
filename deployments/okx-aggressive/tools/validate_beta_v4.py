#!/usr/bin/env python3
"""Evaluate frozen V4 research and 2024-2025 validation evidence."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any


STRATEGY = "OkxCrossAssetBetaV4"
REQUIRED_GROUPS = ("benchmark", "mag7", "memory", "crypto")
REQUIRED_MODELS = ("systemic_short", "residual_short")


def load_result(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        name = next(
            value
            for value in archive.namelist()
            if value.endswith(".json") and not value.endswith("_config.json")
        )
        payload = json.loads(archive.read(name))
    return payload["strategy"][STRATEGY]


def load_attribution(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))["summary"]


def metric(result: dict[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if result.get(name) is not None:
            return float(result[name])
    return default


def result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "trades": int(result.get("total_trades") or len(result.get("trades", []))),
        "profit_abs": metric(result, "profit_total_abs", "profit_total"),
        "profit_factor": metric(result, "profit_factor"),
        "sharpe": metric(result, "sharpe"),
        "max_drawdown": metric(
            result, "max_drawdown_account", default=float("inf")
        ),
    }


def count_bucket(
    attribution: dict[str, Any],
    bucket: str,
    key: str,
) -> int:
    return int(
        attribution.get(bucket, {}).get(key, {}).get("trades", 0)
    )


def build_report(
    research_result: dict[str, Any],
    research_attr: dict[str, Any],
    validation_result: dict[str, Any],
    validation_attr: dict[str, Any],
    stress_results: dict[str, dict[str, Any]],
    monte_carlo_p95: float | None,
) -> dict[str, Any]:
    research = result_summary(research_result)
    validation = result_summary(validation_result)
    stress = {
        label: result_summary(result)
        for label, result in stress_results.items()
    }
    gates = {
        "research_at_least_100_trades": research["trades"] >= 100,
        "research_at_least_40_longs": (
            count_bucket(research_attr, "by_side", "long") >= 40
        ),
        "research_at_least_30_shorts": (
            count_bucket(research_attr, "by_side", "short") >= 30
        ),
        "each_short_model_at_least_10": all(
            count_bucket(research_attr, "by_model", model) >= 10
            for model in REQUIRED_MODELS
        ),
        "each_group_at_least_10": all(
            count_bucket(research_attr, "by_group", group) >= 10
            for group in REQUIRED_GROUPS
        ),
        "validation_positive": validation["profit_abs"] > 0,
        "validation_pf_at_least_1_20": validation["profit_factor"] >= 1.20,
        "validation_sharpe_at_least_0_70": validation["sharpe"] >= 0.70,
        "validation_drawdown_at_most_15pct": validation["max_drawdown"] <= 0.15,
        "validation_payoff_at_least_1": (
            validation_attr.get("payoff_ratio") is not None
            and float(validation_attr["payoff_ratio"]) >= 1.0
        ),
        "validation_r_expectancy_above_0_10": (
            validation_attr.get("r_coverage") == 1.0
            and validation_attr.get("r_expectancy") is not None
            and float(validation_attr["r_expectancy"]) > 0.10
        ),
        "median_winner_mfe_capture_at_least_20pct": (
            validation_attr.get("median_winner_mfe_capture") is not None
            and float(validation_attr["median_winner_mfe_capture"]) >= 0.20
        ),
        "profit_without_top_5_nonnegative": (
            float(validation_attr.get("profit_without_top_5") or 0.0) >= 0
        ),
        "one_point_five_cost_stress_positive": (
            "1.5x" in stress and stress["1.5x"]["profit_abs"] > 0
        ),
        "two_x_cost_stress_reported": "2x" in stress,
        "monte_carlo_p95_drawdown_at_most_15pct": (
            monte_carlo_p95 is not None and monte_carlo_p95 <= 0.15
        ),
    }
    return {
        "strategy": STRATEGY,
        "research_2018_2023": research,
        "validation_2024_2025": validation,
        "stress": stress,
        "monte_carlo_p95_drawdown": monte_carlo_p95,
        "gates": gates,
        "backtest_release_candidate": all(gates.values()),
        "diagnostic_only_2026_interval_excluded": True,
        "forward_gate": {
            "start": "2026-07-28",
            "required_days": 30,
            "required_closed_trades": 10,
            "status": "not_evaluated",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--research-attribution", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--validation-attribution", type=Path, required=True)
    parser.add_argument(
        "--stress",
        action="append",
        default=[],
        metavar="LABEL=ZIP",
    )
    parser.add_argument("--monte-carlo-p95", type=float)
    args = parser.parse_args()
    stresses = {}
    for value in args.stress:
        label, raw_path = value.split("=", 1)
        stresses[label] = load_result(Path(raw_path))
    report = build_report(
        load_result(args.research),
        load_attribution(args.research_attribution),
        load_result(args.validation),
        load_attribution(args.validation_attribution),
        stresses,
        args.monte_carlo_p95,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["backtest_release_candidate"] else 2)


if __name__ == "__main__":
    main()
