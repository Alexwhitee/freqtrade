from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd


DEPLOYMENT_DIR = Path(__file__).parents[1]
STRATEGY_DIR = DEPLOYMENT_DIR / "runtime" / "strategies"
CALENDAR_PATH = DEPLOYMENT_DIR / "runtime" / "market_calendars.beta.json"


class _Parameter:
    def __init__(self, *values, default=None, **kwargs):
        self.value = default
        self.range = values[0] if len(values) == 1 and isinstance(values[0], list) else [default]


def _load_v3_module():
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
        v1_spec = importlib.util.spec_from_file_location(
            "OkxAggressiveTrendV1", STRATEGY_DIR / "OkxAggressiveTrendV1.py"
        )
        v1_module = importlib.util.module_from_spec(v1_spec)
        assert v1_spec and v1_spec.loader
        sys.modules[v1_spec.name] = v1_module
        v1_spec.loader.exec_module(v1_module)

        v2_spec = importlib.util.spec_from_file_location(
            "OkxAggressiveTrendV2", STRATEGY_DIR / "OkxAggressiveTrendV2.py"
        )
        v2_module = importlib.util.module_from_spec(v2_spec)
        assert v2_spec and v2_spec.loader
        sys.modules[v2_spec.name] = v2_module
        v2_spec.loader.exec_module(v2_module)

        v3_spec = importlib.util.spec_from_file_location(
            "OkxCrossAssetBetaV3", STRATEGY_DIR / "OkxCrossAssetBetaV3.py"
        )
        v3_module = importlib.util.module_from_spec(v3_spec)
        assert v3_spec and v3_spec.loader
        sys.modules[v3_spec.name] = v3_module
        v3_spec.loader.exec_module(v3_module)
    return v3_module


V3 = _load_v3_module()


class _Wallets:
    def __init__(self, equity: float):
        self.equity = equity

    def get_total_stake_amount(self) -> float:
        return self.equity


class _DailyProvider:
    def __init__(self, missing=None):
        self.missing = set(missing or [])
        self.dates = pd.date_range("2025-01-01", periods=100, freq="1D", tz="UTC")

    def get_pair_dataframe(self, pair, timeframe, **kwargs):
        if pair in self.missing:
            return pd.DataFrame()
        base = 100 + pd.Series(range(len(self.dates)), dtype=float)
        return pd.DataFrame({"date": self.dates, "close": base.to_numpy()})


