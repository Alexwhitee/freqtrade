from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd


DEPLOYMENT_DIR = Path(__file__).parents[1]
STRATEGY_DIR = DEPLOYMENT_DIR / "runtime" / "strategies"


class _Parameter:
    def __init__(self, *values, default=None, **kwargs):
        self.value = default
        self.range = [default]


def _load_v7_module():
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
            "OkxCrossAssetBetaV7",
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
    return loaded["OkxCrossAssetBetaV7"], persistence


V7, PERSISTENCE = _load_v7_module()


class _Wallets:
    def get_total(self, _currency: str) -> float:
        return 80.0

    def get_total_stake_amount(self) -> float:
        return 40.0


class _Trade:
    entry_side = "buy"
    is_short = False

    def __init__(
        self,
        pair="AAPL/USDT:USDT",
        stake_amount=4.0,
        leverage=2.0,
        custom_data=None,
    ):
        self.id = 1
        self.pair = pair
        self.stake_amount = stake_amount
        self.leverage = leverage
        self.custom_data = dict(custom_data or {})

    def get_custom_data(self, key, default=None):
        return self.custom_data.get(key, default)

    def set_custom_data(self, key, value):
        self.custom_data[key] = value


def _strategy(
    ledger_path: Path | None = None,
    market_snapshot_path: Path | None = None,
):
    strategy = V7.OkxCrossAssetBetaV7()
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
    strategy._open_trades = lambda: []
    strategy._market_snapshot_path = lambda: (
        market_snapshot_path
        or DEPLOYMENT_DIR / "runtime" / "okx_beta_markets.snapshot.json"
    )
    if ledger_path is None:
        strategy._entry_risk_plans = {}
        strategy._v7_risk_ledger_valid = True
        strategy._persist_risk_plans = lambda: None
    else:
        strategy._risk_ledger_path = lambda: ledger_path
    return strategy


def _candle(pair, regime="risk_on", rate=100.0):
    return pd.Series(
        {
            "pair": pair,
            "close": rate,
            "atr_4h": rate * 0.01,
            "risk_quality": 1.0,
            "beta_regime": regime,
            "enter_tag": "trend_long",
        }
    )


