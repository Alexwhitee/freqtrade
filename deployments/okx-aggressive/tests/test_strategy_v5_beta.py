from __future__ import annotations

import hashlib
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


class _Parameter:
    def __init__(self, *values, default=None, **kwargs):
        self.value = default
        self.range = (
            values[0]
            if len(values) == 1 and isinstance(values[0], list)
            else [default]
        )


def _load_v5_module():
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
            "OkxCrossAssetBetaV5",
        ):
            spec = importlib.util.spec_from_file_location(
                name, STRATEGY_DIR / f"{name}.py"
            )
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    return loaded["OkxCrossAssetBetaV5"]


V5 = _load_v5_module()


class _Wallets:
    def get_total(self, _currency: str) -> float:
        return 30.0

    def get_total_stake_amount(self) -> float:
        return 15.0


class ContinuationSignalTests(unittest.TestCase):
    def test_pullback_state_uses_only_prior_completed_4h_bars(self):
        base = pd.DataFrame(
            {
                "atr": [2.0] * 5,
                "ema30": [105.0, 104.0, 104.0, 104.0, 104.0],
                "ema90": [100.0] * 5,
                "close": [108.0, 104.0, 106.0, 107.0, 107.0],
                "low": [107.0, 103.5, 105.0, 106.0, 106.0],
                "high": [109.0, 105.0, 107.0, 108.0, 108.0],
            }
        )
        strategy = V5.OkxCrossAssetBetaV5()
        with patch.object(
            V5.OkxCrossAssetBetaV4,
            "populate_indicators_4h",
            return_value=base.copy(),
        ):
            result = strategy.populate_indicators_4h(base.copy(), {})
        self.assertFalse(bool(result.loc[1, "recent_long_pullback"]))
        self.assertTrue(bool(result.loc[2, "recent_long_pullback"]))
        self.assertEqual(result.loc[2, "previous_high"], base.loc[1, "high"])

        changed_future = base.copy()
        changed_future.loc[4, ["close", "low", "high"]] = [50.0, 40.0, 120.0]
        with patch.object(
            V5.OkxCrossAssetBetaV4,
            "populate_indicators_4h",
            return_value=changed_future,
        ):
            future_result = strategy.populate_indicators_4h(changed_future, {})
        pd.testing.assert_series_equal(
            result.loc[:3, "recent_long_pullback"],
            future_result.loc[:3, "recent_long_pullback"],
        )

    @staticmethod
    def _entry_frame() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "close_4h": [105.0, 95.0],
                "previous_high_4h": [106.0, 104.0],
                "previous_low_4h": [96.0, 94.0],
                "previous_close_4h": [104.0, 96.0],
                "recent_long_pullback_4h": [True, False],
                "recent_short_pullback_4h": [False, True],
                "channel_high_60_4h": [110.0, 110.0],
                "channel_low_60_4h": [90.0, 90.0],
                "channel_low_90_4h": [90.0, 90.0],
                "ema30_4h": [103.0, 97.0],
                "ema90_4h": [100.0, 100.0],
                "atr_4h": [2.0, 2.0],
                "adx_4h": [22.0, 26.0],
                "volume_4h": [100.0, 100.0],
                "volume_median_30_4h": [100.0, 100.0],
                "absolute_trend": [0.35, -0.35],
                "residual_momentum": [0.20, -0.30],
                "beta_regime": ["risk_on", "risk_off"],
                "risk_quality": [1.0, 1.0],
                "cross_asset_data_fresh": [True, True],
                "volume": [1.0, 1.0],
            }
        )

    def test_continuation_models_do_not_loosen_v4_breakouts(self):
        strategy = V5.OkxCrossAssetBetaV5()
        result = strategy.populate_entry_trend(self._entry_frame(), {})
        self.assertEqual(result.loc[0, "enter_tag"], "continuation_long")
        self.assertEqual(result.loc[0, "enter_long"], 1)
        self.assertEqual(result.loc[1, "enter_tag"], "systemic_pullback_short")
        self.assertEqual(result.loc[1, "enter_short"], 1)

        breakout = self._entry_frame().iloc[[0]].copy()
        breakout.loc[:, "close_4h"] = 111.0
        breakout_result = strategy.populate_entry_trend(breakout, {})
        self.assertEqual(breakout_result.iloc[0]["enter_tag"], "trend_long")

    def test_continuation_trigger_score_is_positive_only_after_reclaim(self):
        strategy = V5.OkxCrossAssetBetaV5
        frame = self._entry_frame()
        components = strategy._candidate_components_v5(
            frame, "continuation_long"
        )
        self.assertGreater(components["breakout"].iloc[0], 0.0)
        self.assertEqual(components["breakout"].iloc[1], 0.0)


