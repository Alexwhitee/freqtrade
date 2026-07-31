from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


DEPLOYMENT_DIR = Path(__file__).parents[1]
STRATEGY_DIR = DEPLOYMENT_DIR / "runtime" / "strategies"


class _Parameter:
    def __init__(self, *values, default=None, **kwargs):
        self.value = default
        self.range = (
            values[0]
            if len(values) == 1 and isinstance(values[0], list)
            else [default]
        )


def _load_v4_module():
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
        loaded = {}
        for name in (
            "OkxAggressiveTrendV1",
            "OkxAggressiveTrendV2",
            "OkxCrossAssetBetaV3",
            "OkxCrossAssetBetaV4",
        ):
            spec = importlib.util.spec_from_file_location(
                name, STRATEGY_DIR / f"{name}.py"
            )
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    return loaded["OkxCrossAssetBetaV4"]


V4 = _load_v4_module()


class _Wallets:
    def __init__(self, equity: float):
        self.equity = equity

    def get_total(self, _currency: str) -> float:
        return self.equity

    def get_total_stake_amount(self) -> float:
        return self.equity * 0.5


class _Trade:
    def __init__(
        self,
        *,
        is_short: bool = False,
        open_rate: float = 100.0,
        max_rate: float = 100.0,
        min_rate: float = 100.0,
        leverage: float = 1.0,
        stake_amount: float = 5.0,
    ):
        self.is_short = is_short
        self.open_rate = open_rate
        self.max_rate = max_rate
        self.min_rate = min_rate
        self.leverage = leverage
        self.stake_amount = stake_amount
        self.data = {"initial_stop_distance": 0.05}

    def set_custom_data(self, key, value):
        self.data[key] = value

    def get_custom_data(self, key, default=None):
        return self.data.get(key, default)

    def calc_profit_ratio(self, rate):
        raw = rate / self.open_rate - 1.0
        return (-raw if self.is_short else raw) * self.leverage