class CapitalAwareRiskTests(unittest.TestCase):
    def test_public_stake_callback_fails_closed_on_runtime_error(self):
        strategy = _strategy()
        strategy._limits = lambda: (
            _ for _ in ()
        ).throw(RuntimeError("db"))
        stake = strategy.custom_stake_amount(
            "AAPL/USDT:USDT",
            datetime(2026, 7, 29, tzinfo=timezone.utc),
            100.0,
            5.0,
            0.1,
            20.0,
            2.0,
            "trend_long",
            "long",
        )
        self.assertEqual(stake, 0.0)

    def test_ordinary_risk_is_half_v5_budget(self):
        strategy = _strategy()
        strategy._last_candle = lambda pair: _candle(pair)
        stake = strategy._custom_stake_amount(
            "AAPL/USDT:USDT",
            datetime(2026, 7, 29, tzinfo=timezone.utc),
            100.0,
            5.0,
            0.1,
            20.0,
            2.0,
            "trend_long",
            "long",
        )
        self.assertAlmostEqual(stake, 4.0)

    def test_pending_plans_cannot_exceed_aggregate_risk(self):
        strategy = _strategy()
        strategy._last_candle = lambda pair: _candle(
            pair,
            regime="strong_risk_on",
        )
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        stakes = [
            strategy._custom_stake_amount(
                pair,
                now,
                100.0,
                5.0,
                0.1,
                20.0,
                2.0,
                "trend_long",
                "long",
            )
            for pair in (
                "AAPL/USDT:USDT",
                "AMZN/USDT:USDT",
                "QQQ/USDT:USDT",
            )
        ]
        self.assertEqual(stakes, [5.0, 5.0, 2.0])
        total_risk = sum(stakes) * 2.0 * 0.025 / 80.0
        self.assertAlmostEqual(total_risk, 0.0075)

    def test_public_rejection_releases_reserved_risk(self):
        strategy = _strategy()
        pair = "AAPL/USDT:USDT"
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        candle = _candle(pair)
        candle["cross_asset_data_fresh"] = True
        strategy._last_candle = lambda _pair: candle
        strategy._calendar = lambda: types.SimpleNamespace(
            is_open_for_entry=lambda *_args: True
        )
        strategy._critical_data_fresh = lambda _time: True
        strategy._is_backtest = lambda: False
        strategy._stable_v6_candidates = lambda _time: []
        strategy._store_entry_risk_plan(
            pair,
            "long",
            0.025,
            100.0,
            now,
        )
        strategy._risk_plans()[(pair, "long")]["risk_fraction"] = 0.0025

        accepted = strategy.confirm_trade_entry(
            pair,
            "market",
            0.04,
            100.0,
            "GTC",
            now,
            "trend_long",
            "long",
        )

        self.assertFalse(accepted)
        self.assertNotIn((pair, "long"), strategy._risk_plans())

    def test_public_confirmation_exception_releases_reserved_risk(self):
        strategy = _strategy()
        pair = "AAPL/USDT:USDT"
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        strategy._calendar = lambda: (
            _ for _ in ()
        ).throw(RuntimeError("calendar"))
        strategy._is_backtest = lambda: False
        strategy._store_entry_risk_plan(
            pair,
            "long",
            0.025,
            100.0,
            now,
        )

        accepted = strategy.confirm_trade_entry(
            pair,
            "market",
            0.04,
            100.0,
            "GTC",
            now,
            "trend_long",
            "long",
        )

        self.assertFalse(accepted)
        self.assertNotIn((pair, "long"), strategy._risk_plans())

    def test_unverified_open_trade_blocks_new_entries(self):
        strategy = _strategy()
        pair = "AAPL/USDT:USDT"
        trade = _Trade(pair=pair)
        strategy._open_trades = lambda: [trade]
        strategy._last_candle = lambda _pair: _candle(pair)

        stake = strategy._custom_stake_amount(
            "AMZN/USDT:USDT",
            datetime(2026, 7, 29, tzinfo=timezone.utc),
            100.0,
            5.0,
            0.1,
            20.0,
            2.0,
            "trend_long",
            "long",
        )

        self.assertEqual(stake, 0.0)
        self.assertTrue(
            trade.custom_data[strategy._UNVERIFIED_STOP_KEY]
        )

    def test_entry_fill_removes_persisted_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = Path(temp_dir) / "risk-ledger.json"
            strategy = _strategy(ledger_path=ledger)
            pair = "AAPL/USDT:USDT"
            now = datetime(2026, 7, 29, tzinfo=timezone.utc)
            trade = _Trade(pair=pair)
            order = types.SimpleNamespace(ft_order_side="buy")
            strategy._store_entry_risk_plan(
                pair,
                "long",
                0.04,
                100.0,
                now,
                risk_fraction=0.0025,
            )

            strategy.order_filled(pair, trade, order, now)

            payload = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(payload["reservations"], [])
            self.assertEqual(payload["pending_risk_fraction"], 0.0)

    def test_expired_plan_is_removed_from_persisted_ledger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = Path(temp_dir) / "risk-ledger.json"
            strategy = _strategy(ledger_path=ledger)
            pair = "AAPL/USDT:USDT"
            now = datetime(2026, 7, 29, tzinfo=timezone.utc)
            strategy._store_entry_risk_plan(
                pair,
                "long",
                0.04,
                100.0,
                now,
                risk_fraction=0.0025,
            )

            plan = strategy._entry_risk_plan(
                pair,
                "long",
                now + strategy._ENTRY_RISK_PLAN_TTL + timedelta(seconds=1),
            )

            payload = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertIsNone(plan)
            self.assertEqual(payload["reservations"], [])

    def test_entry_fill_persists_verified_stop_and_releases_plan(self):
        strategy = _strategy()
        pair = "AAPL/USDT:USDT"
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        trade = _Trade(pair=pair)
        order = types.SimpleNamespace(ft_order_side="buy")
        strategy._store_entry_risk_plan(
            pair,
            "long",
            0.04,
            100.0,
            now,
        )

        strategy.order_filled(pair, trade, order, now)

        self.assertEqual(
            trade.custom_data["initial_stop_distance"],
            0.04,
        )
        self.assertFalse(
            trade.custom_data[strategy._UNVERIFIED_STOP_KEY]
        )
        self.assertNotIn((pair, "long"), strategy._risk_plans())

    def test_orphaned_plan_is_released_after_order_disappears(self):
        strategy = _strategy()
        pair = "AAPL/USDT:USDT"
        strategy._store_entry_risk_plan(
            pair,
            "long",
            0.04,
            100.0,
            datetime(2026, 7, 29, tzinfo=timezone.utc),
        )

        strategy._reconcile_entry_risk_plans(
            datetime(2026, 7, 29, 0, 10, 1, tzinfo=timezone.utc)
        )

        self.assertNotIn((pair, "long"), strategy._risk_plans())

    def test_recent_orphaned_plan_is_retained_during_order_grace(self):
        strategy = _strategy()
        pair = "AAPL/USDT:USDT"
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        strategy._store_entry_risk_plan(pair, "long", 0.04, 100.0, now)

        strategy._reconcile_entry_risk_plans(now + timedelta(minutes=1))

        self.assertIn((pair, "long"), strategy._risk_plans())

    def test_risk_plan_survives_strategy_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = Path(temp_dir) / "risk-ledger.json"
            strategy = _strategy(ledger_path=ledger)
            strategy._last_candle = lambda pair: _candle(pair)
            now = datetime(2026, 7, 29, tzinfo=timezone.utc)

            stake = strategy._custom_stake_amount(
                "AAPL/USDT:USDT",
                now,
                100.0,
                5.0,
                0.1,
                20.0,
                2.0,
                "trend_long",
                "long",
            )
            restarted = _strategy(ledger_path=ledger)
            plan = restarted._risk_plans()[("AAPL/USDT:USDT", "long")]

            self.assertEqual(stake, 4.0)
            self.assertAlmostEqual(plan["risk_fraction"], 0.0025)
            payload = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertTrue(payload["valid"])
            self.assertEqual(len(payload["reservations"]), 1)

    def test_corrupt_risk_ledger_blocks_new_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = Path(temp_dir) / "risk-ledger.json"
            ledger.write_text("not-json", encoding="utf-8")
            strategy = _strategy(ledger_path=ledger)
            strategy._last_candle = lambda pair: _candle(pair)

            stake = strategy._custom_stake_amount(
                "AAPL/USDT:USDT",
                datetime(2026, 7, 29, tzinfo=timezone.utc),
                100.0,
                5.0,
                0.1,
                20.0,
                2.0,
                "trend_long",
                "long",
            )

            self.assertEqual(stake, 0.0)
            self.assertFalse(strategy._v7_risk_ledger_valid)

    def test_risk_ledger_write_failure_fails_stake_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            blocking_file = Path(temp_dir) / "not-a-directory"
            blocking_file.write_text("block", encoding="utf-8")
            strategy = _strategy(
                ledger_path=blocking_file / "risk-ledger.json"
            )
            strategy._last_candle = lambda pair: _candle(pair)

            stake = strategy.custom_stake_amount(
                "AAPL/USDT:USDT",
                datetime(2026, 7, 29, tzinfo=timezone.utc),
                100.0,
                5.0,
                0.1,
                20.0,
                2.0,
                "trend_long",
                "long",
            )

            self.assertEqual(stake, 0.0)
            self.assertFalse(strategy._v7_risk_ledger_valid)

    def test_stale_market_snapshot_blocks_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "markets.json"
            payload = json.loads(
                (
                    DEPLOYMENT_DIR
                    / "runtime"
                    / "okx_beta_markets.snapshot.json"
                ).read_text(encoding="utf-8")
            )
            payload["captured_at"] = "2026-01-01T00:00:00+00:00"
            snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
            strategy = _strategy(market_snapshot_path=snapshot_path)
            strategy._is_backtest = lambda: False
            pair = "AAPL/USDT:USDT"
            strategy._last_candle = lambda _pair: _candle(pair)

            feasible = strategy._candidate_is_capital_feasible(
                {
                    "pair": pair,
                    "side": "long",
                    "model": "trend_long",
                    "score": 90.0,
                },
                datetime(2026, 7, 29, tzinfo=timezone.utc),
            )

            self.assertFalse(feasible)

    def test_live_entry_requires_every_approval(self):
        strategy = _strategy()
        strategy.config["dry_run"] = False
        pair = "AAPL/USDT:USDT"
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        gates = (
            "RISK_GUARD_VALIDATION_APPROVED",
            "RISK_GUARD_LIVE_APPROVED",
            "RISK_GUARD_SPLUS_APPROVED",
            "BETA_V7_LIVE_APPROVED",
        )
        for closed_gate in gates:
            with self.subTest(closed_gate=closed_gate):
                strategy._store_entry_risk_plan(
                    pair,
                    "long",
                    0.04,
                    100.0,
                    now,
                )
                environment = {gate: "true" for gate in gates}
                environment[closed_gate] = "false"
                with patch.dict(os.environ, environment, clear=False):
                    accepted = strategy._confirm_trade_entry(
                        pair,
                        "market",
                        0.04,
                        100.0,
                        "GTC",
                        now,
                        "trend_long",
                        "long",
                    )

                self.assertFalse(accepted)
                self.assertNotIn((pair, "long"), strategy._risk_plans())

    def test_btc_minimum_is_regime_dependent_at_80_usdt(self):
        strategy = _strategy()
        ordinary = _candle("BTC/USDT:USDT", rate=120000.0)
        strong = _candle(
            "BTC/USDT:USDT",
            regime="strong_risk_on",
            rate=120000.0,
        )
        candidate = {
            "pair": "BTC/USDT:USDT",
            "side": "long",
            "model": "trend_long",
            "score": 90.0,
        }
        strategy._last_candle = lambda _pair: ordinary
        self.assertFalse(
            strategy._candidate_is_capital_feasible(
                candidate,
                datetime(2026, 7, 29, tzinfo=timezone.utc),
            )
        )
        strategy._last_candle = lambda _pair: strong
        self.assertTrue(
            strategy._candidate_is_capital_feasible(
                candidate,
                datetime(2026, 7, 29, tzinfo=timezone.utc),
            )
        )

    def test_unaffordable_leader_falls_back_before_ranking(self):
        strategy = _strategy()
        now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
        frames = {
            "BTC/USDT:USDT": _candle(
                "BTC/USDT:USDT",
                rate=120000.0,
            ),
            "AAPL/USDT:USDT": _candle(
                "AAPL/USDT:USDT",
                rate=200.0,
            ),
        }
        strategy._last_candle = lambda pair: frames[pair]
        strategy._open_pairs = lambda: ()
        strategy._runtime_candidates_all = lambda _time: [
            {
                "pair": "BTC/USDT:USDT",
                "side": "long",
                "model": "trend_long",
                "score": 95.0,
                "volatility": 0.3,
            },
            {
                "pair": "AAPL/USDT:USDT",
                "side": "long",
                "model": "trend_long",
                "score": 90.0,
                "volatility": 0.2,
            },
        ]
        selected = strategy._stable_v6_candidates(now)
        self.assertEqual(
            [candidate["pair"] for candidate in selected],
            ["AAPL/USDT:USDT"],
        )


