#!/usr/bin/env python3
"""Compute bootstrap drawdown/ruin risk and a Deflated Sharpe estimate."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import zipfile
from pathlib import Path
from typing import Any


def load_result(path: Path, strategy: str) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        result_name = next(
            name
            for name in archive.namelist()
            if name.endswith(".json") and not name.endswith("_config.json")
        )
        payload = json.loads(archive.read(result_name))
    return payload["strategy"][strategy]


def equity_returns(result: dict[str, Any], starting_balance: float) -> list[float]:
    equity = starting_balance
    returns: list[float] = []
    for trade in sorted(result["trades"], key=lambda row: row["close_timestamp"]):
        profit = float(trade["profit_abs"])
        returns.append(profit / equity)
        equity += profit
    return returns


def daily_returns(result: dict[str, Any], starting_balance: float) -> list[float]:
    equity = starting_balance
    returns: list[float] = []
    for _, raw_profit in result["daily_profit"]:
        profit = float(raw_profit)
        returns.append(profit / equity)
        equity += profit
    return returns


def deflated_sharpe(returns: list[float], trials: int) -> dict[str, float]:
    count = len(returns)
    mean = statistics.fmean(returns)
    stdev = statistics.stdev(returns)
    sharpe = mean / stdev
    centered = [value - mean for value in returns]
    variance = sum(value * value for value in centered) / count
    skew = sum(value**3 for value in centered) / count / variance**1.5
    kurtosis = sum(value**4 for value in centered) / count / variance**2
    adjustment = max(1e-12, 1 - skew * sharpe + ((kurtosis - 1) / 4) * sharpe**2)
    sharpe_std = math.sqrt(adjustment / (count - 1))
    normal = statistics.NormalDist()
    euler_gamma = 0.5772156649015329
    expected_max = sharpe_std * (
        (1 - euler_gamma) * normal.inv_cdf(1 - 1 / trials)
        + euler_gamma * normal.inv_cdf(1 - 1 / (trials * math.e))
    )
    probability = normal.cdf(
        (sharpe - expected_max) * math.sqrt(count - 1) / math.sqrt(adjustment)
    )
    return {
        "observations": count,
        "trials": trials,
        "daily_sharpe": sharpe,
        "annualized_sharpe_365": sharpe * math.sqrt(365),
        "expected_max_daily_sharpe_under_null": expected_max,
        "deflated_sharpe_probability": probability,
        "skew": skew,
        "kurtosis": kurtosis,
    }


def monte_carlo(
    returns: list[float], starting_balance: float, floor: float, paths: int, horizon: int, seed: int
) -> dict[str, float | int]:
    generator = random.Random(seed)
    drawdowns: list[float] = []
    terminal: list[float] = []
    floor_hits = 0
    for _ in range(paths):
        equity = starting_balance
        peak = equity
        max_drawdown = 0.0
        hit = False
        for _ in range(horizon):
            equity *= 1 + generator.choice(returns)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, 1 - equity / peak)
            hit = hit or equity <= floor
        drawdowns.append(max_drawdown)
        terminal.append(equity)
        floor_hits += int(hit)
    drawdowns.sort()
    terminal.sort()
    return {
        "paths": paths,
        "horizon_trades": horizon,
        "drawdown_p95": drawdowns[math.ceil(paths * 0.95) - 1],
        "drawdown_p99": drawdowns[math.ceil(paths * 0.99) - 1],
        "floor_hit_probability": floor_hits / paths,
        "terminal_equity_p05": terminal[math.floor(paths * 0.05)],
        "terminal_equity_median": terminal[paths // 2],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--strategy", default="OkxAggressiveTrendV2")
    parser.add_argument("--starting-balance", type=float, default=30.0)
    parser.add_argument("--floor", type=float, default=15.0)
    parser.add_argument("--trials", type=int, default=65)
    parser.add_argument("--paths", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    result = load_result(args.result, args.strategy)
    trade_returns = equity_returns(result, args.starting_balance)
    report = {
        "source": str(args.result),
        "strategy": args.strategy,
        "trade_count": len(trade_returns),
        "monte_carlo_observed_horizon": monte_carlo(
            trade_returns,
            args.starting_balance,
            args.floor,
            args.paths,
            len(trade_returns),
            args.seed,
        ),
        "monte_carlo_100_trade_horizon": monte_carlo(
            trade_returns,
            args.starting_balance,
            args.floor,
            args.paths,
            100,
            args.seed + 1,
        ),
        "deflated_sharpe": deflated_sharpe(daily_returns(result, args.starting_balance), args.trials),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