class DirectionModelTests(unittest.TestCase):
    def test_funding_event_is_available_only_after_completed_hour(self):
        class _FundingProvider:
            def __init__(self, funding):
                self.funding = funding
                self.runmode = types.SimpleNamespace(value="dry_run")

            def get_pair_dataframe(self, **_kwargs):
                return self.funding.copy()

        strategy = object.__new__(V4.OkxCrossAssetBetaV4)
        base = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    [
                        "2026-07-27 08:00:00+00:00",
                        "2026-07-27 08:45:00+00:00",
                        "2026-07-27 09:00:00+00:00",
                        "2026-07-27 16:45:00+00:00",
                    ]
                ),
                "close": [100.0] * 4,
            }
        )
        first_event = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-07-27 08:00:00+00:00"]),
                "open": [0.001],
            }
        )
        strategy.dp = _FundingProvider(first_event)
        without_future = strategy._merge_funding_rate(
            base, {"pair": "BTC/USDT:USDT"}
        )
        self.assertTrue(without_future.loc[:1, "funding_rate"].isna().all())
        self.assertEqual(without_future.loc[2, "funding_rate"], 0.001)
        self.assertEqual(without_future.loc[3, "funding_rate"], 0.001)

        strategy.dp = _FundingProvider(
            pd.concat(
                [
                    first_event,
                    pd.DataFrame(
                        {
                            "date": pd.to_datetime(
                                ["2026-07-27 16:00:00+00:00"]
                            ),
                            "open": [-0.002],
                        }
                    ),
                ],
                ignore_index=True,
            )
        )
        with_future = strategy._merge_funding_rate(
            base, {"pair": "BTC/USDT:USDT"}
        )
        pd.testing.assert_series_equal(
            without_future["funding_rate"],
            with_future["funding_rate"],
            check_names=False,
        )

    def test_flat_series_is_centered_after_seasoning(self):
        dates = pd.date_range("2024-01-01", periods=400, freq="1D", tz="UTC")
        frame = pd.DataFrame({"date": dates, "close": 100.0})
        result = V4.OkxCrossAssetBetaV4._daily_asset_features(frame)
        self.assertTrue(pd.isna(result.loc[119, "absolute_trend"]))
        self.assertAlmostEqual(result.loc[200, "absolute_trend"], 0.0)
        self.assertEqual(result.loc[200, "vol_ratio"], 1.0)
        self.assertEqual(result.loc[200, "risk_quality"], 1.0)
        self.assertEqual(
            result.loc[200, "available_at"],
            result.loc[200, "date"] + pd.Timedelta(days=1),
        )

    def test_volatility_quality_boundaries(self):
        values = pd.Series([1.25, 1.26, 1.75, 1.76, 2.0, 2.01, np.nan])
        quality = V4.OkxCrossAssetBetaV4._volatility_quality(values)
        self.assertEqual(quality.tolist(), [1.0, 0.75, 0.75, 0.5, 0.5, 0.0, 0.0])

    def test_signed_regime_boundaries(self):
        regime = V4.OkxCrossAssetBetaV4._raw_regime
        self.assertEqual(regime(0.50, True), "strong_risk_on")
        self.assertEqual(regime(0.20, True), "risk_on")
        self.assertEqual(regime(0.0, True), "neutral")
        self.assertEqual(regime(-0.20, True), "risk_off")
        self.assertEqual(regime(-0.50, True), "strong_risk_off")
        self.assertEqual(regime(1.0, False), "blocked")

    def test_regime_requires_two_bars_and_uses_hysteresis(self):
        method = V4.OkxCrossAssetBetaV4._regimes_with_hysteresis
        result = method(
            pd.Series([0.30, 0.30, 0.16, 0.16, 0.14, 0.14]),
            pd.Series([True] * 6),
        )
        self.assertEqual(result[:2], ["blocked", "risk_on"])
        self.assertEqual(result[2:4], ["risk_on", "risk_on"])
        self.assertEqual(result[-1], "neutral")

    def test_identical_asset_and_benchmark_have_zero_residual(self):
        dates = pd.date_range("2024-01-01", periods=240, freq="1D", tz="UTC")
        close = 100 * np.exp(np.arange(240) * 0.001)
        raw = pd.DataFrame({"date": dates, "close": close})
        feature = V4.OkxCrossAssetBetaV4._daily_asset_features(raw)
        frames = {
            "QQQ/USDT:USDT": feature,
            "SPY/USDT:USDT": feature,
        }
        result = V4.OkxCrossAssetBetaV4._with_residual_momentum(
            "QQQ/USDT:USDT", feature, frames
        )
        self.assertAlmostEqual(result["residual_momentum"].dropna().iloc[-1], 0.0)

    def test_empty_crypto_frame_fails_closed(self):
        result = V4.OkxCrossAssetBetaV4._with_residual_momentum(
            "BTC/USDT:USDT",
            pd.DataFrame(),
            {},
        )
        self.assertTrue(result.empty)
        self.assertIn("residual_momentum", result)