class AssetContractTests(unittest.TestCase):
    def test_universe_classification_and_risk_caps(self):
        strategy = V3.OkxCrossAssetBetaV3()
        qqq = strategy.asset_spec("QQQ/USDT:USDT")
        btc = strategy.asset_spec("BTC/USDT:USDT")
        spy = strategy.asset_spec("SPY/USDT:USDT")
        samsung = strategy.asset_spec("SAMSUNG/USDT:USDT")
        self.assertEqual((qqq.group, qqq.session, qqq.max_leverage, qqq.stop_max), ("benchmark", "us", 2.0, 0.06))
        self.assertEqual((btc.group, btc.session, btc.max_leverage, btc.stop_max), ("crypto", "always", 3.0, 0.08))
        self.assertFalse(spy.tradable)
        self.assertEqual((samsung.group, samsung.session), ("memory", "kr"))
        self.assertTrue(set(V3.OBSERVATION_PAIRS).isdisjoint(V3.TRADABLE_PAIRS))

    def test_beta_config_is_independent_and_dry_run(self):
        with (DEPLOYMENT_DIR / "runtime" / "config.beta.json").open(encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertTrue(config["dry_run"])
        self.assertEqual(config["max_open_trades"], 1)
        self.assertEqual(config["stake_amount"], 5)
        self.assertNotIn("SPY/USDT:USDT", config["exchange"]["pair_whitelist"])
        compose = (DEPLOYMENT_DIR / "docker-compose.beta.yml").read_text(encoding="utf-8")
        self.assertIn("trades-beta-v3.sqlite", compose)
        self.assertIn("127.0.0.1:8082:8080", compose)
        self.assertIn('RISK_GUARD_LIVE_APPROVED: "false"', compose)

    def test_reviewed_market_snapshot_covers_every_input(self):
        with (
            DEPLOYMENT_DIR / "runtime" / "okx_beta_markets.snapshot.json"
        ).open(encoding="utf-8") as handle:
            snapshot = json.load(handle)
        markets = {row["symbol"]: row for row in snapshot["markets"]}
        self.assertTrue(set(V3.BETA_INPUT_PAIRS).issubset(markets))
        self.assertTrue(set(V3.OBSERVATION_PAIRS).issubset(markets))
        for pair in (*V3.BETA_INPUT_PAIRS, *V3.OBSERVATION_PAIRS):
            self.assertTrue(markets[pair]["active"])
            self.assertTrue(markets[pair]["linear"])
            self.assertTrue(markets[pair]["swap"])
            self.assertGreater(markets[pair]["amount_min"], 0)


class MarketSessionTests(unittest.TestCase):
    def setUp(self):
        self.calendar = V3.MarketSessionCalendar(CALENDAR_PATH)

    def test_us_dst_open_window_and_weekend(self):
        self.assertTrue(
            self.calendar.is_open_for_entry(
                "us", datetime(2026, 7, 6, 13, 50, tzinfo=timezone.utc)
            )
        )
        self.assertFalse(
            self.calendar.is_open_for_entry(
                "us", datetime(2026, 7, 6, 13, 44, tzinfo=timezone.utc)
            )
        )
        self.assertFalse(
            self.calendar.is_open_for_entry(
                "us", datetime(2026, 7, 5, 15, 0, tzinfo=timezone.utc)
            )
        )

    def test_us_winter_dst_and_holiday(self):
        self.assertTrue(
            self.calendar.is_open_for_entry(
                "us", datetime(2026, 1, 5, 14, 50, tzinfo=timezone.utc)
            )
        )
        self.assertFalse(
            self.calendar.is_open_for_entry(
                "us", datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)
            )
        )

    def test_kr_open_and_reviewed_holiday(self):
        self.assertTrue(
            self.calendar.is_open_for_entry(
                "kr", datetime(2026, 7, 27, 0, 20, tzinfo=timezone.utc)
            )
        )
        self.assertFalse(
            self.calendar.is_open_for_entry(
                "kr", datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc)
            )
        )
        self.assertFalse(
            self.calendar.is_open_for_entry(
                "kr", datetime(2026, 6, 3, 1, 0, tzinfo=timezone.utc)
            )
        )
        self.assertTrue(
            self.calendar.is_open_for_entry(
                "always", datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc)
            )
        )


class BetaStateTests(unittest.TestCase):
    def test_regime_boundaries(self):
        regime = V3.OkxCrossAssetBetaV3._regime
        self.assertEqual(regime(70, True), "strong_risk_on")
        self.assertEqual(regime(55, True), "risk_on")
        self.assertEqual(regime(40, True), "neutral")
        self.assertEqual(regime(25, True), "risk_off")
        self.assertEqual(regime(24.99, True), "blocked")
        self.assertEqual(regime(99, False), "blocked")

    def test_daily_features_are_available_only_after_close(self):
        dates = pd.date_range("2025-01-01", periods=80, freq="1D", tz="UTC")
        frame = pd.DataFrame({"date": dates, "close": range(100, 180)})
        result = V3.OkxCrossAssetBetaV3._daily_asset_features(frame)
        self.assertTrue((result["available_at"] == result["date"] + pd.Timedelta(days=1)).all())
        self.assertTrue(pd.isna(result.loc[19, "mom20"]))
        self.assertFalse(pd.isna(result.loc[60, "mom60"]))

    def test_complete_inputs_produce_non_blocked_state(self):
        strategy = V3.OkxCrossAssetBetaV3()
        strategy.dp = _DailyProvider()
        dates = pd.Series(pd.date_range("2025-04-05", periods=3, freq="1h", tz="UTC"))
        frame = strategy._beta_regime_frame(dates)
        self.assertTrue(frame.iloc[-1]["cross_asset_data_fresh"])
        self.assertNotEqual(frame.iloc[-1]["beta_regime"], "blocked")
        self.assertGreaterEqual(frame.iloc[-1]["valid_mag7"], 5)
        self.assertGreaterEqual(frame.iloc[-1]["valid_memory"], 2)

    def test_missing_required_assets_fails_closed(self):
        strategy = V3.OkxCrossAssetBetaV3()
        strategy.dp = _DailyProvider(missing={"QQQ/USDT:USDT"})
        dates = pd.Series(pd.date_range("2025-04-05", periods=2, freq="1h", tz="UTC"))
        frame = strategy._beta_regime_frame(dates)
        self.assertFalse(frame.iloc[-1]["cross_asset_data_fresh"])
        self.assertEqual(frame.iloc[-1]["beta_regime"], "blocked")


