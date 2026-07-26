"""Asymmetric 4h trend strategy for OKX BTC perpetuals.

V2 reuses the fail-closed runtime and account-risk integration from V1, but
replaces its fee-heavy 1h breakout with completed-4h asymmetric channels.
Long and short signals intentionally use different horizons because BTC has a
positive long-run drift.  Funding is an execution veto, not a historical
ranking feature, because OKX only exposes a limited recent funding window.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import talib.abstract as ta
from pandas import DataFrame, Series

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    CategoricalParameter,
    stoploss_from_absolute,
    stoploss_from_open,
    informative,
)

from OkxAggressiveTrendV1 import OkxAggressiveTrendV1


logger = logging.getLogger(__name__)


class OkxAggressiveTrendV2(OkxAggressiveTrendV1):
    """Completed-4h asymmetric trend channels with account-risk budgeting."""

    startup_candle_count = 1250
    stoploss = -0.50

    # Disable research parameters inherited from V1.  V2's four discrete
    # parameters below are the complete, auditable search space (3^4 = 81).
    fast_window = 20
    adx_threshold = 18

    channel_scale = CategoricalParameter([0.8, 1.0, 1.2], default=1.0, space="buy")
    adx_offset = CategoricalParameter([-3, 0, 3], default=0, space="buy")
    atr_multiplier = CategoricalParameter([1.8, 2.0, 2.2], default=2.0, space="sell")
    trail_atr_multiplier = CategoricalParameter(
        [2.5, 3.0, 3.5], default=3.5, space="sell"
    )

    _LONG_WINDOWS = {0.8: (24, 12), 1.0: (30, 15), 1.2: (36, 18)}
    _SHORT_WINDOWS = {0.8: (64, 32), 1.0: (80, 40), 1.2: (96, 48)}

    # The original 8/12/20 percentages remain absolute code ceilings in the
    # guard, but normal operation starts with survivable risk targets.  Four
    # consecutive 20% losses would exceed the permanent drawdown boundary.
    _LEVEL_RISK = {"a": 0.02, "s": 0.035, "splus": 0.05}
    _ENTRY_RISK_PLAN_TTL = timedelta(minutes=10)

    plot_config = {
        "main_plot": {
            "ema30_4h": {"color": "blue"},
            "ema60_4h": {"color": "orange"},
        },
        "subplots": {
            "ADX": {"adx_4h": {"color": "purple"}},
            "NATR": {"natr_4h": {"color": "black"}},
            "Funding": {"funding_rate": {"color": "gray"}},
        },
    }

    def _is_backtest(self) -> bool:
        # Only the two modes capable of placing exchange orders may consume
        # the external risk-guard state.  Freqtrade analysis commands use the
        # non-trading "other" mode and must remain deterministic/offline.
        return self._runmode() not in {"live", "dry_run"}

    @staticmethod
    def _scale_key(value: Any) -> float:
        return round(float(value), 1)

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["natr"] = ta.NATR(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["ema30"] = ta.EMA(dataframe, timeperiod=30)
        dataframe["ema60"] = ta.EMA(dataframe, timeperiod=60)
        dataframe["volume_median_30"] = dataframe["volume"].rolling(30).median().shift(1)
        for entry, exit_ in set(self._LONG_WINDOWS.values()) | set(self._SHORT_WINDOWS.values()):
            dataframe[f"channel_high_{entry}"] = dataframe["high"].rolling(entry).max().shift(1)
            dataframe[f"channel_low_{entry}"] = dataframe["low"].rolling(entry).min().shift(1)
            dataframe[f"channel_high_{exit_}"] = dataframe["high"].rolling(exit_).max().shift(1)
            dataframe[f"channel_low_{exit_}"] = dataframe["low"].rolling(exit_).min().shift(1)
        return dataframe

    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema30"] = ta.EMA(dataframe, timeperiod=30)
        dataframe["ema60"] = ta.EMA(dataframe, timeperiod=60)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self._merge_funding_rate(dataframe, metadata)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["natr"] = ta.NATR(dataframe, timeperiod=14)
        dataframe["volume_median_24"] = dataframe["volume"].rolling(24).median().shift(1)
        if "funding_rate" not in dataframe:
            dataframe["funding_rate"] = np.nan
        dataframe["funding_known"] = dataframe["funding_rate"].notna()
        dataframe["funding_rate"] = dataframe["funding_rate"].fillna(0.0)
        return dataframe

    def _windows(self) -> tuple[tuple[int, int], tuple[int, int]]:
        scale = self._scale_key(self.channel_scale.value)
        return self._LONG_WINDOWS[scale], self._SHORT_WINDOWS[scale]

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        (long_entry, _), (short_entry, _) = self._windows()
        long_high = dataframe[f"channel_high_{long_entry}_4h"]
        short_low = dataframe[f"channel_low_{short_entry}_4h"]
        long_adx = 18 + int(self.adx_offset.value)
        short_adx = 25 + int(self.adx_offset.value)
        long_a = (
            (dataframe["close_4h"] > long_high)
            & (dataframe["ema30_4h"] > dataframe["ema60_4h"])
            & (dataframe["close_4h"] > dataframe["ema60_4h"])
            & (dataframe["ema30_1d"] > dataframe["ema60_1d"])
            & (dataframe["close_1d"] > dataframe["ema60_1d"])
            & (dataframe["adx_4h"] >= long_adx)
            & (dataframe["volume_4h"] >= dataframe["volume_median_30_4h"])
            & (dataframe["volume"] > 0)
        )
        short_a = (
            (dataframe["close_4h"] < short_low)
            & (dataframe["ema30_4h"] < dataframe["ema60_4h"])
            & (dataframe["close_4h"] < dataframe["ema60_4h"])
            & (dataframe["ema30_1d"] < dataframe["ema60_1d"])
            & (dataframe["close_1d"] < dataframe["ema60_1d"])
            & (dataframe["adx_4h"] >= short_adx)
            & (dataframe["volume_4h"] >= dataframe["volume_median_30_4h"])
            & (dataframe["volume"] > 0)
        )
        long_s = (
            long_a
            & (dataframe["adx_4h"] >= long_adx + 5)
            & (dataframe["volume_4h"] >= dataframe["volume_median_30_4h"] * 1.25)
        )
        short_s = (
            short_a
            & (dataframe["adx_4h"] >= short_adx + 5)
            & (dataframe["volume_4h"] >= dataframe["volume_median_30_4h"] * 1.25)
        )
        long_strong = (
            long_s
            & (dataframe["adx_4h"] >= long_adx + 10)
            & (dataframe["volume_4h"] >= dataframe["volume_median_30_4h"] * 1.5)
        )
        # The two sides are deliberately asymmetric.  BTC long breakouts need
        # confirmation from expanding participation.  On shorts, the same
        # high-volume/high-ADX combination is usually a late capitulation
        # entry, so it is excluded instead of being treated as higher quality.
        executable_long = long_s & ~long_strong
        executable_short = short_a & ~short_s
        dataframe.loc[executable_long, ["enter_long", "enter_tag"]] = (1, "long_a")
        dataframe.loc[executable_short, ["enter_short", "enter_tag"]] = (1, "short_a")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        (_, long_exit), (_, short_exit) = self._windows()
        long_low = dataframe[f"channel_low_{long_exit}_4h"]
        short_high = dataframe[f"channel_high_{short_exit}_4h"]
        dataframe.loc[
            ((dataframe["close_4h"] < long_low) | (dataframe["ema30_4h"] < dataframe["ema60_4h"]))
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "long_channel_exit")
        dataframe.loc[
            ((dataframe["close_4h"] > short_high) | (dataframe["ema30_4h"] > dataframe["ema60_4h"]))
            & (dataframe["volume"] > 0),
            ["exit_short", "exit_tag"],
        ] = (1, "short_channel_exit")
        return dataframe

    def _price_stop_distance(self, candle: Series | None, rate: float) -> float:
        atr = self._safe_float(candle.get("atr_4h") if candle is not None else None, 0.0)
        if atr <= 0 or rate <= 0:
            return 0.0
        return float(
            np.clip(atr * float(self.atr_multiplier.value) / rate, 0.025, 0.080)
        )

    @staticmethod
    def _risk_plan_key(pair: str, side: str) -> tuple[str, str]:
        return pair, side.lower()

    def _risk_plans(self) -> dict[tuple[str, str], dict[str, Any]]:
        plans = getattr(self, "_entry_risk_plans", None)
        if plans is None:
            plans = {}
            self._entry_risk_plans = plans
        return plans

    def _store_entry_risk_plan(
        self,
        pair: str,
        side: str,
        stop_distance: float,
        rate: float,
        current_time: datetime,
    ) -> None:
        timestamp = current_time
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        self._risk_plans()[self._risk_plan_key(pair, side)] = {
            "stop_distance": stop_distance,
            "rate": rate,
            "created_at": timestamp,
        }

    def _entry_risk_plan(
        self, pair: str, side: str, current_time: datetime
    ) -> dict[str, Any] | None:
        key = self._risk_plan_key(pair, side)
        plan = self._risk_plans().get(key)
        if not plan:
            return None
        created_at = plan.get("created_at")
        now = current_time
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if not isinstance(created_at, datetime) or now - created_at > self._ENTRY_RISK_PLAN_TTL:
            self._risk_plans().pop(key, None)
            return None
        return plan

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
        limits = self._limits()
        if limits.get("entries_blocked", True):
            logger.warning("V2 stake blocked by limits: %s", limits.get("reason"))
            return 0.0
        candle = self._last_candle(pair)
        stop_distance = self._price_stop_distance(candle, current_rate)
        if stop_distance <= 0 or leverage <= 0:
            logger.warning(
                "V2 stake missing stop: candle=%s stop=%s", candle is not None, stop_distance
            )
            return 0.0
        level = self._effective_level(self._level_from_tag(entry_tag), limits)
        try:
            equity = float(self.wallets.get_total_stake_amount())
        except Exception:
            return 0.0
        risk_fraction = min(self._LEVEL_RISK[level], self._safe_float(limits.get("risk_cap"), 0.0))
        calculated = equity * risk_fraction / (stop_distance * leverage)
        reserve_cap = max(0.0, equity - 10.0)
        stake = min(
            calculated,
            self._safe_float(limits.get("margin_cap"), 0.0),
            self._safe_float(max_stake, 0.0),
            reserve_cap,
        )
        if stake <= 0 or (min_stake is not None and stake < float(min_stake)):
            logger.warning(
                "V2 stake rejected: equity=%.4f calculated=%.4f stake=%.4f min=%s max=%.4f",
                equity,
                calculated,
                stake,
                min_stake,
                max_stake,
            )
            return 0.0
        self._store_entry_risk_plan(pair, side, stop_distance, current_rate, current_time)
        return round(stake, 8)

    def order_filled(
        self, pair: str, trade: Trade, order: Any, current_time: datetime, **kwargs
    ) -> None:
        if getattr(order, "ft_order_side", None) != trade.entry_side:
            return
        stored = self._safe_float(trade.get_custom_data("initial_stop_distance", None), 0.0)
        if 0.025 <= stored <= 0.080:
            side = "short" if trade.is_short else "long"
            self._risk_plans().pop(self._risk_plan_key(pair, side), None)
            return
        side = "short" if trade.is_short else "long"
        key = self._risk_plan_key(pair, side)
        plan = self._entry_risk_plan(pair, side, current_time)
        if plan is None:
            logger.error("Entry filled without a valid risk plan for %s %s", pair, side)
            trade.set_custom_data("initial_stop_distance", 0.025)
            return
        trade.set_custom_data("initial_stop_distance", plan["stop_distance"])
        self._risk_plans().pop(key, None)

    def _initial_stop_distance(self, trade: Trade, _candle: Series | None = None) -> float:
        stored = self._safe_float(trade.get_custom_data("initial_stop_distance", None), 0.0)
        if not 0.025 <= stored <= 0.080:
            logger.error(
                "Trade %s has no persisted initial stop; using 2.5%% fail-safe",
                getattr(trade, "id", "unknown"),
            )
            stored = 0.025
            trade.set_custom_data("initial_stop_distance", stored)
        return stored

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
        candle = self._last_candle(pair)
        distance = self._initial_stop_distance(trade)
        initial_risk = distance * trade.leverage
        if current_profit >= initial_risk * 2.0 and candle is not None:
            atr = self._safe_float(candle.get("atr_4h"), 0.0)
            if atr > 0:
                extreme = trade.min_rate if trade.is_short else trade.max_rate
                extreme = self._safe_float(extreme, current_rate)
                stop_price = (
                    extreme + atr * float(self.trail_atr_multiplier.value)
                    if trade.is_short
                    else extreme - atr * float(self.trail_atr_multiplier.value)
                )
                return stoploss_from_absolute(
                    stop_price,
                    current_rate=current_rate,
                    is_short=trade.is_short,
                    leverage=trade.leverage,
                )
        if current_profit >= initial_risk:
            return stoploss_from_open(
                0.0015 * trade.leverage,
                current_profit,
                is_short=trade.is_short,
                leverage=trade.leverage,
            ) or 1
        initial_stop = trade.open_rate * (1 + distance if trade.is_short else 1 - distance)
        return stoploss_from_absolute(
            initial_stop,
            current_rate=current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )

    def _confirm_trade_entry(self, *args, **kwargs) -> bool:
        # OKX exposes only a short recent funding history.  Funding is a live
        # execution veto, not a historical signal feature, so applying the
        # live fail-closed rule in backtests would reject all older signals.
        if self._is_backtest():
            return True

        pair = args[0] if args else kwargs.get("pair")
        rate = args[3] if len(args) > 3 else kwargs.get("rate")
        current_time = args[5] if len(args) > 5 else kwargs.get("current_time")
        side = args[7] if len(args) > 7 else kwargs.get("side")
        plan = self._entry_risk_plan(pair, side, current_time)
        planned_rate = self._safe_float(plan.get("rate") if plan else None, 0.0)
        entry_rate = self._safe_float(rate, 0.0)
        if plan is None or planned_rate <= 0 or entry_rate <= 0:
            logger.error("Entry rejected without a valid risk plan for %s %s", pair, side)
            return False
        if abs(planned_rate - entry_rate) / planned_rate > 0.001:
            logger.error("Entry rejected because the planned rate changed for %s %s", pair, side)
            return False

        if not bool(self.config.get("dry_run", False)) and os.getenv(
            "RISK_GUARD_LIVE_APPROVED", "false"
        ).strip().lower() not in {"1", "true", "yes", "on"}:
            logger.error("Live entry rejected: RISK_GUARD_LIVE_APPROVED is not enabled")
            return False

        if not super()._confirm_trade_entry(*args, **kwargs):
            return False
        candle = self._last_candle(pair)
        if candle is None or not bool(candle.get("funding_known", False)):
            return False
        funding = self._safe_float(candle.get("funding_rate"), 99.0)
        return funding <= 0.0005 if side == "long" else funding >= -0.0005