class SignalAndSelectionTests(unittest.TestCase):
    @staticmethod
    def _signal_frame() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "close_4h": [111.0, 89.0, 89.0],
                "channel_high_60_4h": [110.0, 110.0, 110.0],
                "channel_low_60_4h": [90.0, 90.0, 90.0],
                "channel_low_90_4h": [90.0, 90.0, 90.0],
                "ema30_4h": [105.0, 95.0, 95.0],
                "ema90_4h": [100.0, 100.0, 100.0],
                "adx_4h": [20.0, 25.0, 25.0],
                "volume_4h": [100.0, 100.0, 100.0],
                "volume_median_30_4h": [100.0, 100.0, 100.0],
                "absolute_trend": [0.30, -0.30, -0.30],
                "residual_momentum": [0.30, -0.40, -0.60],
                "beta_regime": ["risk_on", "risk_off", "neutral"],
                "risk_quality": [1.0, 1.0, 1.0],
                "cross_asset_data_fresh": [True, True, True],
                "volume": [1.0, 1.0, 1.0],
            }
        )

    def test_three_signal_models_are_distinct(self):
        strategy = V4.OkxCrossAssetBetaV4()
        result = strategy.populate_entry_trend(self._signal_frame(), {})
        self.assertEqual(result.loc[0, "enter_tag"], "trend_long")
        self.assertEqual(result.loc[1, "enter_tag"], "systemic_short")
        self.assertEqual(result.loc[2, "enter_tag"], "residual_short")
        self.assertEqual(result.loc[0, "enter_long"], 1)
        self.assertEqual(result.loc[1, "enter_short"], 1)
        self.assertEqual(result.loc[2, "enter_short"], 1)

    def test_bottom_twenty_percent_selects_at_least_one_per_group(self):
        snapshots = {
            f"{base}/USDT:USDT": pd.Series({"residual_momentum": residual})
            for base, residual in (
                ("AAPL", -0.8),
                ("AMZN", -0.6),
                ("GOOGL", -0.4),
                ("META", -0.2),
                ("MSFT", 0.0),
                ("NVDA", 0.2),
                ("TSLA", 0.4),
                ("MU", -0.7),
                ("SNDK", -0.1),
            )
        }
        selected = V4.OkxCrossAssetBetaV4._bottom_group_members(snapshots)
        self.assertIn("AAPL/USDT:USDT", selected)
        self.assertIn("MU/USDT:USDT", selected)
        self.assertNotIn("TSLA/USDT:USDT", selected)

    def test_systemic_short_has_priority(self):
        selected = V4.OkxCrossAssetBetaV4._select_v4_candidate(
            [
                {
                    "pair": "QQQ/USDT:USDT",
                    "side": "long",
                    "model": "trend_long",
                    "score": 90,
                    "volatility": 0.2,
                },
                {
                    "pair": "NVDA/USDT:USDT",
                    "side": "short",
                    "model": "systemic_short",
                    "score": 70,
                    "volatility": 0.3,
                    "liquidity": 80,
                },
            ]
        )
        self.assertEqual(selected["model"], "systemic_short")

    def test_risk_on_residual_short_needs_ten_point_advantage(self):
        base = {
            "pair": "QQQ/USDT:USDT",
            "side": "long",
            "model": "trend_long",
            "score": 70,
            "volatility": 0.2,
            "regime": "risk_on",
        }
        weak_short = {
            "pair": "NVDA/USDT:USDT",
            "side": "short",
            "model": "residual_short",
            "score": 79,
            "volatility": 0.3,
            "liquidity": 80,
            "regime": "risk_on",
        }
        selected = V4.OkxCrossAssetBetaV4._select_v4_candidate(
            [base, weak_short]
        )
        self.assertEqual(selected["side"], "long")
        strong_short = dict(weak_short, score=81)
        selected = V4.OkxCrossAssetBetaV4._select_v4_candidate(
            [base, strong_short]
        )
        self.assertEqual(selected["model"], "residual_short")