class RankingAndRiskTests(unittest.TestCase):
    def test_tie_break_prefers_qqq_then_btc_then_lower_volatility(self):
        select = V3.OkxCrossAssetBetaV3._select_candidate
        selected = select(
            [
                {"pair": "NVDA/USDT:USDT", "score": 90, "volatility": 0.2},
                {"pair": "QQQ/USDT:USDT", "score": 86, "volatility": 0.5},
            ]
        )
        self.assertEqual(selected["pair"], "QQQ/USDT:USDT")
        selected = select(
            [
                {"pair": "ETH/USDT:USDT", "score": 90, "volatility": 0.1},
                {"pair": "BTC/USDT:USDT", "score": 87, "volatility": 0.5},
            ]
        )
        self.assertEqual(selected["pair"], "BTC/USDT:USDT")
        selected = select(
            [
                {"pair": "NVDA/USDT:USDT", "score": 90, "volatility": 0.4},
                {"pair": "MSFT/USDT:USDT", "score": 88, "volatility": 0.2},
            ]
        )
        self.assertEqual(selected["pair"], "MSFT/USDT:USDT")

    def test_relative_strength_is_ranked_cross_sectionally(self):
        candidates = [
            {
                "pair": "QQQ/USDT:USDT",
                "score": 35.0,
                "relative_raw": 20.0,
                "execution_raw": 50.0,
                "funding_quality": 50.0,
                "liquidity_quality": 50.0,
                "spread_quality": 50.0,
            },
            {
                "pair": "NVDA/USDT:USDT",
                "score": 50.0,
                "relative_raw": 80.0,
                "execution_raw": 50.0,
                "funding_quality": 50.0,
                "liquidity_quality": 50.0,
                "spread_quality": 50.0,
            },
        ]
        result = V3.OkxCrossAssetBetaV3._finalize_candidate_scores(candidates)
        self.assertGreater(result[1]["score"], result[0]["score"])

    def test_stage_one_leverage_and_stake_limits(self):
        strategy = V3.OkxCrossAssetBetaV3()
        strategy.wallets = _Wallets(30)
        strategy._limits = lambda: {
            "entries_blocked": False,
            "risk_cap": 0.02,
            "leverage_cap": 10.0,
            "margin_cap": 20.0,
        }
        candle = pd.Series(
            {
                "pair": "QQQ/USDT:USDT",
                "atr_4h": 2.0,
                "beta_regime": "strong_risk_on",
            }
        )
        strategy._last_candle = lambda _pair: candle
        now = datetime(2026, 7, 27, tzinfo=timezone.utc)
        self.assertEqual(
            strategy.leverage("QQQ/USDT:USDT", now, 100, 1, 10, "long_beta", "long"),
            2.0,
        )
        self.assertEqual(
            strategy.leverage("BTC/USDT:USDT", now, 100, 1, 10, "long_beta", "long"),
            3.0,
        )
        stake = strategy._custom_stake_amount(
            "QQQ/USDT:USDT", now, 100, 5, 1, 30, 2, "long_beta", "long"
        )
        self.assertLessEqual(stake, 5.0)
        self.assertLessEqual(stake, 30 - 15)
        rejected = strategy._custom_stake_amount(
            "QQQ/USDT:USDT", now, 100, 5, 6, 30, 2, "long_beta", "long"
        )
        self.assertEqual(rejected, 0.0)

    def test_signal_direction_respects_beta_boundaries(self):
        strategy = V3.OkxCrossAssetBetaV3()
        frame = pd.DataFrame(
            {
                "close_4h": [111, 89],
                "channel_high_30_4h": [110, 110],
                "channel_low_80_4h": [90, 90],
                "ema30_4h": [105, 95],
                "ema60_4h": [100, 100],
                "adx_4h": [20, 25],
                "volume_4h": [100, 100],
                "volume_median_30_4h": [100, 100],
                "beta_score": [55, 40],
                "cross_asset_data_fresh": [True, True],
                "volume": [1, 1],
            }
        )
        result = strategy.populate_entry_trend(frame, {})
        self.assertEqual(result.loc[0, "enter_long"], 1)
        self.assertEqual(result.loc[1, "enter_short"], 1)


if __name__ == "__main__":
    unittest.main()