class CandidateAndRiskTests(unittest.TestCase):
    def test_systemic_models_have_priority_and_breakout_wins_tie(self):
        selected = V5.OkxCrossAssetBetaV5._select_v5_candidate(
            [
                {
                    "pair": "QQQ/USDT:USDT",
                    "side": "long",
                    "model": "trend_long",
                    "score": 95.0,
                    "volatility": 0.2,
                },
                {
                    "pair": "NVDA/USDT:USDT",
                    "side": "short",
                    "model": "systemic_pullback_short",
                    "score": 70.0,
                    "liquidity": 80.0,
                    "volatility": 0.3,
                },
            ]
        )
        self.assertEqual(selected["model"], "systemic_pullback_short")

        selected = V5.OkxCrossAssetBetaV5._select_v5_candidate(
            [
                {
                    "pair": "QQQ/USDT:USDT",
                    "side": "long",
                    "model": "continuation_long",
                    "score": 90.0,
                    "volatility": 0.2,
                },
                {
                    "pair": "QQQ/USDT:USDT",
                    "side": "long",
                    "model": "trend_long",
                    "score": 90.0,
                    "volatility": 0.2,
                },
            ]
        )
        self.assertEqual(selected["model"], "trend_long")

    def test_candidate_selection_is_stable_within_base_candle(self):
        strategy = V5.OkxCrossAssetBetaV5()
        calls = []

        def candidates(_current_time):
            calls.append(1)
            return [
                {
                    "pair": "QQQ/USDT:USDT",
                    "side": "long",
                    "model": "continuation_long",
                    "score": 80.0 + len(calls),
                    "volatility": 0.2,
                }
            ]

        strategy._runtime_candidates_all = candidates
        first = strategy._stable_v5_candidate(
            datetime(2026, 7, 28, 12, 1, tzinfo=timezone.utc)
        )
        second = strategy._stable_v5_candidate(
            datetime(2026, 7, 28, 12, 59, tzinfo=timezone.utc)
        )
        third = strategy._stable_v5_candidate(
            datetime(2026, 7, 28, 13, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(first, second)
        self.assertNotEqual(second["score"], third["score"])

    def test_new_models_use_lower_risk_budgets(self):
        strategy = V5.OkxCrossAssetBetaV5()
        strategy.config = {
            "stake_currency": "USDT",
            "tradable_balance_ratio": 0.50,
        }
        strategy.wallets = _Wallets()
        strategy._limits = lambda: {
            "entries_blocked": False,
            "risk_cap": 0.02,
            "leverage_cap": 10.0,
            "margin_cap": 20.0,
        }
        strategy._store_entry_risk_plan = lambda *_args, **_kwargs: None

        def stake(tag, side, regime):
            strategy._last_candle = lambda _pair: pd.Series(
                {
                    "pair": "QQQ/USDT:USDT",
                    "atr_4h": 1.25,
                    "risk_quality": 1.0,
                    "beta_regime": regime,
                    "enter_tag": tag,
                }
            )
            return strategy._custom_stake_amount(
                "QQQ/USDT:USDT",
                datetime(2026, 7, 28, tzinfo=timezone.utc),
                100.0,
                5.0,
                1.0,
                15.0,
                2.0,
                tag,
                side,
            )

        self.assertAlmostEqual(stake("trend_long", "long", "risk_on"), 3.0)
        self.assertAlmostEqual(
            stake("continuation_long", "long", "risk_on"), 2.25
        )
        self.assertAlmostEqual(
            stake("systemic_pullback_short", "short", "risk_off"), 1.5
        )


class IsolationTests(unittest.TestCase):
    def test_v5_runtime_is_isolated_and_approvals_are_false(self):
        config = json.loads(
            (
                DEPLOYMENT_DIR
                / "archive"
                / "v1-v6"
                / "runtime"
                / "config.beta-v5.json"
            ).read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(config["dry_run"])
        self.assertEqual(config["dry_run_wallet"], 30)
        self.assertEqual(config["max_open_trades"], 1)
        self.assertEqual(config["beta_v5_exit_policy"], "channel_only")

        compose = (
            DEPLOYMENT_DIR
            / "archive"
            / "v1-v6"
            / "docker-compose.beta-v5.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8084:8080", compose)
        self.assertIn("trades-beta-v5.sqlite", compose)
        self.assertIn("OkxCrossAssetBetaV5", compose)
        self.assertNotIn("beta-v4-state.json", compose)
        self.assertEqual(compose.count('RISK_GUARD_LIVE_APPROVED: "false"'), 2)

    def test_v4_baseline_hashes_are_frozen(self):
        manifest = json.loads(
            (
                DEPLOYMENT_DIR
                / "archive"
                / "v1-v6"
                / "baselines"
                / "v5_baseline_manifest.json"
            ).read_text(
                encoding="utf-8"
            )
        )
        for relative, expected in manifest["files"].items():
            actual = hashlib.sha256(
                (DEPLOYMENT_DIR / relative).read_bytes()
            ).hexdigest()
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