class RiskAndExitTests(unittest.TestCase):
    def setUp(self):
        self.strategy = V4.OkxCrossAssetBetaV4()
        self.strategy.config = {
            "stake_currency": "USDT",
            "tradable_balance_ratio": 0.50,
            "beta_v4_exit_policy": "channel_only",
        }
        self.strategy.wallets = _Wallets(30)
        self.strategy._limits = lambda: {
            "entries_blocked": False,
            "risk_cap": 0.02,
            "leverage_cap": 10.0,
            "margin_cap": 20.0,
        }
        self.strategy._store_entry_risk_plan = lambda *_args, **_kwargs: None
        self.now = datetime(2026, 7, 27, tzinfo=timezone.utc)

    def _stake(self, pair, tag, side, regime, leverage):
        self.strategy._last_candle = lambda _pair: pd.Series(
            {
                "pair": pair,
                "atr_4h": 1.25,
                "risk_quality": 1.0,
                "beta_regime": regime,
                "enter_tag": tag,
            }
        )
        return self.strategy._custom_stake_amount(
            pair,
            self.now,
            100,
            5,
            1,
            15,
            leverage,
            tag,
            side,
        )

    def test_model_specific_risk_budgets_and_crypto_cap(self):
        self.assertAlmostEqual(
            self._stake(
                "QQQ/USDT:USDT", "trend_long", "long", "strong_risk_on", 2
            ),
            4.5,
        )
        self.assertAlmostEqual(
            self._stake(
                "QQQ/USDT:USDT", "trend_long", "long", "risk_on", 2
            ),
            3.0,
        )
        self.assertAlmostEqual(
            self._stake(
                "QQQ/USDT:USDT", "systemic_short", "short", "risk_off", 2
            ),
            2.25,
        )
        self.assertAlmostEqual(
            self._stake(
                "QQQ/USDT:USDT", "residual_short", "short", "neutral", 2
            ),
            1.5,
        )
        self.assertAlmostEqual(
            self._stake(
                "BTC/USDT:USDT", "trend_long", "long", "strong_risk_on", 3
            ),
            2.0,
        )

    def test_stake_sizing_revalidates_model_side_and_regime(self):
        self.assertEqual(
            self._stake(
                "QQQ/USDT:USDT", "trend_long", "short", "risk_on", 2
            ),
            0.0,
        )
        self.assertEqual(
            self._stake(
                "QQQ/USDT:USDT",
                "systemic_short",
                "short",
                "neutral",
                2,
            ),
            0.0,
        )
        self.assertEqual(
            self._stake(
                "QQQ/USDT:USDT",
                "residual_short",
                "short",
                "strong_risk_on",
                2,
            ),
            0.0,
        )

    def test_delayed_break_even_and_atr_ratchet_are_monotonic(self):
        self.strategy.config["beta_v4_exit_policy"] = "delayed_break_even"
        self.strategy._last_candle = lambda _pair: pd.Series({"atr_4h": 2.0})
        trade = _Trade(max_rate=115.0, min_rate=98.0)
        first = self.strategy.custom_stoploss(
            "QQQ/USDT:USDT", trade, self.now, 110.0, 0.10, False
        )
        self.assertAlmostEqual(first, 107.0)
        trade.max_rate = 112.0
        second = self.strategy.custom_stoploss(
            "QQQ/USDT:USDT", trade, self.now, 108.0, 0.08, False
        )
        self.assertAlmostEqual(second, first)
        self.assertAlmostEqual(trade.data["initial_r_amount"], 0.25)
        self.assertAlmostEqual(trade.data["peak_mfe_r"], 3.0)

    def test_slow_atr_does_not_move_before_two_r(self):
        self.strategy.config["beta_v4_exit_policy"] = "slow_atr"
        self.strategy._last_candle = lambda _pair: pd.Series({"atr_4h": 2.0})
        trade = _Trade(max_rate=109.0, min_rate=98.0)
        before = self.strategy.custom_stoploss(
            "QQQ/USDT:USDT", trade, self.now, 105.0, 0.05, False
        )
        self.assertAlmostEqual(before, 95.0)
        trade.max_rate = 110.0
        after = self.strategy.custom_stoploss(
            "QQQ/USDT:USDT", trade, self.now, 108.0, 0.08, False
        )
        self.assertAlmostEqual(after, 101.0)

    def test_short_stop_ratchet_only_moves_down(self):
        self.strategy.config["beta_v4_exit_policy"] = "delayed_break_even"
        self.strategy._last_candle = lambda _pair: pd.Series({"atr_4h": 1.0})
        trade = _Trade(is_short=True, min_rate=82.0, max_rate=102.0)
        first = self.strategy.custom_stoploss(
            "QQQ/USDT:USDT", trade, self.now, 88.0, 0.12, False
        )
        self.assertAlmostEqual(first, 86.0)
        trade.min_rate = 90.0
        second = self.strategy.custom_stoploss(
            "QQQ/USDT:USDT", trade, self.now, 92.0, 0.08, False
        )
        self.assertAlmostEqual(second, first)


class IsolationContractTests(unittest.TestCase):
    def test_v4_config_and_compose_are_isolated_and_safe(self):
        config_path = (
            DEPLOYMENT_DIR
            / "archive"
            / "v1-v6"
            / "runtime"
            / "config.beta-v4.json"
        )
        with config_path.open(encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertTrue(config["dry_run"])
        self.assertEqual(config["dry_run_wallet"], 30)
        self.assertEqual(config["max_open_trades"], 1)
        self.assertEqual(config["tradable_balance_ratio"], 0.50)
        self.assertEqual(config["beta_v4_exit_policy"], "channel_only")
        compose = (
            DEPLOYMENT_DIR
            / "archive"
            / "v1-v6"
            / "docker-compose.beta-v4.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("trades-beta-v4.sqlite", compose)
        self.assertIn("127.0.0.1:8083:8080", compose)
        self.assertIn("OkxCrossAssetBetaV4", compose)
        self.assertEqual(compose.count('RISK_GUARD_LIVE_APPROVED: "false"'), 2)
        self.assertNotIn('RISK_GUARD_LIVE_APPROVED: "true"', compose)


if __name__ == "__main__":
    unittest.main()
