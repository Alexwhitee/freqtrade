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


def _load_v6_module():
    talib = types.ModuleType("talib")
    talib.abstract = types.ModuleType("talib.abstract")
    persistence = types.ModuleType("freqtrade.persistence")
    persistence.Trade = type(
        "Trade",
        (),
        {"get_open_trades": staticmethod(lambda: [])},
    )
    strategy = types.ModuleType("freqtrade.strategy")
    strategy.CategoricalParameter = _Parameter
    strategy.DecimalParameter = _Parameter
    strategy.IntParameter = _Parameter
    strategy.IStrategy = type("IStrategy", (), {})
    strategy.informative = lambda *_args, **_kwargs: lambda func: func
    strategy.stoploss_from_absolute = lambda stop_price, **_kwargs: stop_price
    strategy.stoploss_from_open = (
        lambda stop_profit, *_args, **_kwargs: stop_profit
    )
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
            "OkxCrossAssetBetaV6",
        ):
            spec = importlib.util.spec_from_file_location(
                name,
                STRATEGY_DIR / f"{name}.py",
            )
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    return loaded["OkxCrossAssetBetaV6"]


V6 = _load_v6_module()


class _Wallets:
    def get_total(self, _currency: str) -> float:
        return 30.0

    def get_total_stake_amount(self) -> float:
        return 15.0


def _candidate(pair, score, group=None, model="trend_long"):
    return {
        "pair": pair,
        "side": "long",
        "model": model,
        "score": score,
        "volatility": 0.2,
        "group": group,
    }


