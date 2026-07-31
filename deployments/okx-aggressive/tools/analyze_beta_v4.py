#!/usr/bin/env python3
"""Create an auditable V4 trade-attribution report from a Freqtrade export."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any


STRATEGY = "OkxCrossAssetBetaV4"
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


def load_result(path: Path, strategy: str = STRATEGY) -> dict[str, Any]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            result_name = next(
                name
                for name in archive.namelist()
                if name.endswith(".json") and not name.endswith("_config.json")
            )
            payload = json.loads(archive.read(result_name))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    strategies = payload.get("strategy", payload)
    if strategy not in strategies:
        available = ", ".join(sorted(strategies))
        raise ValueError(f"strategy {strategy!r} is absent; available: {available}")
    return strategies[strategy]


def load_risk_ledger(path: Path | None) -> dict[str, float]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("trades", payload)
    ledger: dict[str, float] = {}
    if isinstance(records, dict):
        records = [
            {"trade_key": key, "initial_r_amount": value}
            for key, value in records.items()
        ]
    for row in records:
        key = str(row["trade_key"])
        value = float(row["initial_r_amount"])
        if value <= 0 or not math.isfinite(value):
            raise ValueError(f"invalid initial_r_amount for {key}")
        ledger[key] = value
    return ledger


def trade_key(trade: dict[str, Any]) -> str:
    return f"{trade.get('pair', '')}|{trade.get('open_date', '')}"


def _profit_at_rate(trade: dict[str, Any], rate: float) -> float:
    open_rate = float(trade.get("open_rate") or 0.0)
    leverage = float(trade.get("leverage") or 1.0)
    if open_rate <= 0 or rate <= 0:
        return 0.0
    raw = rate / open_rate - 1.0
    return (-raw if bool(trade.get("is_short")) else raw) * leverage


def _execution_cost(trade: dict[str, Any]) -> float:
    orders = trade.get("orders") or []
    entry_notional = sum(
        float(order.get("cost") or 0.0)
        for order in orders
        if order.get("ft_is_entry")
    )
    exit_notional = sum(
        float(order.get("cost") or 0.0)
        for order in orders
        if not order.get("ft_is_entry")
    )
    return (
        entry_notional * float(trade.get("fee_open") or 0.0)
        + exit_notional * float(trade.get("fee_close") or 0.0)
        + abs(float(trade.get("funding_fees") or 0.0))
    )


def attribute_trade(
    trade: dict[str, Any],
    risk_ledger: dict[str, float],
) -> dict[str, Any]:
    pair = str(trade.get("pair", ""))
    base = pair.split("/", 1)[0]
    is_short = bool(trade.get("is_short"))
    favorable_rate = (
        float(trade.get("min_rate") or trade.get("open_rate") or 0.0)
        if is_short
        else float(trade.get("max_rate") or trade.get("open_rate") or 0.0)
    )
    adverse_rate = (
        float(trade.get("max_rate") or trade.get("open_rate") or 0.0)
        if is_short
        else float(trade.get("min_rate") or trade.get("open_rate") or 0.0)
    )
    mfe = max(0.0, _profit_at_rate(trade, favorable_rate))
    mae = min(0.0, _profit_at_rate(trade, adverse_rate))
    profit_ratio = float(trade.get("profit_ratio") or 0.0)
    initial_r_amount = risk_ledger.get(trade_key(trade))
    profit_abs = float(trade.get("profit_abs") or 0.0)
    r_multiple = (
        profit_abs / initial_r_amount
        if initial_r_amount is not None and initial_r_amount > 0
        else None
    )
    tag = str(trade.get("enter_tag") or "unknown")
    return {
        "trade_key": trade_key(trade),
        "pair": pair,
        "group": GROUPS.get(base, "unknown"),
        "side": "short" if is_short else "long",
        "signal_model": tag,
        "exit_reason": str(trade.get("exit_reason") or "unknown"),
        "open_date": trade.get("open_date"),
        "close_date": trade.get("close_date"),
        "duration_minutes": int(trade.get("trade_duration") or 0),
        "leverage": float(trade.get("leverage") or 1.0),
        "profit_abs": profit_abs,
        "profit_ratio": profit_ratio,
        "mfe_ratio": mfe,
        "mae_ratio": mae,
        "mfe_capture": profit_ratio / mfe if profit_ratio > 0 and mfe > 0 else None,
        "initial_r_amount": initial_r_amount,
        "r_multiple": r_multiple,
        "fee_and_funding_abs": _execution_cost(trade),
        "funding_abs": float(trade.get("funding_fees") or 0.0),
    }


def _bucket(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[field])].append(row)
    return {
        key: {
            "trades": len(values),
            "profit_abs": sum(float(row["profit_abs"]) for row in values),
        }
        for key, values in sorted(grouped.items())
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profits = [float(row["profit_abs"]) for row in rows]
    winners = [value for value in profits if value > 0]
    losers = [value for value in profits if value < 0]
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    average_win = statistics.mean(winners) if winners else 0.0
    average_loss = abs(statistics.mean(losers)) if losers else 0.0
    r_values = [
        float(row["r_multiple"])
        for row in rows
        if row["r_multiple"] is not None
    ]
    captures = [
        float(row["mfe_capture"])
        for row in rows
        if row["mfe_capture"] is not None
    ]
    sorted_profits = sorted(profits, reverse=True)
    return {
        "trades": len(rows),
        "profit_abs": sum(profits),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "win_rate": len(winners) / len(rows) if rows else 0.0,
        "average_win": average_win,
        "average_loss": average_loss,
        "payoff_ratio": average_win / average_loss if average_loss > 0 else None,
        "profit_without_top_5": sum(sorted_profits[5:]),
        "r_coverage": len(r_values) / len(rows) if rows else 0.0,
        "r_expectancy": statistics.mean(r_values) if r_values else None,
        "median_winner_mfe_capture": statistics.median(captures) if captures else None,
        "fee_and_funding_abs": sum(
            float(row["fee_and_funding_abs"]) for row in rows
        ),
        "funding_abs": sum(float(row["funding_abs"]) for row in rows),
        "by_group": _bucket(rows, "group"),
        "by_side": _bucket(rows, "side"),
        "by_model": _bucket(rows, "signal_model"),
        "by_exit": _bucket(rows, "exit_reason"),
    }


def build_report(
    result: dict[str, Any],
    risk_ledger: dict[str, float] | None = None,
    strategy: str = STRATEGY,
) -> dict[str, Any]:
    rows = [
        attribute_trade(trade, risk_ledger or {})
        for trade in result.get("trades", [])
        if not bool(trade.get("is_open"))
    ]
    summary = summarize_rows(rows)
    summary["max_drawdown"] = float(
        result.get("max_drawdown_account")
        if result.get("max_drawdown_account") is not None
        else math.inf
    )
    summary["sharpe"] = (
        float(result["sharpe"]) if result.get("sharpe") is not None else None
    )
    return {
        "strategy": strategy,
        "summary": summary,
        "trades": rows,
        "methodology": {
            "r_values_require_sidecar": True,
            "r_sidecar_key": "PAIR|OPEN_DATE",
            "missing_r_is_never_inferred_from_final_stop": True,
            "mfe_mae_use_exported_min_max_rates": True,
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else [
        "trade_key",
        "pair",
        "group",
        "side",
        "signal_model",
        "profit_abs",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--strategy", default=STRATEGY)
    parser.add_argument("--risk-ledger", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-csv", type=Path)
    args = parser.parse_args()
    report = build_report(
        load_result(args.result, args.strategy),
        load_risk_ledger(args.risk_ledger),
        args.strategy,
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(text + "\n", encoding="utf-8")
    if args.output_csv:
        write_csv(args.output_csv, report["trades"])
    print(text)


if __name__ == "__main__":
    main()
