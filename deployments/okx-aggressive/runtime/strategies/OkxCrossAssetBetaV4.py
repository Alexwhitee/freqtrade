"""Cross-asset Beta V4 for OKX linear perpetuals.

V4 freezes V3's exchange/session/risk-plan safety controls and replaces only
the economic model. Directional evidence is centered around zero, volatility
and dispersion scale risk instead of adding bullish score, equity momentum is
residualized against a benchmark, and systemic and residual shorts are
selected in the same single-position portfolio.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame, Series

from freqtrade.persistence import Trade
from freqtrade.strategy import CategoricalParameter, informative, stoploss_from_absolute
from OkxAggressiveTrendV2 import OkxAggressiveTrendV2
from OkxCrossAssetBetaV3 import (
    ASSETS,
    ASSET_BY_PAIR,
    BETA_INPUT_PAIRS,
    TRADABLE_PAIRS,
    AssetSpec,
    OkxCrossAssetBetaV3,
    _pair,
)


logger = logging.getLogger(__name__)

RegimeV4 = Literal[
    "strong_risk_on",
    "risk_on",
    "neutral",
    "risk_off",
    "strong_risk_off",
    "blocked",
]
SignalModel = Literal["trend_long", "systemic_short", "residual_short"]
ExitPolicy = Literal["channel_only", "delayed_break_even", "slow_atr"]


@dataclass(frozen=True)
class BetaRegimeStateV4:
    updated_at: datetime
    direction_score: float
    regime: RegimeV4
    risk_quality: float
    group_direction: dict[str, float]
    valid_assets: dict[str, int]
    data_complete: bool


@dataclass(frozen=True)
class CandidateStateV4:
    pair: str
    side: Literal["long", "short"]
    signal_model: SignalModel
    absolute_trend: float
    residual_momentum: float | None
    breakout_strength: float
    confirmation: float
    execution_quality: float | None
    total_score: float


class OkxCrossAssetBetaV4(OkxCrossAssetBetaV3):
    """Centered multi-horizon Beta rotation with dual short models."""

    startup_candle_count = 1250
    exit_policy = CategoricalParameter(
        ["channel_only", "delayed_break_even", "slow_atr"],
        default="channel_only",
        space="sell",
        optimize=False,
        load=True,
    )

    _GROUP_WEIGHT = {
        "benchmark": 0.35,
        "mag7": 0.30,
        "memory": 0.20,
        "crypto": 0.15,
    }
    _MIN_VALID = {"benchmark": 1, "mag7": 5, "memory": 2, "crypto": 2}
    _MODEL_RISK = {
        "trend_long_strong": 0.0075,
        "trend_long": 0.0050,
        "systemic_short": 0.00375,
        "residual_short": 0.0025,
    }
    _CRYPTO_RISK_CAP = 0.0050
    _REGIME_HYSTERESIS = 0.05
    _REGIME_CONFIRM_BARS = 2

    plot_config = {
        "main_plot": {
            "ema30_4h": {"color": "blue"},
            "ema90_4h": {"color": "orange"},
        },
        "subplots": {
            "Direction": {"beta_direction_score": {"color": "green"}},
            "Risk": {"risk_quality": {"color": "red"}},
            "Candidate": {"candidate_score": {"color": "purple"}},
        },
    }

    @staticmethod
    def _safe_divide(numerator: Series, denominator: Series) -> Series:
        denominator = denominator.replace(0.0, np.nan)
        return numerator / denominator

    def _merge_funding_rate(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Expose a funding event only after its complete one-hour candle.

        OKX funding history is timestamped at the event boundary. Treating the
        value as available on that same timestamp makes sliced backtests depend
        on information that was not yet complete. V4 therefore delays every
        event by one funding-data candle and expires it after nine hours.
        """
        base = dataframe.copy()
        base["date"] = pd.to_datetime(base["date"], utc=True).astype(
            "datetime64[ns, UTC]"
        )
        base = base.drop(columns=["funding_rate", "funding_known"], errors="ignore")
        if self._is_backtest():
            # Freqtrade's manually requested funding dataframe is not sliced
            # with the analyzed base dataframe. Using it in research therefore
            # changes past indicator rows when the test endpoint changes.
            # Funding remains an unavailable execution-score component in
            # research, while the engine can still charge historical funding
            # as a separate cost. Reports must label this as incomplete-cost.
            base["funding_rate"] = np.nan
            return base
        try:
            funding = self.dp.get_pair_dataframe(
                pair=metadata["pair"], timeframe="1h", candle_type="funding_rate"
            )
            if funding is None or funding.empty:
                base["funding_rate"] = np.nan
                return base
            rates = funding[["date", "open"]].copy().rename(
                columns={"date": "funding_event_at", "open": "funding_rate"}
            )
            rates["funding_event_at"] = pd.to_datetime(
                rates["funding_event_at"], utc=True
            ).astype("datetime64[ns, UTC]")
            rates["funding_available_at"] = rates["funding_event_at"] + pd.Timedelta(
                hours=1
            )
            rates["funding_rate"] = pd.to_numeric(
                rates["funding_rate"], errors="coerce"
            )
            rates = (
                rates.dropna(subset=["funding_available_at", "funding_rate"])
                .drop_duplicates("funding_available_at", keep="last")
                .sort_values("funding_available_at")
            )
            if rates.empty:
                base["funding_rate"] = np.nan
                return base
            merged = pd.merge_asof(
                base.sort_values("date"),
                rates[["funding_available_at", "funding_rate"]],
                left_on="date",
                right_on="funding_available_at",
                direction="backward",
                tolerance=pd.Timedelta(hours=9),
            )
            return merged.drop(columns=["funding_available_at"])
        except Exception as exc:  # noqa: BLE001 - missing funding is unavailable evidence.
            logger.warning("V4 funding-rate data unavailable: %s", exc.__class__.__name__)
            base["funding_rate"] = np.nan
            return base

    @classmethod
    def _normalized_momentum(
        cls,
        close: Series,
        realized_vol20: Series,
        periods: int,
    ) -> Series:
        numerator = np.log(close / close.shift(periods))
        denominator = realized_vol20 * np.sqrt(periods / 252.0)
        return np.tanh(cls._safe_divide(numerator, denominator)).fillna(0.0)

    @staticmethod
    def _volatility_quality(vol_ratio: Series) -> Series:
        quality = pd.Series(0.0, index=vol_ratio.index, dtype=float)
        quality.loc[vol_ratio <= 1.25] = 1.0
        quality.loc[(vol_ratio > 1.25) & (vol_ratio <= 1.75)] = 0.75
        quality.loc[(vol_ratio > 1.75) & (vol_ratio <= 2.00)] = 0.50
        return quality

    @classmethod
    def _daily_asset_features(cls, dataframe: DataFrame) -> DataFrame:
        required = {"date", "close"}
        if dataframe is None or dataframe.empty or not required.issubset(dataframe.columns):
            return DataFrame()
        result = dataframe[["date", "close"]].copy()
        result["date"] = pd.to_datetime(
            result["date"], utc=True, errors="coerce"
        ).astype("datetime64[ns, UTC]")
        result["close"] = pd.to_numeric(result["close"], errors="coerce")
        result = result.dropna().sort_values("date").drop_duplicates("date")
        result["daily_return"] = np.log(result["close"]).diff()
        result["ema20"] = result["close"].ewm(
            span=20, adjust=False, min_periods=20
        ).mean()
        result["ema60"] = result["close"].ewm(
            span=60, adjust=False, min_periods=60
        ).mean()
        result["realized_vol20"] = (
            result["daily_return"].rolling(20).std() * np.sqrt(252)
        )
        for periods in (20, 60, 120):
            result[f"momentum{periods}"] = cls._normalized_momentum(
                result["close"], result["realized_vol20"], periods
            )
        ema_numerator = np.log(result["ema20"] / result["ema60"])
        ema_denominator = result["realized_vol20"] * np.sqrt(40 / 252.0)
        result["ema_direction"] = np.tanh(
            cls._safe_divide(ema_numerator, ema_denominator)
        ).fillna(0.0)
        seasoned = result["close"].shift(120).notna()
        result["absolute_trend"] = (
            result["momentum20"]
            + result["momentum60"]
            + result["momentum120"]
            + result["ema_direction"]
        ) / 4.0
        result.loc[~seasoned, "absolute_trend"] = np.nan

        median_vol = result["realized_vol20"].rolling(
            252, min_periods=60
        ).median().shift(1)
        vol_ratio = cls._safe_divide(result["realized_vol20"], median_vol)
        flat = (result["realized_vol20"] == 0) & (median_vol == 0)
        result["vol_ratio"] = vol_ratio.mask(flat, 1.0)
        result["risk_quality"] = cls._volatility_quality(result["vol_ratio"])
        result.loc[result["vol_ratio"].isna(), "risk_quality"] = np.nan
        result["available_at"] = result["date"] + pd.Timedelta(days=1)
        return result

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = OkxAggressiveTrendV2.populate_indicators_4h(
            self, dataframe, metadata
        )
        dataframe["ema90"] = ta.EMA(dataframe, timeperiod=90)
        for periods in (60, 90):
            dataframe[f"channel_high_{periods}"] = (
                dataframe["high"].rolling(periods).max().shift(1)
            )
            dataframe[f"channel_low_{periods}"] = (
                dataframe["low"].rolling(periods).min().shift(1)
            )
        return dataframe

    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return self._daily_asset_features(dataframe)

    def _daily_frame(self, pair: str) -> DataFrame:
        try:
            raw = self.dp.get_pair_dataframe(pair=pair, timeframe="1d")
        except Exception as exc:  # noqa: BLE001 - missing data must fail closed.
            logger.warning("V4 daily input unavailable for %s: %s", pair, exc.__class__.__name__)
            return DataFrame()
        else:
            return self._daily_asset_features(raw)

    @staticmethod
    def _residual_benchmark(pair: str) -> str | None:
        spec = ASSET_BY_PAIR.get(pair)
        if spec is None or spec.group == "crypto" or pair == _pair("SPY"):
            return None
        return _pair("SPY") if pair == _pair("QQQ") else _pair("QQQ")

    @classmethod
    def _with_residual_momentum(
        cls,
        pair: str,
        frame: DataFrame,
        frames: dict[str, DataFrame],
    ) -> DataFrame:
        result = frame.copy()
        if result.empty:
            result["residual_momentum"] = np.nan
            return result
        benchmark_pair = cls._residual_benchmark(pair)
        if benchmark_pair is None:
            result["residual_momentum"] = (
                result["momentum20"] + result["momentum60"]
            ) / 2.0
            return result
        benchmark = frames.get(benchmark_pair, DataFrame())
        if benchmark.empty:
            result["residual_momentum"] = np.nan
            return result
        aligned = result[["date", "daily_return"]].merge(
            benchmark[["date", "daily_return"]],
            on="date",
            how="left",
            suffixes=("_asset", "_benchmark"),
        )
        covariance = aligned["daily_return_asset"].rolling(
            60, min_periods=40
        ).cov(aligned["daily_return_benchmark"])
        variance = aligned["daily_return_benchmark"].rolling(
            60, min_periods=40
        ).var()
        beta = cls._safe_divide(covariance, variance)
        residual = aligned["daily_return_asset"] - beta * aligned["daily_return_benchmark"]
        identical_return = np.isclose(
            aligned["daily_return_asset"],
            aligned["daily_return_benchmark"],
            rtol=1e-10,
            atol=1e-12,
            equal_nan=False,
        )
        residual = residual.mask(identical_return, 0.0)
        residual_vol = residual.rolling(20, min_periods=15).std()
        normalized: list[Series] = []
        for periods in (20, 60):
            cumulative = residual.rolling(periods, min_periods=periods).sum()
            denominator = residual_vol * np.sqrt(periods)
            value = np.tanh(cls._safe_divide(cumulative, denominator))
            value = value.mask((cumulative == 0.0) & (denominator == 0.0), 0.0)
            normalized.append(value)
        result["residual_momentum"] = (
            normalized[0].to_numpy() + normalized[1].to_numpy()
        ) / 2.0
        return result

    @staticmethod
    def _direction_group(
        row: Series,
        pairs: list[str],
    ) -> tuple[float, int, float, float]:
        directions: list[float] = []
        momentum20: list[float] = []
        qualities: list[float] = []
        for pair in pairs:
            prefix = pair.split("/", 1)[0].lower()
            direction = row.get(f"{prefix}_absolute_trend")
            if pd.isna(direction):
                continue
            directions.append(float(direction))
            momentum = row.get(f"{prefix}_momentum20")
            if pd.notna(momentum):
                momentum20.append(float(momentum))
            quality = row.get(f"{prefix}_risk_quality")
            if pd.notna(quality):
                qualities.append(float(quality))
        if not directions:
            return 0.0, 0, np.nan, 0.0
        breadth = float(np.mean(np.sign(directions)))
        direction_score = float(np.clip(np.mean(directions) * 0.75 + breadth * 0.25, -1, 1))
        dispersion = float(np.std(momentum20)) if momentum20 else np.nan
        quality = min(qualities) if qualities else 0.0
        return direction_score, len(directions), dispersion, quality

    @staticmethod
    def _raw_regime(score: float, complete: bool) -> RegimeV4:
        if not complete or not np.isfinite(score):
            return "blocked"
        if score >= 0.50:
            return "strong_risk_on"
        if score >= 0.20:
            return "risk_on"
        if score > -0.20:
            return "neutral"
        if score > -0.50:
            return "risk_off"
        return "strong_risk_off"

    @classmethod
    def _hysteresis_candidate(cls, score: float, current: RegimeV4) -> RegimeV4:
        margin = cls._REGIME_HYSTERESIS
        if current == "strong_risk_on" and score >= 0.50 - margin:
            return current
        if current == "risk_on":
            if score >= 0.50 + margin:
                return "strong_risk_on"
            if score >= 0.20 - margin:
                return current
        if current == "neutral":
            if -0.20 - margin < score < 0.20 + margin:
                return current
        if current == "risk_off":
            if score <= -0.50 - margin:
                return "strong_risk_off"
            if score <= -0.20 + margin:
                return current
        if current == "strong_risk_off" and score <= -0.50 + margin:
            return current
        return cls._raw_regime(score, True)

    @classmethod
    def _regimes_with_hysteresis(
        cls,
        scores: Series,
        complete: Series,
    ) -> list[RegimeV4]:
        current: RegimeV4 = "blocked"
        pending: RegimeV4 = "blocked"
        pending_count = 0
        output: list[RegimeV4] = []
        for score_value, complete_value in zip(scores, complete):
            if not bool(complete_value) or not np.isfinite(float(score_value)):
                current = "blocked"
                pending = "blocked"
                pending_count = 0
                output.append(current)
                continue
            candidate = cls._hysteresis_candidate(float(score_value), current)
            if candidate == current:
                pending = current
                pending_count = 0
            elif candidate == pending:
                pending_count += 1
            else:
                pending = candidate
                pending_count = 1
            if pending_count >= cls._REGIME_CONFIRM_BARS:
                current = pending
                pending_count = 0
            output.append(current)
        return output

    def _daily_beta_panel(self) -> DataFrame:
        frames = {pair: self._daily_frame(pair) for pair in BETA_INPUT_PAIRS}
        frames = {
            pair: self._with_residual_momentum(pair, frame, frames)
            for pair, frame in frames.items()
        }
        dates = sorted(
            {
                value
                for frame in frames.values()
                if not frame.empty
                for value in frame["available_at"].dropna().tolist()
            }
        )
        if not dates:
            return DataFrame()
        panel = DataFrame(
            {"available_at": pd.Series(dates, dtype="datetime64[ns, UTC]")}
        )
        fields = (
            "absolute_trend",
            "momentum20",
            "residual_momentum",
            "realized_vol20",
            "vol_ratio",
            "risk_quality",
        )
        for pair, frame in frames.items():
            prefix = pair.split("/", 1)[0].lower()
            if frame.empty:
                for field in fields:
                    panel[f"{prefix}_{field}"] = np.nan
                continue
            renamed = {
                field: f"{prefix}_{field}"
                for field in fields
            }
            right = frame[["available_at", *fields]].rename(columns=renamed)
            panel = panel.merge(right, on="available_at", how="left")

        group_pairs = {
            group: [asset.pair for asset in ASSETS if asset.group == group]
            for group in self._GROUP_WEIGHT
        }
        group_rows: dict[str, list[tuple[float, int, float, float]]] = {}
        for group, pairs in group_pairs.items():
            group_rows[group] = [
                self._direction_group(row, pairs)
                for _, row in panel.iterrows()
            ]
            panel[f"{group}_direction"] = [value[0] for value in group_rows[group]]
            panel[f"valid_{group}"] = [value[1] for value in group_rows[group]]
            panel[f"{group}_dispersion"] = [value[2] for value in group_rows[group]]
            panel[f"{group}_risk_quality"] = [value[3] for value in group_rows[group]]
            threshold = panel[f"{group}_dispersion"].rolling(
                252, min_periods=60
            ).quantile(0.95).shift(1)
            dispersion_penalty = (
                panel[f"{group}_dispersion"] > threshold
            ) & threshold.notna()
            panel.loc[dispersion_penalty, f"{group}_risk_quality"] *= 0.50

        required = (
            panel["qqq_absolute_trend"].notna()
            & panel["btc_absolute_trend"].notna()
            & panel["eth_absolute_trend"].notna()
        )
        complete = required.copy()
        for group, minimum in self._MIN_VALID.items():
            complete &= panel[f"valid_{group}"] >= minimum
        panel["data_complete"] = complete
        panel["beta_direction_score"] = sum(
            panel[f"{group}_direction"] * weight
            for group, weight in self._GROUP_WEIGHT.items()
        )
        panel["risk_quality"] = sum(
            panel[f"{group}_risk_quality"] * weight
            for group, weight in self._GROUP_WEIGHT.items()
        ).clip(0.0, 1.0)
        panel.loc[~complete, "risk_quality"] = 0.0
        return panel

    def _beta_regime_frame(self, dates: Series) -> DataFrame:
        base = DataFrame(
            {
                "date": pd.to_datetime(dates, utc=True, errors="coerce").astype(
                    "datetime64[ns, UTC]"
                )
            }
        )
        cache_key = (
            len(base),
            str(base["date"].iloc[0]) if len(base) else "",
            str(base["date"].iloc[-1]) if len(base) else "",
        )
        cached = getattr(self, "_v4_beta_frame_cache", None)
        if cached and cached[0] == cache_key:
            return cached[1].copy()
        panel = self._daily_beta_panel()
        if panel.empty:
            result = base.copy()
            result["beta_direction_score"] = 0.0
            result["beta_regime"] = "blocked"
            result["risk_quality"] = 0.0
            result["cross_asset_data_fresh"] = False
            for group in self._GROUP_WEIGHT:
                result[f"{group}_direction"] = 0.0
                result[f"valid_{group}"] = 0
        else:
            result = pd.merge_asof(
                base.sort_values("date"),
                panel.sort_values("available_at"),
                left_on="date",
                right_on="available_at",
                direction="backward",
                tolerance=self._BETA_DAILY_TTL,
            )
            complete = result["data_complete"].fillna(False)
            result["beta_regime"] = self._regimes_with_hysteresis(
                result["beta_direction_score"].fillna(0.0),
                complete,
            )
            result["cross_asset_data_fresh"] = complete & (
                result["beta_regime"] != "blocked"
            )
            result["risk_quality"] = result["risk_quality"].fillna(0.0)
        self._v4_beta_frame_cache = (cache_key, result.copy())
        return result

    @staticmethod
    def _numeric(dataframe: DataFrame, name: str, default: float = 0.0) -> Series:
        if name not in dataframe:
            return pd.Series(default, index=dataframe.index, dtype=float)
        return pd.to_numeric(dataframe[name], errors="coerce").fillna(default)

    @classmethod
    def _candidate_components_v4(
        cls,
        dataframe: DataFrame,
        model: SignalModel,
    ) -> dict[str, Series]:
        direction = cls._numeric(dataframe, "beta_direction_score")
        absolute = cls._numeric(dataframe, "absolute_trend")
        residual = cls._numeric(dataframe, "residual_momentum")
        is_long = model == "trend_long"
        regime = (direction + 1.0) * 50 if is_long else (1.0 - direction) * 50
        absolute_score = (absolute + 1.0) * 50 if is_long else (1.0 - absolute) * 50
        residual_score = (residual + 1.0) * 50 if is_long else (1.0 - residual) * 50
        atr = cls._numeric(dataframe, "atr_4h").replace(0.0, np.nan)
        if model == "trend_long":
            breakout_raw = (
                cls._numeric(dataframe, "close_4h")
                - cls._numeric(dataframe, "channel_high_60_4h")
            )
        elif model == "systemic_short":
            breakout_raw = (
                cls._numeric(dataframe, "channel_low_90_4h")
                - cls._numeric(dataframe, "close_4h")
            )
        else:
            breakout_raw = (
                cls._numeric(dataframe, "channel_low_60_4h")
                - cls._numeric(dataframe, "close_4h")
            )
        breakout = (breakout_raw / atr).clip(0.0, 2.0).fillna(0.0) * 50
        adx = ((cls._numeric(dataframe, "adx_4h") - 20.0) / 30.0).clip(0, 1) * 100
        volume_ratio = cls._numeric(dataframe, "volume_4h") / cls._numeric(
            dataframe, "volume_median_30_4h"
        ).replace(0.0, np.nan)
        confirmation = (
            adx * 0.65 + volume_ratio.clip(0.0, 2.0).fillna(0.0) * 17.5
        )
        funding = pd.to_numeric(
            dataframe.get("funding_rate", Series(np.nan, index=dataframe.index)),
            errors="coerce",
        )
        funding_known = dataframe.get(
            "funding_known", funding.notna()
        )
        funding_quality = (
            (0.0005 - funding) / 0.001 * 100
            if is_long
            else (funding + 0.0005) / 0.001 * 100
        ).clip(0, 100)
        liquidity = volume_ratio.clip(0.0, 2.0).fillna(0.0) * 50
        execution = ((funding_quality + liquidity) / 2.0).where(
            pd.Series(funding_known, index=dataframe.index).astype(bool),
            np.nan,
        )
        return {
            "regime": regime.clip(0, 100),
            "absolute": absolute_score.clip(0, 100),
            "residual": residual_score.clip(0, 100),
            "breakout": breakout.clip(0, 100),
            "confirmation": confirmation.clip(0, 100),
            "funding": funding_quality.clip(0, 100),
            "liquidity": liquidity.clip(0, 100),
            "execution": execution,
        }

    @staticmethod
    def _score_components(components: dict[str, Series]) -> Series:
        weights = {
            "regime": 0.20,
            "absolute": 0.25,
            "residual": 0.25,
            "breakout": 0.15,
            "confirmation": 0.10,
            "execution": 0.05,
        }
        numerator = pd.Series(0.0, index=components["regime"].index)
        denominator = pd.Series(0.0, index=components["regime"].index)
        for name, weight in weights.items():
            available = components[name].notna()
            numerator += components[name].fillna(0.0) * weight
            denominator += available.astype(float) * weight
        return (numerator / denominator.replace(0.0, np.nan)).clip(0, 100)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = OkxAggressiveTrendV2.populate_indicators(
            self, dataframe, metadata
        )
        beta = self._beta_regime_frame(dataframe["date"])
        common = (
            "beta_direction_score",
            "beta_regime",
            "risk_quality",
            "cross_asset_data_fresh",
            "benchmark_direction",
            "mag7_direction",
            "memory_direction",
            "crypto_direction",
            "valid_benchmark",
            "valid_mag7",
            "valid_memory",
            "valid_crypto",
        )
        for column in common:
            dataframe[column] = beta[column].to_numpy()
        pair = str(metadata.get("pair", ""))
        prefix = pair.split("/", 1)[0].lower()
        for source, target in (
            (f"{prefix}_absolute_trend", "absolute_trend"),
            (f"{prefix}_residual_momentum", "residual_momentum"),
            (f"{prefix}_risk_quality", "asset_risk_quality"),
            (f"{prefix}_realized_vol20", "realized_vol20_1d"),
        ):
            dataframe[target] = (
                beta[source].to_numpy() if source in beta else np.nan
            )
        dataframe["risk_quality"] = np.minimum(
            dataframe["risk_quality"],
            pd.to_numeric(dataframe["asset_risk_quality"], errors="coerce").fillna(0.0),
        )
        for model in ("trend_long", "systemic_short", "residual_short"):
            components = self._candidate_components_v4(dataframe, model)
            for name, values in components.items():
                dataframe[f"candidate_{name}_{model}"] = values
            dataframe[f"candidate_score_{model}"] = self._score_components(components)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        regime = dataframe["beta_regime"].astype(str)
        common = (
            dataframe["cross_asset_data_fresh"].fillna(False)
            & (self._numeric(dataframe, "risk_quality") > 0)
            & (self._numeric(dataframe, "volume") > 0)
            & (self._numeric(dataframe, "volume_4h")
               >= self._numeric(dataframe, "volume_median_30_4h"))
        )
        long_signal = (
            common
            & regime.isin(("risk_on", "strong_risk_on"))
            & (self._numeric(dataframe, "absolute_trend") >= 0.25)
            & (
                self._numeric(dataframe, "close_4h")
                > self._numeric(dataframe, "channel_high_60_4h")
            )
            & (
                self._numeric(dataframe, "ema30_4h")
                > self._numeric(dataframe, "ema90_4h")
            )
            & (self._numeric(dataframe, "adx_4h") >= 20)
        )
        systemic_short = (
            common
            & regime.isin(("risk_off", "strong_risk_off"))
            & (self._numeric(dataframe, "absolute_trend") <= -0.25)
            & (
                self._numeric(dataframe, "close_4h")
                < self._numeric(dataframe, "channel_low_90_4h")
            )
            & (
                self._numeric(dataframe, "ema30_4h")
                < self._numeric(dataframe, "ema90_4h")
            )
            & (self._numeric(dataframe, "adx_4h") >= 25)
        )
        residual_short = (
            common
            & regime.isin(("neutral", "risk_on"))
            & (self._numeric(dataframe, "absolute_trend") <= -0.25)
            & (self._numeric(dataframe, "residual_momentum") <= -0.50)
            & (
                self._numeric(dataframe, "close_4h")
                < self._numeric(dataframe, "channel_low_60_4h")
            )
            & (
                self._numeric(dataframe, "ema30_4h")
                < self._numeric(dataframe, "ema90_4h")
            )
            & (self._numeric(dataframe, "adx_4h") >= 25)
        )
        dataframe.loc[long_signal, ["enter_long", "enter_tag"]] = (
            1,
            "trend_long",
        )
        dataframe.loc[
            systemic_short | residual_short,
            "enter_short",
        ] = 1
        dataframe.loc[systemic_short, "enter_tag"] = "systemic_short"
        dataframe.loc[residual_short, "enter_tag"] = "residual_short"
        return dataframe

    @staticmethod
    def _bottom_group_members(
        snapshots: dict[str, Series],
    ) -> set[str]:
        groups: dict[str, list[tuple[str, float]]] = {}
        for pair, candle in snapshots.items():
            spec = ASSET_BY_PAIR.get(pair)
            residual = pd.to_numeric(
                Series([candle.get("residual_momentum")]), errors="coerce"
            ).iloc[0]
            if spec is None or pd.isna(residual):
                continue
            groups.setdefault(spec.group, []).append((pair, float(residual)))
        selected: set[str] = set()
        for values in groups.values():
            count = max(1, int(np.ceil(len(values) * 0.20)))
            selected.update(
                pair for pair, _ in sorted(values, key=lambda item: (item[1], item[0]))[:count]
            )
        return selected

    @classmethod
    def _runtime_score(
        cls,
        candle: Series,
        model: SignalModel,
        spread_quality: float | None,
    ) -> tuple[float, float | None]:
        components = {
            name: pd.Series([candle.get(f"candidate_{name}_{model}")], dtype=float)
            for name in (
                "regime",
                "absolute",
                "residual",
                "breakout",
                "confirmation",
                "execution",
            )
        }
        execution_quality: float | None = None
        funding = candle.get(f"candidate_funding_{model}")
        liquidity = candle.get(f"candidate_liquidity_{model}")
        if pd.notna(funding) and pd.notna(liquidity) and spread_quality is not None:
            execution_quality = float(
                (float(funding) + float(liquidity) + spread_quality) / 3.0
            )
            components["execution"] = pd.Series([execution_quality], dtype=float)
        score = float(cls._score_components(components).iloc[0])
        return score, execution_quality

    def _runtime_candidates_all(self, current_time: datetime) -> list[dict[str, Any]]:
        snapshots: dict[str, Series] = {}
        for pair in TRADABLE_PAIRS:
            try:
                frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            except Exception:
                continue
            if frame is None or frame.empty:
                continue
            candle = frame.iloc[-1]
            candle_time = pd.Timestamp(candle.get("date"))
            if candle_time.tzinfo is None:
                candle_time = candle_time.tz_localize("UTC")
            now = pd.Timestamp(current_time)
            if now.tzinfo is None:
                now = now.tz_localize("UTC")
            if now - candle_time <= self._CRITICAL_INTRADAY_TTL:
                snapshots[pair] = candle
        bottom_members = self._bottom_group_members(snapshots)
        candidates: list[dict[str, Any]] = []
        for pair, candle in snapshots.items():
            spec = self.asset_spec(pair)
            if spec is None or not self._calendar().is_open_for_entry(
                spec.session, current_time
            ):
                continue
            models: list[SignalModel] = []
            if int(self._safe_float(candle.get("enter_long"), 0)) == 1:
                models.append("trend_long")
            if int(self._safe_float(candle.get("enter_short"), 0)) == 1:
                tag = str(candle.get("enter_tag", ""))
                if tag in {"systemic_short", "residual_short"}:
                    models.append(tag)
            for model in models:
                if model == "residual_short" and pair not in bottom_members:
                    continue
                spread_quality: float | None = None
                if not self._is_backtest():
                    if not bool(candle.get("funding_known", False)):
                        continue
                    try:
                        orderbook = self.dp.orderbook(pair, 1)
                        bid = float(orderbook["bids"][0][0])
                        ask = float(orderbook["asks"][0][0])
                        mid = (ask + bid) / 2.0
                        spread = (ask - bid) / mid if mid > 0 else 1.0
                        spread_quality = float(
                            np.clip(1.0 - spread / 0.001, 0.0, 1.0) * 100
                        )
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                score, execution_quality = self._runtime_score(
                    candle, model, spread_quality
                )
                candidates.append(
                    {
                        "pair": pair,
                        "side": "long" if model == "trend_long" else "short",
                        "model": model,
                        "score": score,
                        "volatility": self._safe_float(
                            candle.get("realized_vol20_1d"), 999.0
                        ),
                        "liquidity": self._safe_float(
                            candle.get(f"candidate_liquidity_{model}"), 0.0
                        ),
                        "absolute_trend": self._safe_float(
                            candle.get("absolute_trend"), 0.0
                        ),
                        "residual_momentum": self._safe_float(
                            candle.get("residual_momentum"), np.nan
                        ),
                        "execution_quality": execution_quality,
                        "regime": str(candle.get("beta_regime", "blocked")),
                    }
                )
        return candidates

    @staticmethod
    def _select_v4_candidate(
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        valid = [
            candidate
            for candidate in candidates
            if np.isfinite(float(candidate.get("score", np.nan)))
        ]
        if not valid:
            return None
        systemic = [
            candidate for candidate in valid
            if candidate["model"] == "systemic_short"
        ]
        if systemic:
            valid = systemic
        else:
            longs = [
                candidate for candidate in valid
                if candidate["model"] == "trend_long"
            ]
            residual = [
                candidate for candidate in valid
                if candidate["model"] == "residual_short"
            ]
            if longs and residual:
                best_long = max(float(item["score"]) for item in longs)
                residual = [
                    item for item in residual
                    if item["regime"] != "risk_on"
                    or float(item["score"]) >= best_long + 10.0
                ]
                valid = longs + residual
        best_score = max(float(item["score"]) for item in valid)
        tied = [
            item for item in valid
            if best_score - float(item["score"]) < 5.0
        ]
        if any(item["side"] == "long" for item in tied):
            preference = {_pair("QQQ"): 0, _pair("BTC"): 1}
            return min(
                tied,
                key=lambda item: (
                    0 if item["side"] == "long" else 1,
                    preference.get(str(item["pair"]), 2),
                    float(item.get("volatility", 999.0)),
                    str(item["pair"]),
                ),
            )
        return min(
            tied,
            key=lambda item: (
                -float(item.get("liquidity", 0.0)),
                float(item.get("volatility", 999.0)),
                str(item["pair"]),
            ),
        )

    def _confirm_trade_entry(self, *args, **kwargs) -> bool:
        pair = str(args[0] if args else kwargs.get("pair"))
        current_time = args[5] if len(args) > 5 else kwargs.get("current_time")
        side = str(args[7] if len(args) > 7 else kwargs.get("side"))
        spec = self.asset_spec(pair)
        if spec is None or not spec.tradable:
            return False
        if not self._calendar().is_open_for_entry(spec.session, current_time):
            return False
        candle = self._last_candle(pair)
        if candle is None or not bool(candle.get("cross_asset_data_fresh", False)):
            return False
        if not self._is_backtest() and not self._critical_data_fresh(current_time):
            return False
        selected = self._select_v4_candidate(
            self._runtime_candidates_all(current_time)
        )
        if (
            selected is None
            or selected["pair"] != pair
            or selected["side"] != side
        ):
            return False
        self._selected_pair = pair
        self._selected_side = side
        self._selected_model = selected["model"]
        self._selected_score = float(selected["score"])
        self._selected_residual = selected.get("residual_momentum")
        return OkxAggressiveTrendV2._confirm_trade_entry(
            self, *args, **kwargs
        )

    def _custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        spec = self.asset_spec(pair)
        limits = self._limits()
        if spec is None or not spec.tradable or limits.get("entries_blocked", True):
            return 0.0
        candle = self._last_candle(pair)
        stop_distance = self._price_stop_distance(candle, current_rate)
        if candle is None or stop_distance <= 0 or leverage <= 0:
            return 0.0
        model = str(entry_tag or candle.get("enter_tag", ""))
        regime = str(candle.get("beta_regime", "blocked"))
        if model == "trend_long":
            if side != "long" or regime not in {
                "risk_on",
                "strong_risk_on",
            }:
                return 0.0
            base_risk = self._MODEL_RISK[
                "trend_long_strong"
                if regime == "strong_risk_on"
                else "trend_long"
            ]
        elif model == "systemic_short":
            if side != "short" or regime not in {
                "risk_off",
                "strong_risk_off",
            }:
                return 0.0
            base_risk = self._MODEL_RISK[model]
        elif model == "residual_short":
            if side != "short" or regime not in {"neutral", "risk_on"}:
                return 0.0
            base_risk = self._MODEL_RISK[model]
        else:
            return 0.0
        if spec.group == "crypto":
            base_risk = min(base_risk, self._CRYPTO_RISK_CAP)
        quality = float(
            np.clip(
                self._safe_float(candle.get("risk_quality"), 0.0),
                0.0,
                1.0,
            )
        )
        risk_fraction = min(
            base_risk * quality,
            self._safe_float(limits.get("risk_cap"), 0.0),
        )
        equity = self._account_equity()
        if risk_fraction <= 0 or equity <= 0:
            return 0.0
        calculated = equity * risk_fraction / (stop_distance * leverage)
        stake = min(
            calculated,
            self._MARGIN_CAP,
            self._safe_float(limits.get("margin_cap"), 0.0),
            self._safe_float(max_stake, 0.0),
            max(0.0, equity - self._CASH_RESERVE),
        )
        if stake <= 0 or (min_stake is not None and stake < float(min_stake)):
            return 0.0
        self._store_entry_risk_plan(
            pair, side, stop_distance, current_rate, current_time
        )
        return round(stake, 8)

    def _active_exit_policy(self) -> ExitPolicy:
        configured = str(
            (getattr(self, "config", {}) or {}).get(
                "beta_v4_exit_policy",
                os.getenv("BETA_V4_EXIT_POLICY", self.exit_policy.value),
            )
        )
        if configured not in {
            "channel_only",
            "delayed_break_even",
            "slow_atr",
        }:
            logger.error("Invalid V4 exit policy; using channel_only")
            return "channel_only"
        return configured  # type: ignore[return-value]

    @staticmethod
    def _trade_profit_at_rate(trade: Trade, rate: float, current_rate: float) -> float:
        try:
            return float(trade.calc_profit_ratio(rate))
        except (AttributeError, TypeError, ValueError):
            leverage = float(getattr(trade, "leverage", 1.0) or 1.0)
            open_rate = float(getattr(trade, "open_rate", current_rate))
            if open_rate <= 0:
                return 0.0
            raw = rate / open_rate - 1.0
            return (-raw if bool(getattr(trade, "is_short", False)) else raw) * leverage

    def _update_trade_excursions(
        self,
        trade: Trade,
        current_rate: float,
        initial_risk: float,
        policy: ExitPolicy,
    ) -> tuple[float, float]:
        favorable_rate = self._safe_float(
            getattr(
                trade,
                "min_rate" if trade.is_short else "max_rate",
                current_rate,
            ),
            current_rate,
        )
        adverse_rate = self._safe_float(
            getattr(
                trade,
                "max_rate" if trade.is_short else "min_rate",
                current_rate,
            ),
            current_rate,
        )
        mfe = self._trade_profit_at_rate(trade, favorable_rate, current_rate)
        mae = self._trade_profit_at_rate(trade, adverse_rate, current_rate)
        mfe_r = mfe / initial_risk if initial_risk > 0 else 0.0
        mae_r = mae / initial_risk if initial_risk > 0 else 0.0
        try:
            trade.set_custom_data("initial_r_amount", float(trade.stake_amount) * initial_risk)
            trade.set_custom_data("peak_mfe_r", max(
                mfe_r,
                self._safe_float(trade.get_custom_data("peak_mfe_r", None), 0.0),
            ))
            trade.set_custom_data("peak_mae_r", min(
                mae_r,
                self._safe_float(trade.get_custom_data("peak_mae_r", None), 0.0),
            ))
            trade.set_custom_data("exit_policy", policy)
        except (AttributeError, TypeError, ValueError):
            pass
        return mfe_r, mae_r

    @staticmethod
    def _monotonic_stop(
        trade: Trade,
        initial_stop: float,
        candidate_stop: float,
    ) -> float:
        try:
            previous = float(
                trade.get_custom_data("current_trailing_stop_price", None)
            )
        except (AttributeError, TypeError, ValueError):
            previous = np.nan
        values = [initial_stop, candidate_stop]
        if np.isfinite(previous):
            values.append(previous)
        return min(values) if trade.is_short else max(values)

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        distance = self._initial_stop_distance(trade)
        initial_risk = distance * trade.leverage
        policy = self._active_exit_policy()
        mfe_r, _ = self._update_trade_excursions(
            trade, current_rate, initial_risk, policy
        )
        initial_stop = trade.open_rate * (
            1 + distance if trade.is_short else 1 - distance
        )
        candidate_stop = initial_stop
        candle = self._last_candle(pair)
        if policy == "delayed_break_even" and mfe_r >= 1.5:
            candidate_stop = trade.open_rate * (
                1 - distance * 0.25
                if trade.is_short
                else 1 + distance * 0.25
            )
            if mfe_r >= 3.0 - 1e-9 and candle is not None:
                atr = self._safe_float(candle.get("atr_4h"), 0.0)
                if atr > 0:
                    extreme = trade.min_rate if trade.is_short else trade.max_rate
                    atr_stop = (
                        float(extreme) + atr * 4.0
                        if trade.is_short
                        else float(extreme) - atr * 4.0
                    )
                    candidate_stop = (
                        min(candidate_stop, atr_stop)
                        if trade.is_short
                        else max(candidate_stop, atr_stop)
                    )
        elif policy == "slow_atr" and mfe_r >= 2.0 - 1e-9 and candle is not None:
            atr = self._safe_float(candle.get("atr_4h"), 0.0)
            if atr > 0:
                extreme = trade.min_rate if trade.is_short else trade.max_rate
                candidate_stop = (
                    float(extreme) + atr * 4.5
                    if trade.is_short
                    else float(extreme) - atr * 4.5
                )
        stop_price = self._monotonic_stop(trade, initial_stop, candidate_stop)
        try:
            trade.set_custom_data("current_trailing_stop_price", stop_price)
        except (AttributeError, TypeError, ValueError):
            pass
        return stoploss_from_absolute(
            stop_price,
            current_rate=current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        reason = super().custom_exit(
            pair,
            trade,
            current_time,
            current_rate,
            current_profit,
            **kwargs,
        )
        if reason:
            try:
                trade.set_custom_data("exit_reason", reason)
            except (AttributeError, TypeError, ValueError):
                pass
        return reason

    def _beta_state_path(self) -> Path:
        return Path(
            os.getenv(
                "BETA_REGIME_STATE_PATH",
                "/freqtrade/user_data/risk_guard/beta-v4-regime.json",
            )
        )

    def _write_beta_state(self, current_time: datetime) -> None:
        anchor = self._last_candle(_pair("QQQ"))
        fresh = bool(
            anchor is not None
            and anchor.get("cross_asset_data_fresh", False)
            and self._critical_data_fresh(current_time)
        )
        payload: dict[str, Any] = {
            "updated_at": current_time.astimezone(timezone.utc).isoformat(),
            "beta_direction_score": (
                self._safe_float(anchor.get("beta_direction_score"), 0.0)
                if anchor is not None
                else 0.0
            ),
            "beta_regime": (
                str(anchor.get("beta_regime", "blocked"))
                if anchor is not None
                else "blocked"
            ),
            "risk_quality": (
                self._safe_float(anchor.get("risk_quality"), 0.0)
                if anchor is not None
                else 0.0
            ),
            "selected_pair": getattr(self, "_selected_pair", None),
            "selected_side": getattr(self, "_selected_side", None),
            "selected_model": getattr(self, "_selected_model", None),
            "selected_score": getattr(self, "_selected_score", None),
            "residual_score": getattr(self, "_selected_residual", None),
            "exit_policy": self._active_exit_policy(),
            "market_session": {
                "us": self._calendar().label("us", current_time),
                "kr": self._calendar().label("kr", current_time),
                "crypto": "always_open",
            },
            "cross_asset_data_fresh": fresh,
            "group_exposure": self._open_group_exposure(),
        }
        path = self._beta_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
