#!/usr/bin/env python3
"""Download adjusted daily underlying data for Beta research.

The output is research-only cash/spot history. It must not be described as OKX
perpetual execution history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests


YAHOO_TICKERS = {
    "QQQ": "QQQ",
    "SPY": "SPY",
    "AAPL": "AAPL",
    "AMZN": "AMZN",
    "GOOGL": "GOOGL",
    "META": "META",
    "MSFT": "MSFT",
    "NVDA": "NVDA",
    "TSLA": "TSLA",
    "MU": "MU",
    "SNDK": "SNDK",
    "SAMSUNG": "005930.KS",
    "SKHYNIX": "000660.KS",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}
KRW_PER_USD_TICKER = "KRW=X"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


def utc_timestamp(value: str) -> int:
    timestamp = pd.Timestamp(value, tz="UTC")
    return int(timestamp.timestamp())


def fetch_chart(
    session: requests.Session,
    ticker: str,
    start: str,
    end: str,
    attempts: int = 3,
) -> dict[str, Any]:
    url = CHART_URL.format(ticker=quote(ticker, safe=""))
    params = {
        "period1": utc_timestamp(start),
        "period2": utc_timestamp(end),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            error = payload.get("chart", {}).get("error")
            result = payload.get("chart", {}).get("result") or []
            if error or not result:
                raise ValueError(f"Yahoo chart error for {ticker}: {error}")
            return result[0]
        except (requests.RequestException, ValueError, KeyError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"unable to download {ticker}") from last_error


def chart_to_frame(chart: dict[str, Any]) -> pd.DataFrame:
    timestamps = chart.get("timestamp") or []
    quote_rows = (chart.get("indicators", {}).get("quote") or [{}])[0]
    adjusted_rows = (
        chart.get("indicators", {}).get("adjclose") or [{}]
    )[0].get("adjclose") or [None] * len(timestamps)
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(timestamps, unit="s", utc=True).normalize(),
            "open": quote_rows.get("open"),
            "high": quote_rows.get("high"),
            "low": quote_rows.get("low"),
            "close": quote_rows.get("close"),
            "volume": quote_rows.get("volume"),
            "adjusted_close": adjusted_rows,
        }
    )
    numeric = ("open", "high", "low", "close", "volume", "adjusted_close")
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
    factor = frame["adjusted_close"] / frame["close"]
    factor = factor.where(factor.gt(0), 1.0).fillna(1.0)
    for column in ("open", "high", "low", "close"):
        frame[column] *= factor
    frame["volume"] = frame["volume"].fillna(0.0).clip(lower=0.0)
    frame = frame.drop(columns=["adjusted_close"])
    frame = frame.drop_duplicates("date", keep="last").sort_values("date")
    valid_price = frame[["open", "high", "low", "close"]].gt(0).all(axis=1)
    return frame.loc[valid_price].reset_index(drop=True)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download_dataset(output_dir: Path, start: str, end: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 BetaResearch/1.0"})
    metadata: dict[str, Any] = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "start_requested": start,
        "end_exclusive_requested": end,
        "source": "Yahoo Finance chart API",
        "price_adjustment": "OHLC multiplied by adjusted_close/close",
        "execution_history": False,
        "assets": {},
    }
    for symbol, ticker in YAHOO_TICKERS.items():
        frame = chart_to_frame(fetch_chart(session, ticker, start, end))
        path = output_dir / f"{symbol}.csv"
        frame.to_csv(path, index=False, date_format="%Y-%m-%d")
        metadata["assets"][symbol] = {
            "ticker": ticker,
            "rows": len(frame),
            "start": frame["date"].min().date().isoformat() if len(frame) else None,
            "end": frame["date"].max().date().isoformat() if len(frame) else None,
            "sha256": file_sha256(path),
        }
        time.sleep(0.15)

    krw = chart_to_frame(fetch_chart(session, KRW_PER_USD_TICKER, start, end))
    krw["krw_usd"] = 1.0 / krw["close"]
    fx_path = output_dir / "KRWUSD.csv"
    krw[["date", "krw_usd"]].to_csv(
        fx_path, index=False, date_format="%Y-%m-%d"
    )
    metadata["fx"] = {
        "ticker": KRW_PER_USD_TICKER,
        "quote": "KRW per USD inverted to USD per KRW",
        "rows": len(krw),
        "start": krw["date"].min().date().isoformat(),
        "end": krw["date"].max().date().isoformat(),
        "sha256": file_sha256(fx_path),
    }
    metadata_path = output_dir / "source_metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default="2026-01-01")
    args = parser.parse_args()
    metadata = download_dataset(args.output_dir, args.start, args.end)
    print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
