"""Aggressive, risk-budgeted OKX perpetual trend strategy.

The strategy is intentionally fail-closed in live and dry-run modes when the
external risk guard is missing or stale. Backtesting and hyperopt are not
gated by the runtime risk-state file so every signal grade can be evaluated.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IStrategy,
    IntParameter,
    informative,
    stoploss_from_absolute,
    stoploss_from_open,
)


logger = logging.getLogger(__name__)


class OkxAggressiveTrendV1(IStrategy):
    """BTC perpetual trend breakout with graded risk and dynamic leverage."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "1h"
    startup_candle_count = 1250
    process_only_new_candles = True

    # ROI exits are effectively disabled. Exits come from signals, time-stop,
    # custom stoploss, or the external account risk guard.
    minimal_roi = {}
    stoploss = -0.25
    use_custom_stoploss = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    position_adjustment_enable = False

    entry_window = IntParameter(19, 29, default=24, space="buy", optimize=True, load=True)
    adx_threshold = IntParameter(18, 24, default=20, space="buy", optimize=True, load=True)
    atr_multiplier = DecimalParameter(
        1.8, 2.6, default=2.2, decimals=1, space="stoploss", optimize=True, load=True
    )
    trail_atr_multiplier = DecimalParameter(
        2.2, 3.2, default=2.8, decimals=1, space="stoploss", optimize=True, load=True
    )

    order_types = {
        "entry": "market",
        "exit": "market",
        "emergency_exit": "market",
        "force_entry": "market",
        "force_exit": "market",
        "stoploss": "limit",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
        "stoploss_on_exchange_limit_ratio": 0.99,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    _LEVEL_NUMBER = {"a": 1, "s": 2, "splus": 3}
    _LEVEL_RISK = {"a": 0.08, "s": 0.12, "splus": 0.20}
    _LEVEL_LEVERAGE = {"a": 5.0, "s": 7.0, "splus": 10.0}
    _DEFAULT_BACKTEST_LIMITS = {
        "entries_blocked": False,
        "max_signal_level": 3,
        "risk_cap": 0.20,
        "leverage_cap": 10.0,
        "margin_cap": 20.0,
        "updated_at": None,
    }

    def bot_start(self, **kwargs) -> None:
        self._risk_state: dict[str, Any] = {}
        self._risk_state_loaded_at: datetime | None = None

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 3},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 24,
                "trade_limit": 2,
                "stop_duration_candles": 12,
                "required_profit": 0.0,
                "only_per_pair": False,
                "only_per_side": False,
            },
        ]

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        return dataframe

    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema50_slope"] = dataframe["ema50"].diff()
        return dataframe

    def informative_pairs(self):
        # Register funding data for live refresh, but merge it manually.  The
        # informative decorator aborts an entire backtest when an exchange has
        # no funding history for the requested period (OKX only exposes a
        # limited recent window through CCXT).
        return [(pair, "1h", "funding_rate") for pair in self.dp.current_whitelist()]

    def _merge_funding_rate(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        try:
            funding = self.dp.get_pair_dataframe(
                pair=metadata["pair"], timeframe="1h", candle_type="funding_rate"
            )
            if funding is None or funding.empty:
                dataframe["funding_rate"] = np.nan
                return dataframe
            rates = funding[["date", "open"]].copy().rename(columns={"open": "funding_rate"})
            rates["date"] = pd.to_datetime(rates["date"], utc=True)
            rates = rates.dropna(subset=["date", "funding_rate"]).sort_values("date")
            base = dataframe.copy()
            base["date"] = pd.to_datetime(base["date"], utc=True)
            return pd.merge_asof(
                base.sort_values("date"),
                rates,
                on="date",
                direction="backward",
                tolerance=pd.Timedelta(hours=12),
            )
        except Exception as exc:  # noqa: BLE001 - missing funding must remain an unknown signal.
            logger.warning("Funding-rate data unavailable: %s", exc.__class__.__name__)
            dataframe["funding_rate"] = np.nan
            return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self._merge_funding_rate(dataframe, metadata)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["natr"] = ta.NATR(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["volume_median_24"] = dataframe["volume"].rolling(24).median().shift(1)
        dataframe["exit_high_12"] = dataframe["high"].rolling(12).max().shift(1)
        dataframe["exit_low_12"] = dataframe["low"].rolling(12).min().shift(1)

        for window in self.entry_window.range:
            dataframe[f"entry_high_{window}"] = dataframe["high"].rolling(window).max().shift(1)
            dataframe[f"entry_low_{window}"] = dataframe["low"].rolling(window).min().shift(1)

        selected_high = dataframe[f"entry_high_{self.entry_window.value}"]
        selected_low = dataframe[f"entry_low_{self.entry_window.value}"]
        safe_atr = dataframe["atr"].replace(0, np.nan)
        dataframe["long_breakout_strength"] = (dataframe["close"] - selected_high) / safe_atr
        dataframe["short_breakout_strength"] = (selected_low - dataframe["close"]) / safe_atr
        dataframe["stop_distance"] = (
            (dataframe["atr"] * float(self.atr_multiplier.value)) / dataframe["close"]
        ).clip(lower=0.015, upper=0.035)

        if "funding_rate" not in dataframe:
            dataframe["funding_rate"] = np.nan
        dataframe["funding_known"] = dataframe["funding_rate"].notna()
        dataframe["funding_rate"] = dataframe["funding_rate"].fillna(0.0)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        high = dataframe[f"entry_high_{self.entry_window.value}"]
        low = dataframe[f"entry_low_{self.entry_window.value}"]
        common = (
            (dataframe["adx"] >= self.adx_threshold.value)
            & dataframe["natr"].between(0.5, 3.0, inclusive="both")
            & (dataframe["volume"] >= dataframe["volume_median_24"])
            & (dataframe["volume"] > 0)
        )
        long_a = (
            common
            & (dataframe["close"] > high)
            & (dataframe["ema50_4h"] > dataframe["ema200_4h"])
            & (dataframe["close_4h"] > dataframe["ema200_4h"])
            & (dataframe["funding_rate"] <= 0.0005)
        )
        short_a = (
            common
            & (dataframe["close"] < low)
            & (dataframe["ema50_4h"] < dataframe["ema200_4h"])
            & (dataframe["close_4h"] < dataframe["ema200_4h"])
            & (dataframe["funding_rate"] >= -0.0005)
        )
        long_s = (
            long_a
            & (dataframe["adx"] >= 25)
            & (dataframe["volume"] >= dataframe["volume_median_24"] * 1.25)
            & (dataframe["close_1d"] > dataframe["ema50_1d"])
            & (dataframe["ema50_slope_1d"] > 0)
        )
        short_s = (
            short_a
            & (dataframe["adx"] >= 25)
            & (dataframe["volume"] >= dataframe["volume_median_24"] * 1.25)
            & (dataframe["close_1d"] < dataframe["ema50_1d"])
            & (dataframe["ema50_slope_1d"] < 0)
        )
        long_splus = (
            long_s
            & dataframe["funding_known"]
            & (dataframe["funding_rate"] <= 0)
            & (dataframe["adx"] >= 30)
            & (dataframe["adx"] > dataframe["adx"].shift(1))
            & (dataframe["long_breakout_strength"] >= 0.25)
            & (dataframe["volume"] >= dataframe["volume_median_24"] * 1.5)
        )
        short_splus = (
            short_s
            & dataframe["funding_known"]
            & (dataframe["funding_rate"] >= 0)
            & (dataframe["adx"] >= 30)
            & (dataframe["adx"] > dataframe["adx"].shift(1))
            & (dataframe["short_breakout_strength"] >= 0.25)
            & (dataframe["volume"] >= dataframe["volume_median_24"] * 1.5)
        )

        dataframe.loc[long_a, ["enter_long", "enter_tag"]] = (1, "long_a")
        dataframe.loc[short_a, ["enter_short", "enter_tag"]] = (1, "short_a")
        dataframe.loc[long_s, "enter_tag"] = "long_s"
        dataframe.loc[short_s, "enter_tag"] = "short_s"
        dataframe.loc[long_splus, "enter_tag"] = "long_splus"
        dataframe.loc[short_splus, "enter_tag"] = "short_splus"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long_exit = (
            (dataframe["close"] < dataframe["exit_low_12"])
            | (dataframe["ema50_4h"] < dataframe["ema200_4h"])
            | (dataframe["close_4h"] < dataframe["ema200_4h"])
        ) & (dataframe["volume"] > 0)
        short_exit = (
            (dataframe["close"] > dataframe["exit_high_12"])
            | (dataframe["ema50_4h"] > dataframe["ema200_4h"])
            | (dataframe["close_4h"] > dataframe["ema200_4h"])
        ) & (dataframe["volume"] > 0)
        dataframe.loc[long_exit, ["exit_long", "exit_tag"]] = (1, "trend_reversal")
        dataframe.loc[short_exit, ["exit_short", "exit_tag"]] = (1, "trend_reversal")
        return dataframe

    @staticmethod
    def _level_from_tag(entry_tag: str | None) -> str:
        tag = (entry_tag or "").lower()
        if tag.endswith("_splus"):
            return "splus"
        if tag.endswith("_s"):
            return "s"
        return "a"

    @classmethod
    def _effective_level(cls, signal_level: str, limits: dict[str, Any]) -> str:
        permitted = max(1, min(3, int(limits.get("max_signal_level", 1))))
        effective_number = min(cls._LEVEL_NUMBER[signal_level], permitted)
        return {1: "a", 2: "s", 3: "splus"}[effective_number]

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            converted = float(value)
            return converted if np.isfinite(converted) else default
        except (TypeError, ValueError):
            return default

    def _runmode(self) -> str:
        runmode = getattr(getattr(self, "dp", None), "runmode", None)
        return str(getattr(runmode, "value", runmode or ""))

    def _is_backtest(self) -> bool:
        return self._runmode() in {"backtest", "hyperopt", "edge"}

    def _state_path(self) -> Path:
        return Path(os.getenv("RISK_GUARD_STATE_PATH", "/freqtrade/user_data/risk_guard/state.json"))

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        if self._is_backtest():
            self._risk_state = dict(self._DEFAULT_BACKTEST_LIMITS)
            return
        try:
            with self._state_path().open(encoding="utf-8") as handle:
                self._risk_state = json.load(handle)
            self._risk_state_loaded_at = current_time
        except (OSError, ValueError, TypeError) as exc:
            self._risk_state = {"entries_blocked": True, "reason": "risk_state_unavailable"}
            logger.error("Risk state unavailable; entries are blocked: %s", exc.__class__.__name__)

    def _limits(self) -> dict[str, Any]:
        if self._is_backtest():
            return dict(self._DEFAULT_BACKTEST_LIMITS)
        state = dict(getattr(self, "_risk_state", {}) or {})
        updated = state.get("updated_at")
        try:
            timestamp = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            stale = datetime.now(timezone.utc) - timestamp > timedelta(seconds=90)
        except (TypeError, ValueError):
            stale = True
        if stale:
            state["entries_blocked"] = True
            state["reason"] = "risk_state_stale"
        return state

    def _last_candle(self, pair: str) -> pd.Series | None:
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is None or dataframe.empty:
                return None
            return dataframe.iloc[-1].squeeze()
        except Exception as exc:  # Freqtrade will log detailed provider failures separately.
            logger.warning("Unable to read analyzed candle: %s", exc.__class__.__name__)
            return None

    @staticmethod
    def _volatility_leverage(natr: float) -> float:
        if natr <= 0.8:
            return 10.0
        if natr <= 1.5:
            return 7.0
        if natr <= 2.2:
            return 5.0
        if natr <= 3.0:
            return 3.0
        return 0.0

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        try:
            candle = self._last_candle(pair)
            natr = self._safe_float(candle.get("natr") if candle is not None else None, 99.0)
            limits = self._limits()
            level = self._effective_level(self._level_from_tag(entry_tag), limits)
            selected = min(
                self._LEVEL_LEVERAGE[level],
                self._volatility_leverage(natr),
                self._safe_float(limits.get("leverage_cap"), 0.0),
                self._safe_float(max_leverage, 1.0),
            )
            return max(1.0, selected) if selected > 0 else 1.0
        except Exception as exc:  # noqa: BLE001 - leverage must fail safe, never fail open.
            logger.error("Leverage calculation failed safely: %s", exc.__class__.__name__)
            return 1.0

    def custom_stake_amount(
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
        try:
            return self._custom_stake_amount(
                pair,
                current_time,
                current_rate,
                proposed_stake,
                min_stake,
                max_stake,
                leverage,
                entry_tag,
                side,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - callback exceptions otherwise fail open.
            logger.error("Stake calculation failed closed: %s", exc.__class__.__name__)
            return 0.0

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
            return 0.0
        level = self._effective_level(self._level_from_tag(entry_tag), limits)
        candle = self._last_candle(pair)
        if candle is None:
            return 0.0
        stop_distance = self._safe_float(candle.get("stop_distance"), 0.0)
        if not 0.015 <= stop_distance <= 0.035 or leverage <= 0:
            return 0.0
        try:
            equity = float(self.wallets.get_total_stake_amount())
        except Exception:
            return 0.0
        if equity <= 0:
            return 0.0
        risk_fraction = min(
            self._LEVEL_RISK[level], self._safe_float(limits.get("risk_cap"), 0.0)
        )
        risk_budget = equity * risk_fraction
        calculated = risk_budget / (stop_distance * leverage)
        reserve_cap = max(0.0, min(equity, self._safe_float(max_stake, 0.0)) - 10.0)
        margin_cap = min(
            self._safe_float(limits.get("margin_cap"), 0.0),
            self._safe_float(max_stake, 0.0),
            reserve_cap,
        )
        stake = min(calculated, margin_cap)
        if stake <= 0 or (min_stake is not None and stake < float(min_stake)):
            return 0.0
        return round(stake, 8)

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        try:
            return self._confirm_trade_entry(
                pair,
                order_type,
                amount,
                rate,
                time_in_force,
                current_time,
                entry_tag,
                side,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - callback exceptions otherwise fail open.
            logger.error("Entry confirmation failed closed: %s", exc.__class__.__name__)
            return False

    def _confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        if self._is_backtest():
            return True
        limits = self._limits()
        if limits.get("entries_blocked", True):
            return False
        candle = self._last_candle(pair)
        if candle is None:
            return False
        candle_date = pd.Timestamp(candle.get("date"))
        if candle_date.tzinfo is None:
            candle_date = candle_date.tz_localize("UTC")
        now = pd.Timestamp(current_time)
        if now.tzinfo is None:
            now = now.tz_localize("UTC")
        if now - candle_date > pd.Timedelta(hours=2, minutes=5):
            return False
        try:
            orderbook = self.dp.orderbook(pair, 1)
            bid = float(orderbook["bids"][0][0])
            ask = float(orderbook["asks"][0][0])
            mid = (ask + bid) / 2
            if mid <= 0 or (ask - bid) / mid > 0.001:
                return False
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        return True

    def _initial_stop_distance(self, trade: Trade, candle: pd.Series | None) -> float:
        stored = trade.get_custom_data("initial_stop_distance", None)
        distance = self._safe_float(stored, 0.0)
        if not 0.015 <= distance <= 0.035:
            distance = self._safe_float(
                candle.get("stop_distance") if candle is not None else None, 0.035
            )
            distance = float(np.clip(distance, 0.015, 0.035))
            trade.set_custom_data("initial_stop_distance", distance)
        return distance

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
        distance = self._initial_stop_distance(trade, candle)
        initial_risk = distance * trade.leverage

        if current_profit >= initial_risk * 1.5 and candle is not None:
            atr = self._safe_float(candle.get("atr"), 0.0)
            if atr > 0:
                if trade.is_short:
                    extreme = self._safe_float(trade.min_rate, current_rate)
                    stop_price = extreme + atr * float(self.trail_atr_multiplier.value)
                else:
                    extreme = self._safe_float(trade.max_rate, current_rate)
                    stop_price = extreme - atr * float(self.trail_atr_multiplier.value)
                return stoploss_from_absolute(
                    stop_price,
                    current_rate=current_rate,
                    is_short=trade.is_short,
                    leverage=trade.leverage,
                )

        if current_profit >= initial_risk:
            fee_buffer_profit = 0.0015 * trade.leverage
            return stoploss_from_open(
                fee_buffer_profit,
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

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        if current_time - trade.open_date_utc < timedelta(hours=96):
            return None
        candle = self._last_candle(pair)
        distance = self._initial_stop_distance(trade, candle)
        initial_risk = distance * trade.leverage
        extreme = trade.min_rate if trade.is_short else trade.max_rate
        max_profit = trade.calc_profit_ratio(extreme) if extreme else current_profit
        if max_profit < initial_risk * 0.5:
            return "time_stop_96h"
        return None

    plot_config = {
        "main_plot": {
            "entry_high_24": {"color": "green"},
            "entry_low_24": {"color": "red"},
            "ema50_4h": {"color": "blue"},
            "ema200_4h": {"color": "orange"},
        },
        "subplots": {
            "ADX": {"adx": {"color": "purple"}},
            "NATR": {"natr": {"color": "black"}},
            "Funding": {"funding_rate": {"color": "gray"}},
        },
    }
