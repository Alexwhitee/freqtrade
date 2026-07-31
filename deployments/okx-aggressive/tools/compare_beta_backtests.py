#!/usr/bin/env python3
"""Compare frozen V3 and V4 Freqtrade result archives on identical inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analyze_beta_v4 import build_report, load_result, summarize_rows


def summarize(path: Path, strategy: str) -> dict[str, Any]:
    raw = load_result(path, strategy)
    report = build_report(raw)
    summary = report["summary"]
    by_pair: dict[str, dict[str, float | int]] = {}
    for row in report["trades"]:
        pair = str(row["pair"])
        bucket = by_pair.setdefault(pair, {"trades": 0, "profit_abs": 0.0})
        bucket["trades"] = int(bucket["trades"]) + 1
        bucket["profit_abs"] = float(bucket["profit_abs"]) + float(row["profit_abs"])
    return {
        "strategy": strategy,
        "backtest_start": raw.get("backtest_start"),
        "backtest_end": raw.get("backtest_end"),
        "trades": summary["trades"],
        "profit_abs": summary["profit_abs"],
        "profit_pct_wallet": float(raw.get("profit_total", 0.0)) * 100,
        "profit_factor": summary["profit_factor"],
        "sharpe_closed": raw.get("sharpe"),
        "sharpe_wallet": raw.get("sharpe_daily"),
        "max_drawdown_pct": summary["max_drawdown"] * 100,
        "win_rate_pct": summary["win_rate"] * 100,
        "average_win": summary["average_win"],
        "average_loss": summary["average_loss"],
        "payoff_ratio": summary["payoff_ratio"],
        "gross_profit": summary["gross_profit"],
        "gross_loss": summary["gross_loss"],
        "fee_and_funding_abs": summary["fee_and_funding_abs"],
        "funding_abs": summary["funding_abs"],
        "conservative_cost_stress_profit_abs": {
            "1.5x": summary["profit_abs"] - 0.5 * summary["fee_and_funding_abs"],
            "2.0x": summary["profit_abs"] - summary["fee_and_funding_abs"],
        },
        "rejected_signals": raw.get("rejected_signals"),
        "by_pair": by_pair,
        "by_side": summary["by_side"],
        "by_model": summary["by_model"],
        "by_exit": summary["by_exit"],
        "trades_detail": report["trades"],
    }


def compare(v3_path: Path, v4_path: Path) -> dict[str, Any]:
    v3 = summarize(v3_path, "OkxCrossAssetBetaV3")
    v4 = summarize(v4_path, "OkxCrossAssetBetaV4")
    v4_keys = {str(row["trade_key"]) for row in v4["trades_detail"]}
    overlap = [
        str(row["trade_key"])
        for row in v3["trades_detail"]
        if str(row["trade_key"]) in v4_keys
    ]
    omitted_v3 = [
        row for row in v3["trades_detail"] if str(row["trade_key"]) not in v4_keys
    ]
    return {
        "V3": v3,
        "V4": v4,
        "delta_v4_minus_v3": {
            "trades": int(v4["trades"]) - int(v3["trades"]),
            "profit_abs": float(v4["profit_abs"]) - float(v3["profit_abs"]),
            "profit_pct_wallet": float(v4["profit_pct_wallet"])
            - float(v3["profit_pct_wallet"]),
            "max_drawdown_pct": float(v4["max_drawdown_pct"])
            - float(v3["max_drawdown_pct"]),
        },
        "trade_overlap": {
            "shared_trade_keys": overlap,
            "shared_count": len(overlap),
            "v3_omitted": summarize_rows(omitted_v3),
        },
        "methodology": {
            "requires_identical_external_parameters": True,
            "profit_factor_without_losses_is_null": True,
            "single_trade_metrics_are_not_statistically_meaningful": True,
            "cost_stress_scales_fee_plus_absolute_funding_only": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v3_result", type=Path)
    parser.add_argument("v4_result", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.v3_result, args.v4_result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
