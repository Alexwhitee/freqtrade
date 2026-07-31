#!/usr/bin/env python3
"""Run a research-only daily economic proxy for OkxCrossAssetBetaV5.

This uses adjusted cash/spot underlying data. It tests economic signal shape
and portfolio constraints, not OKX perpetual fills or intraday 4h fidelity.
Signals use completed daily data and execute at the next available session.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd


GROUPS = {
    "QQQ": "benchmark",
    "SPY": "benchmark",
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
GROUP_ASSETS = {
    group: [symbol for symbol, asset_group in GROUPS.items() if asset_group == group]
    for group in ("benchmark", "mag7", "memory", "crypto")
}
TRADABLE = tuple(symbol for symbol in GROUPS if symbol != "SPY")
GROUP_WEIGHT = {"benchmark": 0.35, "mag7": 0.30, "memory": 0.20, "crypto": 0.15}
MIN_VALID = {"benchmark": 1, "mag7": 5, "memory": 2, "crypto": 2}


@dataclass(frozen=True)
class ResearchParameters:
    absolute_threshold: float = 0.25
    residual_threshold: float = -0.50
    long_adx: float = 20.0
    short_adx: float = 25.0
    long_channel: int = 40
    short_channel: int = 60
    long_exit_channel: int = 10
    short_exit_channel: int = 25
    time_stop_sessions: int = 20


@dataclass(frozen=True)
class SimulationOptions:
    start: str
    end: str
    max_slots: int = 1
    model_set: Literal["breakout_only", "v5"] = "v5"
    side_set: Literal["long_only", "dual"] = "dual"
    exit_policy: Literal["channel_only", "delayed_break_even", "slow_atr"] = (
        "channel_only"
    )
    include_btc: bool = True
    include_crypto: bool = True
    include_memory: bool = True
    portfolio_risk_scale: float = 1.0
    portfolio_profile: Literal["v5", "v6"] = "v5"
    max_pair_correlation: float = 0.90
    aggregate_risk_cap: float | None = None
    cost_multiplier: float = 1.0
    initial_balance: float = 30.0
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005
    carry_bps_per_day: float = 0.5


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def normalized_momentum(
    close: pd.Series, realized_vol20: pd.Series, periods: int
) -> pd.Series:
    numerator = np.log(close / close.shift(periods))
    denominator = realized_vol20 * np.sqrt(periods / 252.0)
    return np.tanh(safe_divide(numerator, denominator)).fillna(0.0)


def volatility_quality(vol_ratio: pd.Series) -> pd.Series:
    result = pd.Series(0.0, index=vol_ratio.index, dtype=float)
    result.loc[vol_ratio <= 1.25] = 1.0
    result.loc[(vol_ratio > 1.25) & (vol_ratio <= 1.75)] = 0.75
    result.loc[(vol_ratio > 1.75) & (vol_ratio <= 2.00)] = 0.50
    return result


def adx(frame: pd.DataFrame, periods: int = 14) -> pd.Series:
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    true_range = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(
        alpha=1 / periods, adjust=False, min_periods=periods
    ).mean()
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / periods, adjust=False, min_periods=periods
    ).mean() / atr
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / periods, adjust=False, min_periods=periods
    ).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / periods, adjust=False, min_periods=periods).mean()


def asset_features(frame: pd.DataFrame, params: ResearchParameters) -> pd.DataFrame:
    result = frame.copy().sort_values("date").reset_index(drop=True)
    result["daily_return"] = np.log(result["close"]).diff()
    result["ema20"] = result["close"].ewm(
        span=20, adjust=False, min_periods=20
    ).mean()
    result["ema60"] = result["close"].ewm(
        span=60, adjust=False, min_periods=60
    ).mean()
    result["realized_vol20"] = result["daily_return"].rolling(20).std() * np.sqrt(
        252
    )
    for periods in (20, 60, 120):
        result[f"momentum{periods}"] = normalized_momentum(
            result["close"], result["realized_vol20"], periods
        )
    ema_numerator = np.log(result["ema20"] / result["ema60"])
    ema_denominator = result["realized_vol20"] * np.sqrt(40 / 252.0)
    result["ema_direction"] = np.tanh(
        safe_divide(ema_numerator, ema_denominator)
    ).fillna(0.0)
    result["absolute_trend"] = (
        result["momentum20"]
        + result["momentum60"]
        + result["momentum120"]
        + result["ema_direction"]
    ) / 4.0
    result.loc[result["close"].shift(120).isna(), "absolute_trend"] = np.nan
    median_vol = result["realized_vol20"].rolling(
        252, min_periods=60
    ).median().shift(1)
    vol_ratio = safe_divide(result["realized_vol20"], median_vol)
    vol_ratio = vol_ratio.mask(
        (result["realized_vol20"] == 0) & (median_vol == 0), 1.0
    )
    result["vol_ratio"] = vol_ratio
    result["risk_quality"] = volatility_quality(vol_ratio)
    result.loc[vol_ratio.isna(), "risk_quality"] = np.nan

    prior_close = result["close"].shift(1)
    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - prior_close).abs(),
            (result["low"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr"] = true_range.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()
    result["adx"] = adx(result)
    result["volume_median30"] = result["volume"].rolling(30).median().shift(1)
    result["channel_high_long"] = (
        result["high"].rolling(params.long_channel).max().shift(1)
    )
    result["channel_low_long"] = (
        result["low"].rolling(params.long_channel).min().shift(1)
    )
    result["channel_low_short"] = (
        result["low"].rolling(params.short_channel).min().shift(1)
    )
    result["channel_low_exit"] = (
        result["low"].rolling(params.long_exit_channel).min().shift(1)
    )
    result["channel_high_exit"] = (
        result["high"].rolling(params.short_exit_channel).max().shift(1)
    )
    long_touch = (
        (result["low"] <= result["ema20"] + result["atr"] * 0.25)
        & (result["close"] >= result["ema60"])
        & (result["ema20"] > result["ema60"])
    )
    short_touch = (
        (result["high"] >= result["ema20"] - result["atr"] * 0.25)
        & (result["close"] <= result["ema60"])
        & (result["ema20"] < result["ema60"])
    )
    result["recent_long_pullback"] = (
        long_touch.shift(1).rolling(3, min_periods=1).max().fillna(False).astype(bool)
    )
    result["recent_short_pullback"] = (
        short_touch.shift(1).rolling(3, min_periods=1).max().fillna(False).astype(bool)
    )
    result["previous_close"] = result["close"].shift(1)
    return result


def add_residual(
    symbol: str,
    frame: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    result = frame.copy()
    if symbol in {"BTC", "ETH", "SPY"}:
        result["residual_momentum"] = (
            result["momentum20"] + result["momentum60"]
        ) / 2.0
        return result
    benchmark = "SPY" if symbol == "QQQ" else "QQQ"
    benchmark_returns = frames[benchmark][["date", "daily_return"]].rename(
        columns={"daily_return": "benchmark_return"}
    )
    aligned = result[["date", "daily_return"]].merge(
        benchmark_returns, on="date", how="left"
    )
    covariance = aligned["daily_return"].rolling(60, min_periods=40).cov(
        aligned["benchmark_return"]
    )
    variance = aligned["benchmark_return"].rolling(60, min_periods=40).var()
    beta = safe_divide(covariance, variance)
    residual = aligned["daily_return"] - beta * aligned["benchmark_return"]
    residual_vol = residual.rolling(20, min_periods=15).std()
    values = []
    for periods in (20, 60):
        cumulative = residual.rolling(periods, min_periods=periods).sum()
        normalized = np.tanh(
            safe_divide(cumulative, residual_vol * np.sqrt(periods))
        )
        normalized = normalized.mask(
            (cumulative == 0) & (residual_vol == 0), 0.0
        )
        values.append(normalized)
    result["residual_momentum"] = (
        values[0].to_numpy() + values[1].to_numpy()
    ) / 2.0
    return result


def regimes_with_hysteresis(
    scores: pd.Series, complete: pd.Series
) -> list[str]:
    def raw(score: float) -> str:
        if score >= 0.50:
            return "strong_risk_on"
        if score >= 0.20:
            return "risk_on"
        if score > -0.20:
            return "neutral"
        if score > -0.50:
            return "risk_off"
        return "strong_risk_off"

    def candidate(score: float, current: str) -> str:
        margin = 0.05
        if current == "strong_risk_on" and score >= 0.50 - margin:
            return current
        if current == "risk_on":
            if score >= 0.50 + margin:
                return "strong_risk_on"
            if score >= 0.20 - margin:
                return current
        if current == "neutral" and -0.20 - margin < score < 0.20 + margin:
            return current
        if current == "risk_off":
            if score <= -0.50 - margin:
                return "strong_risk_off"
            if score <= -0.20 + margin:
                return current
        if current == "strong_risk_off" and score <= -0.50 + margin:
            return current
        return raw(score)

    current = "blocked"
    pending = "blocked"
    pending_count = 0
    output = []
    for score, is_complete in zip(scores, complete):
        if not bool(is_complete) or not np.isfinite(score):
            current = pending = "blocked"
            pending_count = 0
            output.append(current)
            continue
        next_regime = candidate(float(score), current)
        if next_regime == current:
            pending = current
            pending_count = 0
        elif next_regime == pending:
            pending_count += 1
        else:
            pending = next_regime
            pending_count = 1
        if pending_count >= 2:
            current = pending
            pending_count = 0
        output.append(current)
    return output


def build_panel(path: Path, params: ResearchParameters) -> pd.DataFrame:
    panel = pd.read_parquet(path)
    panel["date"] = pd.to_datetime(panel["date"], utc=True).dt.normalize()
    panel = panel.loc[panel["date"] < pd.Timestamp("2026-01-01", tz="UTC")]
    raw = {
        symbol: group.drop(columns=["symbol", "group"]).sort_values("date")
        for symbol, group in panel.groupby("symbol")
    }
    features = {
        symbol: asset_features(frame, params) for symbol, frame in raw.items()
    }
    features = {
        symbol: add_residual(symbol, frame, features)
        for symbol, frame in features.items()
    }
    master = pd.DataFrame({"date": features["SPY"]["date"]})
    fields = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "ema20",
        "ema60",
        "realized_vol20",
        "momentum20",
        "absolute_trend",
        "vol_ratio",
        "risk_quality",
        "atr",
        "adx",
        "volume_median30",
        "channel_high_long",
        "channel_low_long",
        "channel_low_short",
        "channel_low_exit",
        "channel_high_exit",
        "recent_long_pullback",
        "recent_short_pullback",
        "previous_close",
        "residual_momentum",
    ]
    aligned_parts: list[pd.DataFrame] = []
    for symbol, frame in features.items():
        prefix = symbol.lower()
        available = frame[["date", *fields]].copy()
        available["source_date"] = available["date"]
        renamed = {field: f"{prefix}_{field}" for field in fields}
        available = available.rename(columns=renamed)
        aligned = pd.merge_asof(
            master,
            available.sort_values("date"),
            on="date",
            direction="backward",
            tolerance=pd.Timedelta(days=3),
        )
        aligned[f"{prefix}_fresh"] = aligned["date"].eq(aligned["source_date"])
        aligned = aligned.drop(columns=["source_date"])
        aligned_parts.append(aligned.drop(columns=["date"]).reset_index(drop=True))
    result = pd.concat([master.reset_index(drop=True), *aligned_parts], axis=1)

    group_dispersion: dict[str, pd.Series] = {}
    group_quality: dict[str, pd.Series] = {}
    for group, symbols in GROUP_ASSETS.items():
        directions = result[
            [f"{symbol.lower()}_absolute_trend" for symbol in symbols]
        ]
        momentum = result[[f"{symbol.lower()}_momentum20" for symbol in symbols]]
        qualities = result[
            [f"{symbol.lower()}_risk_quality" for symbol in symbols]
        ]
        valid = directions.notna().sum(axis=1)
        breadth = np.sign(directions).mean(axis=1, skipna=True)
        result[f"{group}_direction"] = (
            directions.mean(axis=1, skipna=True) * 0.75 + breadth * 0.25
        ).clip(-1.0, 1.0)
        result[f"valid_{group}"] = valid
        group_dispersion[group] = momentum.std(axis=1, skipna=True).where(valid > 0)
        group_quality[group] = qualities.min(axis=1, skipna=True).where(valid > 0)
        threshold = group_dispersion[group].rolling(
            252, min_periods=60
        ).quantile(0.95).shift(1)
        group_quality[group] = group_quality[group].where(
            ~(group_dispersion[group].gt(threshold) & threshold.notna()),
            group_quality[group] * 0.5,
        )

    complete = (
        result["qqq_absolute_trend"].notna()
        & result["btc_absolute_trend"].notna()
        & result["eth_absolute_trend"].notna()
    )
    for group, minimum in MIN_VALID.items():
        complete &= result[f"valid_{group}"] >= minimum
    result["data_complete"] = complete
    result["beta_direction_score"] = sum(
        result[f"{group}_direction"] * weight
        for group, weight in GROUP_WEIGHT.items()
    )
    result["risk_quality"] = sum(
        group_quality[group].fillna(0.0) * weight
        for group, weight in GROUP_WEIGHT.items()
    ).clip(0.0, 1.0)
    result.loc[~complete, "risk_quality"] = 0.0
    result["beta_regime"] = regimes_with_hysteresis(
        result["beta_direction_score"], complete
    )
    correlation_columns: dict[str, pd.Series] = {}
    v6_symbols = [
        symbol
        for symbol in TRADABLE
        if GROUPS[symbol] != "memory"
    ]
    daily_returns = {
        symbol: np.log(
            pd.to_numeric(
                result[f"{symbol.lower()}_close"],
                errors="coerce",
            )
        ).diff()
        for symbol in v6_symbols
    }
    for left, right in combinations(v6_symbols, 2):
        correlation_columns[correlation_column(left, right)] = (
            daily_returns[left]
            .rolling(60, min_periods=40)
            .corr(daily_returns[right])
        )
    result = pd.concat(
        [result, pd.DataFrame(correlation_columns, index=result.index)],
        axis=1,
    )
    return result


def correlation_column(left: str, right: str) -> str:
    first, second = sorted((left.lower(), right.lower()))
    return f"correlation_{first}_{second}"


def candidate_score(row: pd.Series, symbol: str, model: str) -> float:
    prefix = symbol.lower()
    direction = float(row["beta_direction_score"])
    absolute = float(row[f"{prefix}_absolute_trend"])
    residual = float(row[f"{prefix}_residual_momentum"])
    is_long = model in {"trend_long", "continuation_long"}
    regime_score = (direction + 1) * 50 if is_long else (1 - direction) * 50
    absolute_score = (absolute + 1) * 50 if is_long else (1 - absolute) * 50
    residual_score = (residual + 1) * 50 if is_long else (1 - residual) * 50
    close = float(row[f"{prefix}_close"])
    atr = float(row[f"{prefix}_atr"])
    if model == "trend_long":
        raw_trigger = close - float(row[f"{prefix}_channel_high_long"])
    elif model == "systemic_short":
        raw_trigger = float(row[f"{prefix}_channel_low_short"]) - close
    elif model == "residual_short":
        raw_trigger = float(row[f"{prefix}_channel_low_long"]) - close
    elif model == "continuation_long":
        raw_trigger = close - float(row[f"{prefix}_previous_close"])
    else:
        raw_trigger = float(row[f"{prefix}_previous_close"]) - close
    trigger = float(np.clip(raw_trigger / atr, 0, 2) * 50)
    adx_score = float(np.clip((float(row[f"{prefix}_adx"]) - 20) / 30, 0, 1) * 100)
    volume_ratio = float(row[f"{prefix}_volume"]) / float(
        row[f"{prefix}_volume_median30"]
    )
    confirmation = adx_score * 0.65 + float(np.clip(volume_ratio, 0, 2)) * 17.5
    execution = float(np.clip(volume_ratio, 0, 2) * 50)
    return float(
        regime_score * 0.20
        + absolute_score * 0.25
        + residual_score * 0.25
        + trigger * 0.15
        + confirmation * 0.10
        + execution * 0.05
    )


def daily_candidates(
    row: pd.Series,
    params: ResearchParameters,
    options: SimulationOptions,
) -> list[dict[str, Any]]:
    if not bool(row["data_complete"]) or float(row["risk_quality"]) <= 0:
        return []
    regime = str(row["beta_regime"])
    residual_by_group: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for symbol in TRADABLE:
        value = row.get(f"{symbol.lower()}_residual_momentum")
        if pd.notna(value):
            residual_by_group[GROUPS[symbol]].append((symbol, float(value)))
    bottom: set[str] = set()
    for values in residual_by_group.values():
        count = max(1, int(math.ceil(len(values) * 0.20)))
        bottom.update(symbol for symbol, _ in sorted(values, key=lambda item: item[1])[:count])

    candidates: list[dict[str, Any]] = []
    for symbol in TRADABLE:
        if symbol == "BTC" and not options.include_btc:
            continue
        if GROUPS[symbol] == "crypto" and not options.include_crypto:
            continue
        if GROUPS[symbol] == "memory" and not options.include_memory:
            continue
        prefix = symbol.lower()
        values = [
            row.get(f"{prefix}_{field}")
            for field in (
                "close",
                "volume",
                "volume_median30",
                "ema20",
                "ema60",
                "atr",
                "adx",
                "absolute_trend",
                "residual_momentum",
            )
        ]
        if not bool(row.get(f"{prefix}_fresh", False)) or any(pd.isna(v) for v in values):
            continue
        close = float(row[f"{prefix}_close"])
        ema20 = float(row[f"{prefix}_ema20"])
        ema60 = float(row[f"{prefix}_ema60"])
        atr = float(row[f"{prefix}_atr"])
        absolute = float(row[f"{prefix}_absolute_trend"])
        residual = float(row[f"{prefix}_residual_momentum"])
        adx_value = float(row[f"{prefix}_adx"])
        volume_ok = float(row[f"{prefix}_volume"]) >= float(
            row[f"{prefix}_volume_median30"]
        )
        if not volume_ok or atr <= 0:
            continue
        models: list[tuple[str, str]] = []
        if (
            regime in {"risk_on", "strong_risk_on"}
            and absolute >= params.absolute_threshold
            and close > float(row[f"{prefix}_channel_high_long"])
            and ema20 > ema60
            and adx_value >= params.long_adx
        ):
            models.append(("trend_long", "long"))
        if (
            options.model_set == "v5"
            and regime in {"risk_on", "strong_risk_on"}
            and absolute >= params.absolute_threshold
            and bool(row[f"{prefix}_recent_long_pullback"])
            and close > float(row[f"{prefix}_previous_close"])
            and close > ema20
            and close <= float(row[f"{prefix}_channel_high_long"])
            and close <= ema20 + atr * 2.5
            and ema20 > ema60
            and adx_value >= params.long_adx
        ):
            models.append(("continuation_long", "long"))
        if options.side_set == "dual":
            if (
                regime in {"risk_off", "strong_risk_off"}
                and absolute <= -params.absolute_threshold
                and close < float(row[f"{prefix}_channel_low_short"])
                and ema20 < ema60
                and adx_value >= params.short_adx
            ):
                models.append(("systemic_short", "short"))
            if (
                options.model_set == "v5"
                and regime in {"risk_off", "strong_risk_off"}
                and absolute <= -params.absolute_threshold
                and bool(row[f"{prefix}_recent_short_pullback"])
                and close < float(row[f"{prefix}_previous_close"])
                and close < ema20
                and close >= float(row[f"{prefix}_channel_low_short"])
                and close >= ema20 - atr * 2.5
                and ema20 < ema60
                and adx_value >= params.short_adx
            ):
                models.append(("systemic_pullback_short", "short"))
            if (
                regime in {"neutral", "risk_on"}
                and absolute <= -params.absolute_threshold
                and residual <= params.residual_threshold
                and symbol in bottom
                and close < float(row[f"{prefix}_channel_low_long"])
                and ema20 < ema60
                and adx_value >= params.short_adx
            ):
                models.append(("residual_short", "short"))
        for model, side in models:
            candidates.append(
                {
                    "symbol": symbol,
                    "group": GROUPS[symbol],
                    "model": model,
                    "side": side,
                    "score": candidate_score(row, symbol, model),
                    "regime": regime,
                    "risk_quality": min(
                        float(row["risk_quality"]),
                        float(row[f"{prefix}_risk_quality"]),
                    ),
                    "atr": atr,
                    "signal_close": close,
                    "volatility": float(row[f"{prefix}_realized_vol20"]),
                    "liquidity": float(row[f"{prefix}_volume"])
                    / float(row[f"{prefix}_volume_median30"]),
                }
            )
    return candidates


def select_candidates(
    candidates: list[dict[str, Any]],
    max_slots: int,
    *,
    row: pd.Series | None = None,
    profile: Literal["v5", "v6"] = "v5",
    open_symbols: tuple[str, ...] = (),
    max_pair_correlation: float = 0.90,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    if profile == "v6":
        group_cap = {
            "benchmark": 1,
            "mag7": 2,
            "memory": 0,
            "crypto": 1,
        }
        open_set = set(open_symbols)
        group_count = {group: 0 for group in group_cap}
        for symbol in open_set:
            group_count[GROUPS[symbol]] += 1
        remaining = max(0, max_slots - len(open_set))
        ordered = sorted(
            (
                candidate
                for candidate in candidates
                if candidate["side"] == "long"
                and candidate["group"] != "memory"
            ),
            key=lambda item: (
                -float(item["score"]),
                0 if item["symbol"] == "QQQ" else 1 if item["symbol"] == "BTC" else 2,
                0 if item["model"] == "trend_long" else 1,
                float(item["volatility"]),
                item["symbol"],
            ),
        )
        selected: list[dict[str, Any]] = []
        selected_symbols: set[str] = set()
        for candidate in ordered:
            symbol = candidate["symbol"]
            group = candidate["group"]
            if (
                symbol in open_set
                or symbol in selected_symbols
                or group_count[group] >= group_cap[group]
            ):
                continue
            references = [*open_symbols, *selected_symbols]
            correlations = [
                row.get(correlation_column(symbol, reference))
                if row is not None
                else np.nan
                for reference in references
            ]
            if any(
                pd.isna(value)
                or abs(float(value)) > max_pair_correlation
                for value in correlations
            ):
                continue
            selected.append(candidate)
            selected_symbols.add(symbol)
            group_count[group] += 1
            if len(selected) >= remaining:
                break
        return selected
    systemic = [
        item
        for item in candidates
        if item["model"] in {"systemic_short", "systemic_pullback_short"}
    ]
    valid = systemic if systemic else candidates
    if not systemic:
        longs = [item for item in valid if item["side"] == "long"]
        residual = [item for item in valid if item["model"] == "residual_short"]
        if longs and residual:
            best_long = max(float(item["score"]) for item in longs)
            residual = [
                item
                for item in residual
                if item["regime"] != "risk_on"
                or float(item["score"]) >= best_long + 10
            ]
            valid = longs + residual
    pair_preference = {"QQQ": 0, "BTC": 1}
    model_preference = {
        "trend_long": 0,
        "continuation_long": 1,
        "systemic_short": 0,
        "systemic_pullback_short": 1,
        "residual_short": 2,
    }
    return sorted(
        valid,
        key=lambda item: (
            -float(item["score"]),
            pair_preference.get(item["symbol"], 2),
            model_preference.get(item["model"], 3),
            float(item["volatility"]),
            item["symbol"],
        ),
    )[:max_slots]


def model_risk(model: str, regime: str, group: str) -> float:
    if model == "trend_long":
        risk = 0.0075 if regime == "strong_risk_on" else 0.0050
    elif model == "continuation_long":
        risk = 0.0050 if regime == "strong_risk_on" else 0.00375
    elif model == "systemic_short":
        risk = 0.00375
    else:
        risk = 0.0025
    return min(risk, 0.0050) if group == "crypto" else risk


def stop_bounds(group: str) -> tuple[float, float]:
    return (0.025, 0.080) if group == "crypto" else (0.025, 0.060)


def close_position(
    position: dict[str, Any],
    exit_date: pd.Timestamp,
    exit_price: float,
    reason: str,
    balance: float,
    fee: float,
    carry_bps: float,
) -> tuple[float, dict[str, Any]]:
    days = max(1.0, (exit_date - position["entry_date"]).total_seconds() / 86400)
    amount = position["amount"]
    gross = (
        amount * (position["entry_price"] - exit_price)
        if position["side"] == "short"
        else amount * (exit_price - position["entry_price"])
    )
    exit_fee = abs(amount * exit_price) * fee
    carry = position["notional"] * carry_bps / 10000 * days
    net = gross - position["entry_fee"] - exit_fee - carry
    balance += gross - exit_fee - carry
    risk_amount = position["risk_amount"]
    mfe_net = max(0.0, position["mfe_gross"] - position["entry_fee"])
    trade = {
        "symbol": position["symbol"],
        "group": position["group"],
        "side": position["side"],
        "model": position["model"],
        "signal_date": position["signal_date"].isoformat(),
        "entry_date": position["entry_date"].isoformat(),
        "exit_date": exit_date.isoformat(),
        "entry_price": position["entry_price"],
        "exit_price": exit_price,
        "stake": position["stake"],
        "leverage": position["leverage"],
        "risk_amount": risk_amount,
        "profit_abs": net,
        "r_multiple": net / risk_amount if risk_amount > 0 else None,
        "mfe_r": position["mfe_gross"] / risk_amount if risk_amount > 0 else None,
        "mae_r": position["mae_gross"] / risk_amount if risk_amount > 0 else None,
        "mfe_capture": net / mfe_net if mfe_net > 0 and net > 0 else None,
        "holding_days": days,
        "exit_reason": reason,
        "entry_fee": position["entry_fee"],
        "exit_fee": exit_fee,
        "carry_cost": carry,
        "balance_before": position["balance_before"],
        "balance_after": balance,
    }
    return balance, trade


def summarize(
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    options: SimulationOptions,
    candidate_count: Counter,
    rejected_by_slot: Counter,
    panel: pd.DataFrame,
) -> dict[str, Any]:
    profits = [float(trade["profit_abs"]) for trade in trades]
    winners = [value for value in profits if value > 0]
    losers = [value for value in profits if value < 0]
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    final_balance = equity_curve[-1]["equity"] if equity_curve else options.initial_balance
    curve = pd.DataFrame(equity_curve)
    if curve.empty:
        max_drawdown = 0.0
        sharpe = 0.0
    else:
        running_max = curve["equity"].cummax()
        drawdown = 1 - curve["equity"] / running_max.replace(0, np.nan)
        max_drawdown = float(drawdown.max(skipna=True) or 0.0)
        daily_return = curve["equity"].pct_change().fillna(0.0)
        sharpe = (
            float(daily_return.mean() / daily_return.std() * np.sqrt(252))
            if daily_return.std() > 0
            else 0.0
        )

    def buckets(
        field: str,
        key_transform: Any | None = None,
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        key_for = key_transform or (lambda value: str(value))
        keyed = [(key_for(trade[field]), trade) for trade in trades]
        for key in sorted({key for key, _ in keyed}):
            values = [trade for item_key, trade in keyed if item_key == key]
            result[key] = {
                "trades": len(values),
                "profit_abs": sum(float(value["profit_abs"]) for value in values),
                "wins": sum(float(value["profit_abs"]) > 0 for value in values),
            }
        return result

    top_five_removed = sum(sorted(profits, reverse=True)[5:]) if profits else 0.0
    qqq = panel.loc[
        (panel["date"] >= pd.Timestamp(options.start, tz="UTC"))
        & (panel["date"] <= pd.Timestamp(options.end, tz="UTC"))
        & panel["qqq_fresh"]
    ]
    qqq_return = (
        float(qqq["qqq_close"].iloc[-1] / qqq["qqq_open"].iloc[0] - 1)
        if len(qqq) > 1
        else None
    )
    r_values = [
        float(trade["r_multiple"])
        for trade in trades
        if trade["r_multiple"] is not None
    ]
    captures = [
        float(trade["mfe_capture"])
        for trade in trades
        if trade["mfe_capture"] is not None
    ]
    return {
        "trades": len(trades),
        "final_balance": final_balance,
        "profit_abs": final_balance - options.initial_balance,
        "return_pct": (final_balance / options.initial_balance - 1) * 100,
        "win_rate": len(winners) / len(trades) if trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "average_win": np.mean(winners) if winners else 0.0,
        "average_loss": np.mean(losers) if losers else 0.0,
        "payoff_ratio": (
            np.mean(winners) / abs(np.mean(losers)) if winners and losers else None
        ),
        "max_drawdown_pct": max_drawdown * 100,
        "sharpe_daily": sharpe,
        "r_expectancy": float(np.mean(r_values)) if r_values else None,
        "median_winner_mfe_capture": (
            float(np.median(captures)) if captures else None
        ),
        "profit_without_top_5": top_five_removed,
        "fee_abs": sum(
            float(trade["entry_fee"]) + float(trade["exit_fee"]) for trade in trades
        ),
        "carry_abs": sum(float(trade["carry_cost"]) for trade in trades),
        "qqq_buy_hold_pct": qqq_return * 100 if qqq_return is not None else None,
        "by_side": buckets("side"),
        "by_model": buckets("model"),
        "by_group": buckets("group"),
        "by_symbol": buckets("symbol"),
        "by_exit": buckets("exit_reason"),
        "by_year": buckets(
            "exit_date",
            lambda value: str(pd.Timestamp(value).year),
        ),
        "candidate_signals": dict(candidate_count),
        "rejected_by_slot": dict(rejected_by_slot),
    }


def monte_carlo(trades: list[dict[str, Any]], paths: int = 5000) -> dict[str, Any]:
    if not trades:
        return {"paths": paths, "drawdown_p95_pct": None, "loss_probability": None}
    returns = np.array(
        [
            float(trade["profit_abs"]) / float(trade["balance_before"])
            for trade in trades
            if float(trade["balance_before"]) > 0
        ]
    )
    rng = np.random.default_rng(42)
    drawdowns = []
    terminal = []
    for _ in range(paths):
        sampled = rng.choice(returns, size=len(returns), replace=True)
        curve = np.cumprod(1 + sampled)
        peaks = np.maximum.accumulate(np.r_[1.0, curve])
        values = np.r_[1.0, curve]
        drawdowns.append(float(np.max(1 - values / peaks)))
        terminal.append(float(curve[-1] - 1))
    return {
        "paths": paths,
        "drawdown_p95_pct": float(np.quantile(drawdowns, 0.95) * 100),
        "loss_probability": float(np.mean(np.array(terminal) < 0)),
        "terminal_return_median_pct": float(np.median(terminal) * 100),
    }


def simulate(
    panel: pd.DataFrame,
    params: ResearchParameters,
    options: SimulationOptions,
) -> dict[str, Any]:
    fee = options.fee_rate * options.cost_multiplier
    slippage = options.slippage_rate * options.cost_multiplier
    carry_bps = options.carry_bps_per_day * options.cost_multiplier
    start = pd.Timestamp(options.start, tz="UTC")
    end = pd.Timestamp(options.end, tz="UTC")
    balance = options.initial_balance
    positions: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    candidate_count: Counter = Counter()
    rejected_by_slot: Counter = Counter()

    for _, row in panel.iterrows():
        date = row["date"]
        if date < start:
            pending = []
            continue
        if date > end:
            break

        for symbol, position in list(positions.items()):
            prefix = symbol.lower()
            if not bool(row.get(f"{prefix}_fresh", False)):
                continue
            open_price = float(row[f"{prefix}_open"])
            if position.get("pending_exit"):
                raw_exit = open_price
                exit_price = raw_exit * (
                    1 + slippage if position["side"] == "short" else 1 - slippage
                )
                balance, trade = close_position(
                    position,
                    date,
                    exit_price,
                    str(position["pending_exit"]),
                    balance,
                    fee,
                    carry_bps,
                )
                trades.append(trade)
                del positions[symbol]

        available_margin = max(
            0.0,
            balance - 15.0 - sum(float(pos["stake"]) for pos in positions.values()),
        )
        for candidate in pending:
            symbol = candidate["symbol"]
            prefix = symbol.lower()
            if (
                len(positions) >= options.max_slots
                or symbol in positions
                or not bool(row.get(f"{prefix}_fresh", False))
            ):
                rejected_by_slot[candidate["model"]] += 1
                continue
            leverage = 3.0 if candidate["group"] == "crypto" else 2.0
            raw_open = float(row[f"{prefix}_open"])
            entry_price = raw_open * (
                1 - slippage if candidate["side"] == "short" else 1 + slippage
            )
            low_stop, high_stop = stop_bounds(candidate["group"])
            stop_distance = float(
                np.clip(2.0 * candidate["atr"] / candidate["signal_close"], low_stop, high_stop)
            )
            risk_fraction = model_risk(
                candidate["model"], candidate["regime"], candidate["group"]
            ) * max(0.0, min(1.0, candidate["risk_quality"]))
            risk_budget = (
                balance * risk_fraction * max(0.0, options.portfolio_risk_scale)
            )
            if options.aggregate_risk_cap is not None:
                open_risk = sum(
                    float(position["risk_amount"])
                    for position in positions.values()
                )
                remaining_risk = max(
                    0.0,
                    balance * options.aggregate_risk_cap - open_risk,
                )
                risk_budget = min(risk_budget, remaining_risk)
            stake = min(
                risk_budget / (stop_distance * leverage),
                5.0,
                available_margin,
            )
            if stake <= 0:
                rejected_by_slot["insufficient_margin"] += 1
                continue
            notional = stake * leverage
            amount = notional / entry_price
            entry_fee = notional * fee
            balance_before = balance
            balance -= entry_fee
            available_margin -= stake
            initial_stop = entry_price * (
                1 + stop_distance
                if candidate["side"] == "short"
                else 1 - stop_distance
            )
            positions[symbol] = {
                **candidate,
                "entry_date": date,
                "entry_price": entry_price,
                "stake": stake,
                "leverage": leverage,
                "notional": notional,
                "amount": amount,
                "entry_fee": entry_fee,
                "risk_amount": notional * stop_distance,
                "initial_stop": initial_stop,
                "stop": initial_stop,
                "extreme": entry_price,
                "mfe_gross": 0.0,
                "mae_gross": 0.0,
                "sessions": 0,
                "pending_exit": None,
                "balance_before": balance_before,
            }

        for symbol, position in list(positions.items()):
            prefix = symbol.lower()
            if not bool(row.get(f"{prefix}_fresh", False)):
                continue
            high = float(row[f"{prefix}_high"])
            low = float(row[f"{prefix}_low"])
            open_price = float(row[f"{prefix}_open"])
            stop = float(position["stop"])
            stopped = False
            if position["side"] == "long":
                if open_price <= stop:
                    exit_price = open_price * (1 - slippage)
                    stopped = True
                elif low <= stop:
                    exit_price = stop * (1 - slippage)
                    stopped = True
            else:
                if open_price >= stop:
                    exit_price = open_price * (1 + slippage)
                    stopped = True
                elif high >= stop:
                    exit_price = stop * (1 + slippage)
                    stopped = True
            if stopped:
                balance, trade = close_position(
                    position, date, exit_price, "initial_or_trailing_stop", balance, fee, carry_bps
                )
                trades.append(trade)
                del positions[symbol]
                continue

            amount = position["amount"]
            if position["side"] == "long":
                favorable = amount * (high - position["entry_price"])
                adverse = amount * (low - position["entry_price"])
                position["extreme"] = max(float(position["extreme"]), high)
            else:
                favorable = amount * (position["entry_price"] - low)
                adverse = amount * (position["entry_price"] - high)
                position["extreme"] = min(float(position["extreme"]), low)
            position["mfe_gross"] = max(float(position["mfe_gross"]), favorable)
            position["mae_gross"] = min(float(position["mae_gross"]), adverse)
            position["sessions"] += 1
            mfe_r = position["mfe_gross"] / position["risk_amount"]
            atr_value = float(row[f"{prefix}_atr"])
            next_stop = float(position["stop"])
            if options.exit_policy == "delayed_break_even" and mfe_r >= 1.5:
                break_even = position["entry_price"] * (
                    1 - (position["initial_stop"] / position["entry_price"] - 1) * 0.25
                    if position["side"] == "short"
                    else 1 + (1 - position["initial_stop"] / position["entry_price"]) * 0.25
                )
                next_stop = (
                    min(next_stop, break_even)
                    if position["side"] == "short"
                    else max(next_stop, break_even)
                )
                if mfe_r >= 3.0:
                    trail = (
                        position["extreme"] + atr_value * 4.0
                        if position["side"] == "short"
                        else position["extreme"] - atr_value * 4.0
                    )
                    next_stop = (
                        min(next_stop, trail)
                        if position["side"] == "short"
                        else max(next_stop, trail)
                    )
            elif options.exit_policy == "slow_atr" and mfe_r >= 2.0:
                trail = (
                    position["extreme"] + atr_value * 4.5
                    if position["side"] == "short"
                    else position["extreme"] - atr_value * 4.5
                )
                next_stop = (
                    min(next_stop, trail)
                    if position["side"] == "short"
                    else max(next_stop, trail)
                )
            position["stop"] = next_stop
            close = float(row[f"{prefix}_close"])
            channel_exit = (
                close > float(row[f"{prefix}_channel_high_exit"])
                or float(row[f"{prefix}_ema20"]) > float(row[f"{prefix}_ema60"])
                if position["side"] == "short"
                else close < float(row[f"{prefix}_channel_low_exit"])
                or float(row[f"{prefix}_ema20"]) < float(row[f"{prefix}_ema60"])
            )
            if channel_exit:
                position["pending_exit"] = "channel_exit"
            elif (
                position["sessions"] >= params.time_stop_sessions and mfe_r < 0.5
            ):
                position["pending_exit"] = "time_stop"

        candidates = daily_candidates(row, params, options)
        candidate_count.update(candidate["model"] for candidate in candidates)
        pending = select_candidates(
            candidates,
            options.max_slots,
            row=row,
            profile=options.portfolio_profile,
            open_symbols=tuple(sorted(positions)),
            max_pair_correlation=options.max_pair_correlation,
        )
        for candidate in pending:
            candidate["signal_date"] = date

        equity = balance
        for position in positions.values():
            prefix = position["symbol"].lower()
            if pd.isna(row.get(f"{prefix}_close")):
                continue
            mark = float(row[f"{prefix}_close"])
            unrealized = (
                position["amount"] * (position["entry_price"] - mark)
                if position["side"] == "short"
                else position["amount"] * (mark - position["entry_price"])
            )
            days = max(1.0, (date - position["entry_date"]).total_seconds() / 86400)
            equity += (
                unrealized
                - abs(position["amount"] * mark) * fee
                - position["notional"] * carry_bps / 10000 * days
            )
        equity_curve.append({"date": date.isoformat(), "equity": equity})

    if not panel.empty:
        final_row = panel.loc[panel["date"] <= end].iloc[-1]
        final_date = final_row["date"]
        for symbol, position in list(positions.items()):
            close = float(final_row[f"{symbol.lower()}_close"])
            exit_price = close * (
                1 + slippage if position["side"] == "short" else 1 - slippage
            )
            balance, trade = close_position(
                position, final_date, exit_price, "force_exit", balance, fee, carry_bps
            )
            trades.append(trade)
        if equity_curve:
            equity_curve[-1]["equity"] = balance

    summary = summarize(
        trades, equity_curve, options, candidate_count, rejected_by_slot, panel
    )
    return {
        "options": asdict(options),
        "parameters": asdict(params),
        "summary": summary,
        "monte_carlo": monte_carlo(trades),
        "trades": trades,
        "equity_curve": equity_curve,
    }


def compact(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "options": result["options"],
        "parameters": result["parameters"],
        "summary": result["summary"],
        "monte_carlo": result["monte_carlo"],
    }


def run_suite(panel: pd.DataFrame, params: ResearchParameters) -> dict[str, Any]:
    def run(**kwargs: Any) -> dict[str, Any]:
        options = SimulationOptions(start="2018-01-01", end="2023-12-31", **kwargs)
        return simulate(panel, params, options)

    baseline = run()
    folds = {}
    for test_year in (2021, 2022, 2023):
        options = SimulationOptions(
            start=f"{test_year}-01-01", end=f"{test_year}-12-31"
        )
        folds[str(test_year)] = compact(simulate(panel, params, options))
    validation = compact(
        simulate(
            panel,
            params,
            SimulationOptions(start="2024-01-01", end="2025-12-31"),
        )
    )
    validation_years = {
        str(year): compact(
            simulate(
                panel,
                params,
                SimulationOptions(start=f"{year}-01-01", end=f"{year}-12-31"),
            )
        )
        for year in (2024, 2025)
    }
    validation_ablations = {
        "breakout_only": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    model_set="breakout_only",
                ),
            )
        ),
        "long_only": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    side_set="long_only",
                ),
            )
        ),
        "long_breakout_only": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    model_set="breakout_only",
                    side_set="long_only",
                ),
            )
        ),
        "three_slots": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    max_slots=3,
                ),
            )
        ),
        "long_only_three_slots": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    max_slots=3,
                    side_set="long_only",
                ),
            )
        ),
        "two_slots_equal_total_risk": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    max_slots=2,
                    portfolio_risk_scale=0.5,
                ),
            )
        ),
        "three_slots_equal_total_risk": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    max_slots=3,
                    portfolio_risk_scale=1 / 3,
                ),
            )
        ),
        "long_only_three_slots_equal_total_risk": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    max_slots=3,
                    side_set="long_only",
                    portfolio_risk_scale=1 / 3,
                ),
            )
        ),
        "exclude_memory": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    include_memory=False,
                ),
            )
        ),
    }
    validation_diagnostics = {
        "long_breakout_only_by_year": {
            str(year): compact(
                simulate(
                    panel,
                    params,
                    SimulationOptions(
                        start=f"{year}-01-01",
                        end=f"{year}-12-31",
                        model_set="breakout_only",
                        side_set="long_only",
                    ),
                )
            )
            for year in (2024, 2025)
        },
        "long_only_three_slots_equal_total_risk_by_year": {
            str(year): compact(
                simulate(
                    panel,
                    params,
                    SimulationOptions(
                        start=f"{year}-01-01",
                        end=f"{year}-12-31",
                        max_slots=3,
                        side_set="long_only",
                        portfolio_risk_scale=1 / 3,
                    ),
                )
            )
            for year in (2024, 2025)
        },
        "long_only_three_slots_equal_total_risk_cost_stress": {
            multiplier: compact(
                simulate(
                    panel,
                    params,
                    SimulationOptions(
                        start="2024-01-01",
                        end="2025-12-31",
                        max_slots=3,
                        side_set="long_only",
                        portfolio_risk_scale=1 / 3,
                        cost_multiplier=float(multiplier),
                    ),
                )
            )
            for multiplier in (1.0, 1.5, 2.0)
        },
        "v6_contract": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    max_slots=3,
                    side_set="long_only",
                    include_memory=False,
                    portfolio_risk_scale=1 / 3,
                    portfolio_profile="v6",
                ),
            )
        ),
        "v6_contract_by_year": {
            str(year): compact(
                simulate(
                    panel,
                    params,
                    SimulationOptions(
                        start=f"{year}-01-01",
                        end=f"{year}-12-31",
                        max_slots=3,
                        side_set="long_only",
                        include_memory=False,
                        portfolio_risk_scale=1 / 3,
                        portfolio_profile="v6",
                    ),
                )
            )
            for year in (2024, 2025)
        },
        "v6_contract_cost_stress": {
            multiplier: compact(
                simulate(
                    panel,
                    params,
                    SimulationOptions(
                        start="2024-01-01",
                        end="2025-12-31",
                        max_slots=3,
                        side_set="long_only",
                        include_memory=False,
                        portfolio_risk_scale=1 / 3,
                        portfolio_profile="v6",
                        cost_multiplier=float(multiplier),
                    ),
                )
            )
            for multiplier in (1.0, 1.5, 2.0)
        },
        "v6_wallet_80": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    max_slots=3,
                    side_set="long_only",
                    include_memory=False,
                    portfolio_risk_scale=1 / 3,
                    portfolio_profile="v6",
                    initial_balance=80.0,
                ),
            )
        ),
        "v7_wallet_80": compact(
            simulate(
                panel,
                params,
                SimulationOptions(
                    start="2024-01-01",
                    end="2025-12-31",
                    max_slots=3,
                    side_set="long_only",
                    include_memory=False,
                    portfolio_risk_scale=0.5,
                    portfolio_profile="v6",
                    aggregate_risk_cap=0.0075,
                    initial_balance=80.0,
                ),
            )
        ),
        "v6_wallet_80_by_year": {
            str(year): compact(
                simulate(
                    panel,
                    params,
                    SimulationOptions(
                        start=f"{year}-01-01",
                        end=f"{year}-12-31",
                        max_slots=3,
                        side_set="long_only",
                        include_memory=False,
                        portfolio_risk_scale=1 / 3,
                        portfolio_profile="v6",
                        initial_balance=80.0,
                    ),
                )
            )
            for year in (2024, 2025)
        },
        "v6_robustness": {
            "breakout_only": compact(
                simulate(
                    panel,
                    params,
                    SimulationOptions(
                        start="2024-01-01",
                        end="2025-12-31",
                        max_slots=3,
                        model_set="breakout_only",
                        side_set="long_only",
                        include_memory=False,
                        portfolio_risk_scale=1 / 3,
                        portfolio_profile="v6",
                    ),
                )
            ),
            "exclude_crypto": compact(
                simulate(
                    panel,
                    params,
                    SimulationOptions(
                        start="2024-01-01",
                        end="2025-12-31",
                        max_slots=3,
                        side_set="long_only",
                        include_crypto=False,
                        include_memory=False,
                        portfolio_risk_scale=1 / 3,
                        portfolio_profile="v6",
                    ),
                )
            ),
            "two_slots_equal_total_risk": compact(
                simulate(
                    panel,
                    params,
                    SimulationOptions(
                        start="2024-01-01",
                        end="2025-12-31",
                        max_slots=2,
                        side_set="long_only",
                        include_memory=False,
                        portfolio_risk_scale=0.5,
                        portfolio_profile="v6",
                    ),
                )
            ),
            **{
                f"correlation_{threshold:.2f}": compact(
                    simulate(
                        panel,
                        params,
                        SimulationOptions(
                            start="2024-01-01",
                            end="2025-12-31",
                            max_slots=3,
                            side_set="long_only",
                            include_memory=False,
                            portfolio_risk_scale=1 / 3,
                            portfolio_profile="v6",
                            max_pair_correlation=threshold,
                        ),
                    )
                )
                for threshold in (0.85, 0.90, 0.95)
            },
        },
    }
    ablations = {
        "breakout_only": compact(run(model_set="breakout_only")),
        "long_only": compact(run(side_set="long_only")),
        "three_slots": compact(run(max_slots=3)),
        "three_slots_equal_total_risk": compact(
            run(max_slots=3, portfolio_risk_scale=1 / 3)
        ),
        "long_only_three_slots_equal_total_risk": compact(
            run(
                max_slots=3,
                side_set="long_only",
                portfolio_risk_scale=1 / 3,
            )
        ),
        "v6_contract": compact(
            run(
                max_slots=3,
                side_set="long_only",
                include_memory=False,
                portfolio_risk_scale=1 / 3,
                portfolio_profile="v6",
            )
        ),
        "v6_wallet_80": compact(
            run(
                max_slots=3,
                side_set="long_only",
                include_memory=False,
                portfolio_risk_scale=1 / 3,
                portfolio_profile="v6",
                initial_balance=80.0,
            )
        ),
        "v7_wallet_80": compact(
            run(
                max_slots=3,
                side_set="long_only",
                include_memory=False,
                portfolio_risk_scale=0.5,
                portfolio_profile="v6",
                aggregate_risk_cap=0.0075,
                initial_balance=80.0,
            )
        ),
        "v6_breakout_only": compact(
            run(
                max_slots=3,
                model_set="breakout_only",
                side_set="long_only",
                include_memory=False,
                portfolio_risk_scale=1 / 3,
                portfolio_profile="v6",
            )
        ),
        "v6_exclude_crypto": compact(
            run(
                max_slots=3,
                side_set="long_only",
                include_crypto=False,
                include_memory=False,
                portfolio_risk_scale=1 / 3,
                portfolio_profile="v6",
            )
        ),
        "v6_two_slots_equal_total_risk": compact(
            run(
                max_slots=2,
                side_set="long_only",
                include_memory=False,
                portfolio_risk_scale=0.5,
                portfolio_profile="v6",
            )
        ),
        **{
            f"v6_correlation_{threshold:.2f}": compact(
                run(
                    max_slots=3,
                    side_set="long_only",
                    include_memory=False,
                    portfolio_risk_scale=1 / 3,
                    portfolio_profile="v6",
                    max_pair_correlation=threshold,
                )
            )
            for threshold in (0.85, 0.90, 0.95)
        },
        "exclude_btc": compact(run(include_btc=False)),
        "delayed_break_even": compact(run(exit_policy="delayed_break_even")),
        "slow_atr": compact(run(exit_policy="slow_atr")),
    }
    cost_stress = {
        multiplier: compact(run(cost_multiplier=float(multiplier)))
        for multiplier in (1.0, 1.5, 2.0)
    }
    perturbations = {}
    for label, absolute, long_adx_value, short_adx_value in (
        ("minus_10pct", 0.225, 18.0, 22.5),
        ("baseline", 0.25, 20.0, 25.0),
        ("plus_10pct", 0.275, 22.0, 27.5),
    ):
        changed = ResearchParameters(
            absolute_threshold=absolute,
            long_adx=long_adx_value,
            short_adx=short_adx_value,
        )
        changed_panel = build_panel_data_cache[changed]
        perturbations[label] = compact(
            simulate(
                changed_panel,
                changed,
                SimulationOptions(start="2018-01-01", end="2023-12-31"),
            )
        )
    return {
        "methodology": {
            "label": "DAILY_UNDERLYING_ECONOMIC_PROXY_NOT_OKX_EXECUTION",
            "signal_availability": "completed daily close; next-session open execution",
            "intraday_fidelity": False,
            "funding_history": "unavailable; explicit assumed daily carry only",
            "price_source": "adjusted underlying cash/spot OHLCV in USD",
            "research_period": "2018-2023",
            "frozen_validation_period": "2024-2025",
            "portfolio_risk_scale": (
                "diagnostic multiplier applied to every model risk budget"
            ),
        },
        "baseline": baseline,
        "walk_forward_test_years": folds,
        "validation": validation,
        "validation_years": validation_years,
        "validation_ablations": validation_ablations,
        "validation_diagnostics": validation_diagnostics,
        "ablations": ablations,
        "cost_stress": cost_stress,
        "parameter_perturbations": perturbations,
    }


build_panel_data_cache: dict[ResearchParameters, pd.DataFrame] = {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("panel", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    base_params = ResearchParameters()
    for params in (
        ResearchParameters(absolute_threshold=0.225, long_adx=18.0, short_adx=22.5),
        base_params,
        ResearchParameters(absolute_threshold=0.275, long_adx=22.0, short_adx=27.5),
    ):
        build_panel_data_cache[params] = build_panel(args.panel, params)
    report = run_suite(build_panel_data_cache[base_params], base_params)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=float) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "baseline": report["baseline"]["summary"],
                "validation": report["validation"]["summary"],
            },
            indent=2,
            sort_keys=True,
            default=float,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
