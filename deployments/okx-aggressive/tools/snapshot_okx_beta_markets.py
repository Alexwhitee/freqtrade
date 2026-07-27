#!/usr/bin/env python3
"""Print an auditable public OKX contract-specification snapshot for V3."""

from __future__ import annotations

import json
from datetime import datetime, timezone

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


def main() -> None:
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
    print(
        json.dumps(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "exchange": "OKX",
                "ccxt_version": ccxt.__version__,
                "market_type": "swap",
                "markets": rows,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
