"""Cross-asset Beta V5 continuation experiment for OKX perpetuals.

V5 keeps V4's breakout models and execution controls unchanged. It adds
completed-4h pullback/re-entry opportunities inside established trends, with
lower model risk and one stable cross-sectional decision per base candle.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pandas import DataFrame, Series

from freqtrade.persistence import Trade
from freqtrade.strategy import informative
from OkxAggressiveTrendV2 import OkxAggressiveTrendV2
from OkxCrossAssetBetaV3 import TRADABLE_PAIRS, _pair
from OkxCrossAssetBetaV4 import (
    ExitPolicy,
    OkxCrossAssetBetaV4,
    SignalModel,
)


logger = logging.getLogger(__name__)

SignalModelV5 = Literal[
    "trend_long",
    "continuation_long",
    "systemic_short",
    "systemic_pullback_short",
    "residual_short",
]


@dataclass(frozen=True)
class CandidateStateV5:
    pair: str
    side: Literal["long", "short"]
    signal_model: SignalModelV5
    absolute_trend: float
    residual_momentum: float | None
    trigger_strength: float
    confirmation: float
    execution_quality: float | None
    total_score: float


class OkxCrossAssetBetaV5(OkxCrossAssetBetaV4):
    """V4 plus lower-risk trend continuation entries."""

    _MODEL_RISK = {
        **OkxCrossAssetBetaV4._MODEL_RISK,
        "continuation_long_strong": 0.0050,
        "continuation_long": 0.00375,
        "systemic_pullback_short": 0.0025,
    }
    _PULLBACK_LOOKBACK = 3
    _PULLBACK_TOUCH_ATR = 0.25
    _MAX_REENTRY_EXTENSION_ATR = 2.5

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = OkxCrossAssetBetaV4.populate_indicators_4h(
            self, dataframe, metadata
        )
        atr = pd.to_numeric(dataframe["atr"], errors="coerce")
        ema30 = pd.to_numeric(dataframe["ema30"], errors="coerce")
        ema90 = pd.to_numeric(dataframe["ema90"], errors="coerce")
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        low = pd.to_numeric(dataframe["low"], errors="coerce")
        high = pd.to_numeric(dataframe["high"], errors="coerce")

        long_touch = (
            (low <= ema30 + atr * self._PULLBACK_TOUCH_ATR)
            & (close >= ema90)
            & (ema30 > ema90)
        )
        short_touch = (
            (high >= ema30 - atr * self._PULLBACK_TOUCH_ATR)
            & (close <= ema90)
            & (ema30 < ema90)
        )
        dataframe["recent_long_pullback"] = (
            long_touch.shift(1)
            .rolling(self._PULLBACK_LOOKBACK, min_periods=1)
            .max()
            .fillna(False)
            .astype(bool)
        )
        dataframe["recent_short_pullback"] = (
            short_touch.shift(1)
            .rolling(self._PULLBACK_LOOKBACK, min_periods=1)
            .max()
            .fillna(False)
            .astype(bool)
        )
        dataframe["previous_high"] = high.shift(1)
        dataframe["previous_low"] = low.shift(1)
        dataframe["previous_close"] = close.shift(1)
        return dataframe

    @classmethod
    def _candidate_components_v5(
        cls,
        dataframe: DataFrame,
        model: SignalModelV5,
    ) -> dict[str, Series]:
        base_model: SignalModel = (
            "trend_long" if model == "continuation_long" else "systemic_short"
        )
        components = OkxCrossAssetBetaV4._candidate_components_v4(
            dataframe, base_model
        )
        atr = cls._numeric(dataframe, "atr_4h").replace(0.0, np.nan)
        if model == "continuation_long":
            trigger_raw = (
                cls._numeric(dataframe, "close_4h")
                - cls._numeric(dataframe, "previous_close_4h")
            )
        else:
            trigger_raw = (
                cls._numeric(dataframe, "previous_close_4h")
                - cls._numeric(dataframe, "close_4h")
            )
        components["breakout"] = (
            trigger_raw / atr
        ).clip(0.0, 2.0).fillna(0.0) * 50
        return components

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = OkxCrossAssetBetaV4.populate_indicators(
            self, dataframe, metadata
        )
        for model in ("continuation_long", "systemic_pullback_short"):
            components = self._candidate_components_v5(dataframe, model)
            for name, values in components.items():
                dataframe[f"candidate_{name}_{model}"] = values
            dataframe[f"candidate_score_{model}"] = self._score_components(components)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = OkxCrossAssetBetaV4.populate_entry_trend(
            self, dataframe, metadata
        )
        regime = dataframe["beta_regime"].astype(str)
        atr = self._numeric(dataframe, "atr_4h")
        close = self._numeric(dataframe, "close_4h")
        ema30 = self._numeric(dataframe, "ema30_4h")
        ema90 = self._numeric(dataframe, "ema90_4h")
        common = (
            dataframe["cross_asset_data_fresh"].fillna(False)
            & (self._numeric(dataframe, "risk_quality") > 0)
            & (self._numeric(dataframe, "volume") > 0)
            & (
                self._numeric(dataframe, "volume_4h")
                >= self._numeric(dataframe, "volume_median_30_4h")
            )
        )
        no_long_breakout = self._numeric(dataframe, "enter_long") != 1
        no_short_breakout = self._numeric(dataframe, "enter_short") != 1
        continuation_long = (
            common
            & no_long_breakout
            & regime.isin(("risk_on", "strong_risk_on"))
            & (self._numeric(dataframe, "absolute_trend") >= 0.25)
            & (self._numeric(dataframe, "recent_long_pullback_4h") > 0)
            & (close > self._numeric(dataframe, "previous_close_4h"))
            & (close > ema30)
            & (close <= self._numeric(dataframe, "channel_high_60_4h"))
            & (close <= ema30 + atr * self._MAX_REENTRY_EXTENSION_ATR)
            & (ema30 > ema90)
            & (self._numeric(dataframe, "adx_4h") >= 20)
        )
        systemic_pullback_short = (
            common
            & no_short_breakout
            & regime.isin(("risk_off", "strong_risk_off"))
            & (self._numeric(dataframe, "absolute_trend") <= -0.25)
            & (self._numeric(dataframe, "recent_short_pullback_4h") > 0)
            & (close < self._numeric(dataframe, "previous_close_4h"))
            & (close < ema30)
            & (close >= self._numeric(dataframe, "channel_low_90_4h"))
            & (close >= ema30 - atr * self._MAX_REENTRY_EXTENSION_ATR)
            & (ema30 < ema90)
            & (self._numeric(dataframe, "adx_4h") >= 25)
        )
        dataframe.loc[
            continuation_long, ["enter_long", "enter_tag"]
        ] = (1, "continuation_long")
        dataframe.loc[
            systemic_pullback_short, ["enter_short", "enter_tag"]
        ] = (1, "systemic_pullback_short")
        return dataframe

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
        long_models = {"trend_long", "continuation_long"}
        short_models = {
            "systemic_short",
            "systemic_pullback_short",
            "residual_short",
        }
        for pair, candle in snapshots.items():
            spec = self.asset_spec(pair)
            if spec is None or not self._calendar().is_open_for_entry(
                spec.session, current_time
            ):
                continue
            tag = str(candle.get("enter_tag", ""))
            models: list[SignalModelV5] = []
            if (
                int(self._safe_float(candle.get("enter_long"), 0)) == 1
                and tag in long_models
            ):
                models.append(tag)  # type: ignore[arg-type]
            if (
                int(self._safe_float(candle.get("enter_short"), 0)) == 1
                and tag in short_models
            ):
                models.append(tag)  # type: ignore[arg-type]
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
                        "side": "long" if model in long_models else "short",
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
    def _select_v5_candidate(
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        valid = [
            candidate
            for candidate in candidates
            if np.isfinite(float(candidate.get("score", np.nan)))
        ]
        if not valid:
            return None
        systemic_models = {"systemic_short", "systemic_pullback_short"}
        systemic = [
            candidate
            for candidate in valid
            if candidate["model"] in systemic_models
        ]
        if systemic:
            valid = systemic
        else:
            longs = [
                candidate for candidate in valid if candidate["side"] == "long"
            ]
            residual = [
                candidate
                for candidate in valid
                if candidate["model"] == "residual_short"
            ]
            if longs and residual:
                best_long = max(float(item["score"]) for item in longs)
                residual = [
                    item
                    for item in residual
                    if item["regime"] != "risk_on"
                    or float(item["score"]) >= best_long + 10.0
                ]
                valid = longs + residual

        best_score = max(float(item["score"]) for item in valid)
        tied = [
            item
            for item in valid
            if best_score - float(item["score"]) < 5.0
        ]
        if any(item["side"] == "long" for item in tied):
            pair_preference = {_pair("QQQ"): 0, _pair("BTC"): 1}
            model_preference = {"trend_long": 0, "continuation_long": 1}
            return min(
                tied,
                key=lambda item: (
                    0 if item["side"] == "long" else 1,
                    pair_preference.get(str(item["pair"]), 2),
                    model_preference.get(str(item["model"]), 2),
                    float(item.get("volatility", 999.0)),
                    str(item["pair"]),
                ),
            )
        model_preference = {
            "systemic_short": 0,
            "systemic_pullback_short": 1,
            "residual_short": 2,
        }
        return min(
            tied,
            key=lambda item: (
                -float(item.get("liquidity", 0.0)),
                model_preference.get(str(item["model"]), 3),
                float(item.get("volatility", 999.0)),
                str(item["pair"]),
            ),
        )

    def _stable_v5_candidate(
        self, current_time: datetime
    ) -> dict[str, Any] | None:
        timestamp = pd.Timestamp(current_time)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        cache_key = int(timestamp.floor("1h").value)
        cached = getattr(self, "_v5_candidate_cache", None)
        if cached is not None and cached[0] == cache_key:
            selected = cached[1]
            return dict(selected) if selected is not None else None
        selected = self._select_v5_candidate(
            self._runtime_candidates_all(current_time)
        )
        self._v5_candidate_cache = (
            cache_key,
            dict(selected) if selected is not None else None,
        )
        return dict(selected) if selected is not None else None

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
        selected = self._stable_v5_candidate(current_time)
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
        if model in {"trend_long", "continuation_long"}:
            if side != "long" or regime not in {"risk_on", "strong_risk_on"}:
                return 0.0
            if model == "trend_long":
                risk_key = (
                    "trend_long_strong"
                    if regime == "strong_risk_on"
                    else "trend_long"
                )
            else:
                risk_key = (
                    "continuation_long_strong"
                    if regime == "strong_risk_on"
                    else "continuation_long"
                )
            base_risk = self._MODEL_RISK[risk_key]
        elif model == "systemic_short":
            if side != "short" or regime not in {
                "risk_off",
                "strong_risk_off",
            }:
                return 0.0
            base_risk = self._MODEL_RISK[model]
        elif model == "systemic_pullback_short":
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

    def _update_trade_excursions(
        self,
        trade: Trade,
        current_rate: float,
        initial_risk: float,
        policy: ExitPolicy,
    ) -> tuple[float, float]:
        excursions = OkxCrossAssetBetaV4._update_trade_excursions(
            self, trade, current_rate, initial_risk, policy
        )
        model = getattr(trade, "enter_tag", None) or getattr(
            self, "_selected_model", None
        )
        try:
            trade.set_custom_data("signal_model", model)
        except (AttributeError, TypeError, ValueError):
            pass
        return excursions

    def _active_exit_policy(self) -> ExitPolicy:
        configured = str(
            (getattr(self, "config", {}) or {}).get(
                "beta_v5_exit_policy",
                os.getenv("BETA_V5_EXIT_POLICY", self.exit_policy.value),
            )
        )
        if configured not in {
            "channel_only",
            "delayed_break_even",
            "slow_atr",
        }:
            logger.error("Invalid V5 exit policy; using channel_only")
            return "channel_only"
        return configured  # type: ignore[return-value]

    def _beta_state_path(self) -> Path:
        return Path(
            os.getenv(
                "BETA_REGIME_STATE_PATH",
                "/freqtrade/user_data/risk_guard/beta-v5-regime.json",
            )
        )
