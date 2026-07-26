from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd


STRATEGY_DIR = Path(__file__).parents[1] / "runtime" / "strategies"
PAIR = "BTC/USDT:USDT"


class _Parameter:
    def __init__(self, *values, default=None, **kwargs):
        self.value = default
        self.range = values[0] if len(values) == 1 and isinstance(values[0], list) else [default]


def _load_strategy():
    talib = types.ModuleType("talib")
    talib.abstract = types.ModuleType("talib.abstract")
    persistence = types.ModuleType("freqtrade.persistence")
    persistence.Trade = object
    strategy = types.ModuleType("freqtrade.strategy")
    strategy.CategoricalParameter = _Parameter
    strategy.DecimalParameter = _Parameter
    strategy.IntParameter = _Parameter
    strategy.IStrategy = type("IStrategy", (), {})
    strategy.informative = lambda *_args, **_kwargs: lambda func: func
    strategy.stoploss_from_absolute = lambda stop_price, **_kwargs: stop_price
    strategy.stoploss_from_open = lambda stop_profit, *_args, **_kwargs: stop_profit

    modules = {
        "talib": talib,
        "talib.abstract": talib.abstract,
        "freqtrade.persistence": persistence,
        "freqtrade.strategy": strategy,
    }
    with patch.dict(sys.modules, modules):
        base_spec = importlib.util.spec_from_file_location(
            "OkxAggressiveTrendV1", STRATEGY_DIR / "OkxAggressiveTrendV1.py"
        )
        base_module = importlib.util.module_from_spec(base_spec)
        assert base_spec and base_spec.loader
        base_spec.loader.exec_module(base_module)
        with patch.dict(sys.modules, {"OkxAggressiveTrendV1": base_module}):
            spec = importlib.util.spec_from_file_location(
                "OkxAggressiveTrendV2", STRATEGY_DIR / "OkxAggressiveTrendV2.py"
            )
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)
    return module.OkxAggressiveTrendV2()


class _Wallets:
    def __init__(self, equity: float):
        self.equity = equity

    def get_total_stake_amount(self) -> float:
        return self.equity


class _DataProvider:
    def __init__(self, candle: pd.Series, bid: float = 99.95, ask: float = 100.05):
        self.candle = candle
        self.bid = bid
        self.ask = ask
        self.runmode = types.SimpleNamespace(value="dry_run")

    def get_analyzed_dataframe(self, _pair, _timeframe):
        return pd.DataFrame([self.candle]), None

    def orderbook(self, _pair, _depth):
        return {"bids": [[self.bid, 1.0]], "asks": [[self.ask, 1.0]]}


class _Trade:
    def __init__(self, *, is_short: bool = False):
        self.id = 1
        self.is_short = is_short
        self.entry_side = "sell" if is_short else "buy"
        self.open_rate = 100.0
        self.leverage = 5.0
        self.max_rate = 100.0
        self.min_rate = 100.0
        self._custom_data = {}

    def get_custom_data(self, key, default=None):
        return self._custom_data.get(key, default)

    def set_custom_data(self, key, value):
        self._custom_data[key] = value


class TrendDirectionTests(unittest.TestCase):
    def setUp(self):
        self.strategy = _load_strategy()

    def test_ema30_ema60_controls_entries_and_exits(self):
        self.assertEqual(
            set(self.strategy.plot_config["main_plot"]), {"ema30_4h", "ema60_4h"}
        )
        dataframe = pd.DataFrame(
            {
                "channel_high_30_4h": [100, 110],
                "channel_low_80_4h": [90, 100],
                "channel_low_15_4h": [100, 80],
                "channel_high_40_4h": [120, 120],
                "close_4h": [110, 90],
                "close_1d": [110, 90],
                "ema30_4h": [105, 95],
                "ema60_4h": [100, 100],
                "ema30_1d": [105, 95],
                "ema60_1d": [100, 100],
                "adx_4h": [23, 25],
                "volume_4h": [125, 100],
                "volume_median_30_4h": [100, 100],
                "volume": [1, 1],
            }
        )

        result = self.strategy.populate_entry_trend(dataframe.copy(), {})
        self.assertEqual(result.loc[0, "enter_long"], 1)
        self.assertTrue(pd.isna(result.loc[0, "enter_short"]))
        self.assertTrue(pd.isna(result.loc[1, "enter_long"]))
        self.assertEqual(result.loc[1, "enter_short"], 1)

        result = self.strategy.populate_exit_trend(dataframe.copy(), {})
        self.assertTrue(pd.isna(result.loc[0, "exit_long"]))
        self.assertTrue(pd.isna(result.loc[1, "exit_short"]))

        dataframe.loc[0, "ema30_4h"] = 95
        dataframe.loc[1, "ema30_4h"] = 105
        result = self.strategy.populate_exit_trend(dataframe, {})
        self.assertEqual(result.loc[0, "exit_long"], 1)
        self.assertEqual(result.loc[1, "exit_short"], 1)


class InitialRiskPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.strategy = _load_strategy()
        self.strategy.wallets = _Wallets(100.0)
        self.strategy._limits = lambda: {
            "entries_blocked": False,
            "max_signal_level": 1,
            "risk_cap": 0.02,
            "margin_cap": 100.0,
        }

    def test_stake_distance_is_persisted_on_entry_fill(self):
        now = datetime.now(timezone.utc)
        self.strategy._last_candle = lambda _pair: pd.Series({"atr_4h": 2.0})
        stake = self.strategy._custom_stake_amount(
            PAIR, now, 100.0, 10.0, 1.0, 100.0, 5.0, "long_a", "long"
        )
        self.assertEqual(stake, 10.0)

        self.strategy._last_candle = lambda _pair: pd.Series({"atr_4h": 4.0})
        trade = _Trade()
        order = types.SimpleNamespace(ft_order_side="buy")
        self.strategy.order_filled(PAIR, trade, order, now + timedelta(minutes=1))

        self.assertAlmostEqual(trade.get_custom_data("initial_stop_distance"), 0.04)
        self.assertAlmostEqual(
            self.strategy._initial_stop_distance(trade, self.strategy._last_candle(PAIR)), 0.04
        )
        self.assertAlmostEqual(
            self.strategy.custom_stoploss(PAIR, trade, now, 100.0, 0.0, True), 96.0
        )
        self.assertEqual(self.strategy._risk_plans(), {})

    def test_missing_fill_plan_uses_tight_fail_safe(self):
        trade = _Trade()
        order = types.SimpleNamespace(ft_order_side="buy")
        self.strategy.order_filled(PAIR, trade, order, datetime.now(timezone.utc))
        self.assertEqual(trade.get_custom_data("initial_stop_distance"), 0.025)

    def test_exit_fill_does_not_overwrite_initial_risk(self):
        trade = _Trade()
        trade.set_custom_data("initial_stop_distance", 0.04)
        order = types.SimpleNamespace(ft_order_side="sell")
        self.strategy.order_filled(PAIR, trade, order, datetime.now(timezone.utc))
        self.assertEqual(trade.get_custom_data("initial_stop_distance"), 0.04)

    def test_later_entry_fill_does_not_overwrite_initial_risk(self):
        now = datetime.now(timezone.utc)
        trade = _Trade()
        trade.set_custom_data("initial_stop_distance", 0.04)
        self.strategy._store_entry_risk_plan(PAIR, "long", 0.06, 100.0, now)
        order = types.SimpleNamespace(ft_order_side="buy")
        self.strategy.order_filled(PAIR, trade, order, now)
        self.assertEqual(trade.get_custom_data("initial_stop_distance"), 0.04)
        self.assertEqual(self.strategy._risk_plans(), {})


class RuntimeGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.strategy = _load_strategy()
        self.strategy.config = {"dry_run": True}
        self.candle = pd.Series(
            {
                "date": self.now,
                "atr_4h": 2.0,
                "natr_4h": 1.0,
                "funding_known": True,
                "funding_rate": 0.0,
            }
        )
        self.strategy.dp = _DataProvider(self.candle)
        self.strategy._risk_state = {
            "entries_blocked": False,
            "updated_at": self.now.isoformat(),
        }

    def _store_plan(self, side="long", rate=100.0):
        self.strategy._store_entry_risk_plan(PAIR, side, 0.04, rate, self.now)

    def _confirm(self, side="long", rate=100.0):
        return self.strategy.confirm_trade_entry(
            pair=PAIR,
            order_type="market",
            amount=0.5,
            rate=rate,
            time_in_force="GTC",
            current_time=self.now,
            entry_tag=f"{side}_a",
            side=side,
        )

    def test_valid_runtime_entry_is_accepted(self):
        self._store_plan()
        self.assertTrue(self._confirm())

    def test_entry_without_risk_plan_is_rejected(self):
        self.assertFalse(self._confirm())

    def test_live_entry_requires_manual_approval(self):
        self.strategy.config["dry_run"] = False
        self.strategy.dp.runmode.value = "live"
        self._store_plan()
        with patch.dict("os.environ", {"RISK_GUARD_LIVE_APPROVED": "false"}):
            self.assertFalse(self._confirm())

    def test_stale_risk_state_is_rejected(self):
        self.strategy._risk_state["updated_at"] = (self.now - timedelta(minutes=5)).isoformat()
        self._store_plan()
        self.assertFalse(self._confirm())

    def test_stale_candle_is_rejected(self):
        self.strategy.dp.candle["date"] = self.now - timedelta(hours=3)
        self._store_plan()
        self.assertFalse(self._confirm())

    def test_wide_spread_is_rejected(self):
        self.strategy.dp.bid = 99.0
        self.strategy.dp.ask = 101.0
        self._store_plan()
        self.assertFalse(self._confirm())

    def test_adverse_funding_is_rejected(self):
        self.strategy.dp.candle["funding_rate"] = 0.001
        self._store_plan()
        self.assertFalse(self._confirm())

    def test_changed_entry_rate_is_rejected(self):
        self._store_plan(rate=100.0)
        self.assertFalse(self._confirm(rate=100.2))

    def test_leverage_respects_volatility_exchange_and_guard_caps(self):
        self.strategy._last_candle = lambda _pair: pd.Series({"natr": 0.7})
        self.strategy._limits = lambda: {"max_signal_level": 1, "leverage_cap": 10.0}
        self.assertEqual(
            self.strategy.leverage(PAIR, self.now, 100.0, 1.0, 10.0, "long_a", "long"),
            5.0,
        )
        self.assertEqual(
            self.strategy.leverage(PAIR, self.now, 100.0, 1.0, 3.0, "long_a", "long"),
            3.0,
        )
        self.strategy._last_candle = lambda _pair: pd.Series({"natr": 3.1})
        self.assertEqual(
            self.strategy.leverage(PAIR, self.now, 100.0, 1.0, 10.0, "long_a", "long"),
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
