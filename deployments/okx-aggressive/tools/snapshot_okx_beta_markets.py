#!/usr/bin/env python3
"""Print an auditable public OKX contract-specification snapshot for V3."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import ccxt


BASES = (
    "QQQ",
    "SPY",
    "AAPL",
    "AMZN",
    "GOOGL",
    "META",
    "MSFT",
    "NVDA",
    "TSLA",
    "MU",
    "SNDK",
    "SAMSUNG",
    "SKHYNIX",
    "BTC",
    "ETH",
    "DRAM",
    "LITE",
    "SKHY",
)


def build_snapshot() -> dict[str, object]:
    exchange = ccxt.okx(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )
    markets = exchange.load_markets()
    rows = []
    for base in BASES:
        symbol = f"{base}/USDT:USDT"
        market = markets.get(symbol)
        if market is None:
            rows.append({"symbol": symbol, "visible": False})
            continue
        rows.append(
            {
                "symbol": symbol,
                "id": market.get("id"),
                "visible": True,
                "active": market.get("active"),
                "swap": market.get("swap"),
                "linear": market.get("linear"),
                "contract_size": market.get("contractSize"),
                "amount_min": market.get("limits", {}).get("amount", {}).get("min"),
                "amount_precision": market.get("precision", {}).get("amount"),
                "price_precision": market.get("precision", {}).get("price"),
            }
        )
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "exchange": "OKX",
        "ccxt_version": ccxt.__version__,
        "market_type": "swap",
        "markets": rows,
    }


def write_snapshot(path: Path, snapshot: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="Atomically replace this snapshot instead of printing JSON.",
    )
    args = parser.parse_args()
    snapshot = build_snapshot()
    if args.output:
        write_snapshot(args.output, snapshot)
    else:
        print(json.dumps(snapshot, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
