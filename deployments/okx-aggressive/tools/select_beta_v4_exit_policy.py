#!/usr/bin/env python3
"""Select a V4 exit policy from frozen walk-forward fold reports."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


POLICIES = {"channel_only", "delayed_break_even", "slow_atr"}


def parse_fold(value: str) -> tuple[str, str, Path]:
    identity, raw_path = value.split("=", 1)
    policy, fold = identity.split(":", 1)
    if policy not in POLICIES or not fold:
        raise ValueError(f"invalid fold identity: {identity}")
    return policy, fold, Path(raw_path)


def load_attribution(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "summary" not in payload:
        raise ValueError(f"{path} is not a V4 attribution report")
    return payload["summary"]


def evaluate_policy(reports: list[dict[str, Any]]) -> dict[str, Any]:
    positive_folds = sum(float(row["profit_abs"]) > 0 for row in reports)
    profit_factors = [
        float(row["profit_factor"])
        for row in reports
        if row.get("profit_factor") is not None
    ]
    r_values = [
        float(row["r_expectancy"])
        for row in reports
        if row.get("r_expectancy") is not None
    ]
    all_r_complete = bool(reports) and all(
        float(row.get("r_coverage") or 0.0) == 1.0 for row in reports
    )
    combined_without_top = sum(
        float(row["profit_without_top_5"]) for row in reports
    )
    max_drawdown = max(
        (float(row.get("max_drawdown") or 0.0) for row in reports),
        default=float("inf"),
    )
    gates = {
        "at_least_two_positive_folds": positive_folds >= 2,
        "median_pf_above_1_10": (
            bool(profit_factors)
            and statistics.median(profit_factors) > 1.10
        ),
        "max_drawdown_at_most_15pct": max_drawdown <= 0.15,
        "combined_profit_without_top_5_nonnegative": combined_without_top >= 0,
        "complete_r_coverage": all_r_complete,
        "r_expectancy_available_for_every_fold": len(r_values) == len(reports),
    }
    return {
        "folds": len(reports),
        "positive_folds": positive_folds,
        "median_profit_factor": (
            statistics.median(profit_factors) if profit_factors else None
        ),
        "median_r_expectancy": (
            statistics.median(r_values)
            if reports and len(r_values) == len(reports)
            else None
        ),
        "max_drawdown": max_drawdown,
        "combined_profit_without_top_5": combined_without_top,
        "gates": gates,
        "eligible": bool(reports) and all(gates.values()),
    }


def select_policy(
    fold_reports: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    evaluation = {
        policy: evaluate_policy(fold_reports.get(policy, []))
        for policy in sorted(POLICIES)
    }
    eligible = [
        (policy, values)
        for policy, values in evaluation.items()
        if values["eligible"]
    ]
    selected = None
    if eligible:
        selected = min(
            eligible,
            key=lambda item: (
                -float(item[1]["median_r_expectancy"]),
                float(item[1]["max_drawdown"]),
                item[0],
            ),
        )[0]
    return {
        "policies": evaluation,
        "selected_policy": selected,
        "release_stopped": selected is None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fold",
        action="append",
        required=True,
        metavar="POLICY:FOLD=ATTRIBUTION_JSON",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports: dict[str, list[dict[str, Any]]] = {
        policy: [] for policy in POLICIES
    }
    identities: set[tuple[str, str]] = set()
    for value in args.fold:
        policy, fold, path = parse_fold(value)
        if (policy, fold) in identities:
            raise ValueError(f"duplicate fold: {policy}:{fold}")
        identities.add((policy, fold))
        reports[policy].append(load_attribution(path))
    result = select_policy(reports)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if result["selected_policy"] else 2)


if __name__ == "__main__":
    main()
