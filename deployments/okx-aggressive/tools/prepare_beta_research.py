#!/usr/bin/env python3
"""Validate underlying research files and build a USD-denominated panel.

Input files are named ``SYMBOL.csv`` and contain the columns declared by
``research_data_manifest.beta.json``. Korean prices are multiplied by the
date-aligned KRW/USD rate from ``KRWUSD.csv``. This tool is research-only: it
does not create or imitate OKX perpetual candles.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def load_csv(path: Path, required: set[str]) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], utc=True, errors="coerce")
    if frame["date"].isna().any() or frame["date"].duplicated().any():
        raise ValueError(f"{path.name} contains invalid or duplicate dates")
    return frame.sort_values("date")


def build_panel(source_dir: Path, manifest_path: Path) -> pd.DataFrame:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = set(manifest["required_columns"])
    fx_path = source_dir / "KRWUSD.csv"
    fx = load_csv(fx_path, set(manifest["usd_fx_columns"]))
    fx = fx[["date", "krw_usd"]]
    if (pd.to_numeric(fx["krw_usd"], errors="coerce") <= 0).any():
        raise ValueError("KRWUSD.csv contains a non-positive rate")

    panels: list[pd.DataFrame] = []
    for asset in manifest["assets"]:
        symbol = asset["symbol"]
        frame = load_csv(source_dir / f"{symbol}.csv", required)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[["open", "high", "low", "close", "volume"]].isna().any().any():
            raise ValueError(f"{symbol}.csv contains non-numeric OHLCV data")
        if asset["currency"] == "KRW":
            frame = pd.merge_asof(
                frame.sort_values("date"),
                fx.sort_values("date"),
                on="date",
                direction="backward",
                tolerance=pd.Timedelta(days=3),
            )
            if frame["krw_usd"].isna().any():
                raise ValueError(f"{symbol}.csv has dates without a KRW/USD rate")
            for column in ("open", "high", "low", "close"):
                frame[column] *= frame["krw_usd"]
        frame["symbol"] = symbol
        frame["group"] = asset["group"]
        frame["source_currency"] = asset["currency"]
        frame["price_currency"] = "USD"
        panels.append(
            frame[
                [
                    "date",
                    "symbol",
                    "group",
                    "source_currency",
                    "price_currency",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ]
            ]
        )
    return pd.concat(panels, ignore_index=True).sort_values(["date", "symbol"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).parents[1] / "runtime" / "research_data_manifest.beta.json",
    )
    args = parser.parse_args()
    panel = build_panel(args.source_dir, args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(args.output, index=False)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "rows": len(panel),
                "symbols": int(panel["symbol"].nunique()),
                "start": panel["date"].min().isoformat(),
                "end": panel["date"].max().isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
