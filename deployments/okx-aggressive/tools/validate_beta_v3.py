#!/usr/bin/env python3
"""Evaluate V3 backtest exports against the frozen release gates."""

from __future__ import annotations

import argparse
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


STRATEGY = "OkxCrossAssetBetaV3"
GROUPS = {
    "QQQ": "benchmark",
    "AAPL": "mag7",
    "AMZN": "mag7",
    "GOOGL": "mag7",
    "META": "mag7",
    "MSFT": "mag7",
    "NVDA": "mag7",
    "TSLA": "mag7",
    "MU": "memory",
    "SNDK": "memory",
    "SAMSUNG": "memory",
    "SKHYNIX": "memory",
    "BTC": "crypto",
    "ETH": "crypto",
}


def load_result(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        result_name = next(
            name
            for name in archive.namelist()
            if name.endswith(".json") and not name.endswith("_config.json")
        )
        payload = json.loads(archive.read(result_name))
    return payload["strategy"][STRATEGY]


def metric(result: dict[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if result.get(name) is not None:
            return float(result[name])
    return default


def drawdown_days(result: dict[str, Any]) -> int | None:
    start = result.get("drawdown_start")
    end = result.get("drawdown_end")
    if not start or not end:
        return None
    return (datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).days


def group_report(result: dict[str, Any]) -> dict[str, dict[str, float | int]]:
    report = {
        group: {"trades": 0, "profit_abs": 0.0}
        for group in ("benchmark", "mag7", "memory", "crypto")
    }
    for trade in result.get("trades", []):
        base = str(trade.get("pair", "")).split("/", 1)[0]
        group = GROUPS.get(base)
        if group:
            report[group]["trades"] += 1
            report[group]["profit_abs"] += float(trade.get("profit_abs") or 0.0)
    return report


def top_profit_residual(result: dict[str, Any], count: int = 5) -> float:
    profits = sorted(
        (float(trade.get("profit_abs") or 0.0) for trade in result.get("trades", [])),
        reverse=True,
    )
    return sum(profits[count:])


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "trades": int(result.get("total_trades") or len(result.get("trades", []))),
        "profit_abs": metric(result, "profit_total_abs", "profit_total"),
        "profit_factor": metric(result, "profit_factor"),
        "sharpe": metric(result, "sharpe"),
        "max_drawdown": metric(result, "max_drawdown_account", default=float("inf")),
        "drawdown_days": drawdown_days(result),
        "profit_without_top_5": top_profit_residual(result),
        "groups": group_report(result),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--validation-2026", type=Path, required=True)
    parser.add_argument(
        "--stress",
        action="append",
        default=[],
        metavar="LABEL=ZIP",
        help="Add fee/slippage/funding stress result, including 1.5x and 2x cases.",
    )
    args = parser.parse_args()

    research = summarize(load_result(args.research))
    validation = summarize(load_result(args.validation_2026))
    stresses: dict[str, dict[str, Any]] = {}
    for value in args.stress:
        label, raw_path = value.split("=", 1)
        stresses[label] = summarize(load_result(Path(raw_path)))

    gates = {
        "research_at_least_100_closed_trades": research["trades"] >= 100,
        "validation_2026_positive": validation["profit_abs"] > 0,
        "validation_2026_pf_at_least_1_20": validation["profit_factor"] >= 1.20,
        "validation_2026_sharpe_at_least_0_70": validation["sharpe"] >= 0.70,
        "max_drawdown_at_most_15pct": validation["max_drawdown"] <= 0.15,
        "drawdown_shorter_than_v2_514_days": (
            validation["drawdown_days"] is not None and validation["drawdown_days"] < 514
        ),
        "positive_without_top_5": validation["profit_without_top_5"] > 0,
        "all_supplied_cost_stresses_positive": (
            bool(stresses) and all(item["profit_abs"] > 0 for item in stresses.values())
        ),
        "has_1_5x_and_2x_stress": {"1.5x", "2x"}.issubset(stresses),
    }
    report = {
        "strategy": STRATEGY,
        "research": research,
        "validation_2026": validation,
        "stress": stresses,
        "gates": gates,
        "backtest_release_candidate": all(gates.values()),
        "dry_run_gate": {
            "required_days": 30,
            "required_closed_trades": 10,
            "status": "not_evaluated_by_backtest_tool",
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["backtest_release_candidate"] else 2)


if __name__ == "__main__":
    main()