class PortfolioSelectionTests(unittest.TestCase):
    def test_long_only_group_caps_and_correlation_veto(self):
        correlations = {
            frozenset(("QQQ/USDT:USDT", "AAPL/USDT:USDT")): 0.70,
            frozenset(("QQQ/USDT:USDT", "NVDA/USDT:USDT")): 0.75,
            frozenset(("AAPL/USDT:USDT", "NVDA/USDT:USDT")): 0.95,
            frozenset(("QQQ/USDT:USDT", "BTC/USDT:USDT")): 0.30,
            frozenset(("AAPL/USDT:USDT", "BTC/USDT:USDT")): 0.25,
        }
        candidates = [
            _candidate("QQQ/USDT:USDT", 95),
            _candidate("AAPL/USDT:USDT", 90),
            _candidate("NVDA/USDT:USDT", 89),
            _candidate("BTC/USDT:USDT", 85),
            _candidate("ETH/USDT:USDT", 84),
            _candidate("MU/USDT:USDT", 99),
            {
                **_candidate("MSFT/USDT:USDT", 100),
                "side": "short",
            },
        ]

        def lookup(left, right):
            return correlations.get(frozenset((left, right)), 0.20)

        selected = V6.OkxCrossAssetBetaV6._select_v6_candidates(
            candidates,
            correlation_lookup=lookup,
        )
        self.assertEqual(
            [item["pair"] for item in selected],
            [
                "QQQ/USDT:USDT",
                "AAPL/USDT:USDT",
                "BTC/USDT:USDT",
            ],
        )

    def test_missing_correlation_fails_closed_after_first_pair(self):
        selected = V6.OkxCrossAssetBetaV6._select_v6_candidates(
            [
                _candidate("AAPL/USDT:USDT", 90),
                _candidate("BTC/USDT:USDT", 80),
            ],
            correlation_lookup=lambda _left, _right: None,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["pair"], "AAPL/USDT:USDT")

    def test_open_positions_reduce_slots_and_group_capacity(self):
        selected = V6.OkxCrossAssetBetaV6._select_v6_candidates(
            [
                _candidate("AAPL/USDT:USDT", 90),
                _candidate("NVDA/USDT:USDT", 89),
                _candidate("BTC/USDT:USDT", 80),
            ],
            open_pairs=("MSFT/USDT:USDT", "QQQ/USDT:USDT"),
            correlation_lookup=lambda _left, _right: 0.20,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["pair"], "AAPL/USDT:USDT")

    def test_candidate_set_is_stable_within_base_candle(self):
        strategy = V6.OkxCrossAssetBetaV6()
        calls = []
        strategy._open_pairs = lambda: ()
        strategy._pair_correlation = lambda *_args: 0.20

        def candidates(_current_time):
            calls.append(1)
            return [
                _candidate(
                    "QQQ/USDT:USDT",
                    80.0 + len(calls),
                )
            ]

        strategy._runtime_candidates_all = candidates
        first = strategy._stable_v6_candidates(
            datetime(2026, 7, 28, 12, 1, tzinfo=timezone.utc)
        )
        second = strategy._stable_v6_candidates(
            datetime(2026, 7, 28, 12, 59, tzinfo=timezone.utc)
        )
        third = strategy._stable_v6_candidates(
            datetime(2026, 7, 28, 13, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(first, second)
        self.assertNotEqual(second[0]["score"], third[0]["score"])


class SignalAndRiskTests(unittest.TestCase):
    def test_short_and_memory_entries_are_cleared(self):
        strategy = V6.OkxCrossAssetBetaV6()
        base = pd.DataFrame(
            {
                "enter_long": [1],
                "enter_short": [1],
                "enter_tag": ["systemic_short"],
            }
        )
        with patch.object(
            V6.OkxCrossAssetBetaV5,
            "populate_entry_trend",
            side_effect=lambda *_args, **_kwargs: base.copy(),
        ):
            regular = strategy.populate_entry_trend(
                base.copy(),
                {"pair": "AAPL/USDT:USDT"},
            )
            memory = strategy.populate_entry_trend(
                base.copy(),
                {"pair": "MU/USDT:USDT"},
            )
        self.assertEqual(int(regular.loc[0, "enter_short"]), 0)
        self.assertEqual(int(regular.loc[0, "enter_long"]), 1)
        self.assertEqual(int(memory.loc[0, "enter_long"]), 0)
        self.assertIsNone(memory.loc[0, "enter_tag"])

    def test_risk_is_one_third_of_v5_and_short_is_rejected(self):
        strategy = V6.OkxCrossAssetBetaV6()
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

        def stake(pair, tag, side="long"):
            strategy._last_candle = lambda _pair: pd.Series(
                {
                    "pair": pair,
                    "atr_4h": 1.25,
                    "risk_quality": 1.0,
                    "beta_regime": "risk_on",
                    "enter_tag": tag,
                }
            )
            return strategy._custom_stake_amount(
                pair,
                datetime(2026, 7, 28, tzinfo=timezone.utc),
                100.0,
                5.0,
                0.1,
                15.0,
                2.0,
                tag,
                side,
            )

        self.assertAlmostEqual(
            stake("QQQ/USDT:USDT", "trend_long"),
            1.0,
        )
        self.assertAlmostEqual(
            stake("QQQ/USDT:USDT", "continuation_long"),
            0.75,
        )
        self.assertEqual(
            stake("QQQ/USDT:USDT", "systemic_short", "short"),
            0.0,
        )
        self.assertEqual(
            stake("MU/USDT:USDT", "trend_long"),
            0.0,
        )

    def test_minimum_stake_is_not_bypassed(self):
        strategy = V6.OkxCrossAssetBetaV6()
        strategy.config = {"stake_currency": "USDT"}
        strategy.wallets = _Wallets()
        strategy._limits = lambda: {
            "entries_blocked": False,
            "risk_cap": 0.02,
            "margin_cap": 20.0,
        }
        strategy._store_entry_risk_plan = lambda *_args, **_kwargs: None
        strategy._last_candle = lambda _pair: pd.Series(
            {
                "pair": "QQQ/USDT:USDT",
                "atr_4h": 1.25,
                "risk_quality": 1.0,
                "beta_regime": "risk_on",
                "enter_tag": "trend_long",
            }
        )
        stake = strategy._custom_stake_amount(
            "QQQ/USDT:USDT",
            datetime(2026, 7, 28, tzinfo=timezone.utc),
            100.0,
            5.0,
            1.01,
            15.0,
            2.0,
            "trend_long",
            "long",
        )
        self.assertEqual(stake, 0.0)


class IsolationTests(unittest.TestCase):
    def test_v6_runtime_is_isolated_and_safe(self):
        config = json.loads(
            (
                DEPLOYMENT_DIR
                / "archive"
                / "v1-v6"
                / "runtime"
                / "config.beta-v6.json"
            ).read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(config["dry_run"])
        self.assertEqual(config["dry_run_wallet"], 80)
        self.assertEqual(config["max_open_trades"], 3)
        self.assertEqual(
            config["beta_v6_exit_policy"],
            "channel_only",
        )
        whitelist = set(config["exchange"]["pair_whitelist"])
        self.assertNotIn("MU/USDT:USDT", whitelist)
        self.assertNotIn("SNDK/USDT:USDT", whitelist)
        self.assertIn("BTC/USDT:USDT", whitelist)

        compose = (
            DEPLOYMENT_DIR
            / "archive"
            / "v1-v6"
            / "docker-compose.beta-v6.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8085:8080", compose)
        self.assertIn("trades-beta-v6.sqlite", compose)
        self.assertIn("OkxCrossAssetBetaV6", compose)
        self.assertNotIn("beta-v5-state.json", compose)
        self.assertEqual(
            compose.count('RISK_GUARD_LIVE_APPROVED: "false"'),
            2,
        )

    def test_v5_baseline_hashes_are_frozen(self):
        manifest = json.loads(
            (
                DEPLOYMENT_DIR
                / "archive"
                / "v1-v6"
                / "baselines"
                / "v6_baseline_manifest.json"
            ).read_text(encoding="utf-8")
        )
        for relative, expected in manifest["files"].items():
            actual = hashlib.sha256(
                (DEPLOYMENT_DIR / relative).read_bytes()
            ).hexdigest()
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