class IsolationTests(unittest.TestCase):
    def test_v7_runtime_is_isolated_and_safe(self):
        config = json.loads(
            (DEPLOYMENT_DIR / "runtime" / "config.beta-v7.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(config["dry_run"])
        self.assertEqual(config["dry_run_wallet"], 80)
        self.assertEqual(config["max_open_trades"], 3)
        self.assertEqual(config["order_types"]["stoploss"], "market")
        self.assertEqual(
            V7.OkxCrossAssetBetaV7.order_types["stoploss"],
            "market",
        )
        self.assertNotIn(
            "stoploss_on_exchange_limit_ratio",
            config["order_types"],
        )
        compose = (
            DEPLOYMENT_DIR / "docker-compose.beta-v7.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8086:8080", compose)
        self.assertIn("trades-beta-v7.sqlite", compose)
        self.assertIn("OkxCrossAssetBetaV7", compose)
        self.assertEqual(compose.count("${RISK_GUARD_LIVE_APPROVED:-false}"), 2)
        self.assertIn("${BETA_V7_LIVE_APPROVED:-false}", compose)
        self.assertIn("BETA_V7_RISK_LEDGER_PATH", compose)

    def test_v6_baseline_hashes_are_frozen(self):
        manifest = json.loads(
            (
                DEPLOYMENT_DIR
                / "archive"
                / "v1-v6"
                / "baselines"
                / "v7_baseline_manifest.json"
            ).read_text(encoding="utf-8")
        )
        for relative, expected in manifest["files"].items():
            actual = hashlib.sha256(
                (DEPLOYMENT_DIR / relative).read_bytes()
            ).hexdigest()
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
