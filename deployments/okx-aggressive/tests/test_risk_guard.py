from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "runtime" / "risk_guard.py"
SPEC = importlib.util.spec_from_file_location("risk_guard", MODULE_PATH)
risk_guard = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = risk_guard
SPEC.loader.exec_module(risk_guard)


class FakeApi:
    def __init__(
        self,
        equity=30.0,
        losses=0,
        closed_count=0,
        profit_factor=0.0,
        dry_run=False,
        whitelist=None,
        open_pairs=None,
    ):
        self.equity = equity
        self.closed_count = closed_count
        self.profit_factor = profit_factor
        self.dry_run = dry_run
        self.whitelist = whitelist or ["BTC/USDT:USDT"]
        self.open_pairs = open_pairs or []
        self.calls = []
        self.trades = [
            {
                "trade_id": index + 1,
                "is_open": False,
                "close_timestamp": index + 1,
                "close_profit_abs": -1.0,
            }
            for index in range(losses)
        ]

    def get(self, path, **kwargs):
        if path == "balance":
            return {"total": self.equity}
        if path == "profit":
            return {"closed_trade_count": self.closed_count, "profit_factor": self.profit_factor}
        if path == "trades":
            return {"trades": self.trades}
        if path == "status":
            return [{"pair": pair, "trade_id": index + 100} for index, pair in enumerate(self.open_pairs)]
        if path == "show_config":
            return {
                "dry_run": self.dry_run,
                "exchange": {"pair_whitelist": self.whitelist},
            }
        if path == "whitelist":
            return {"whitelist": self.whitelist}
        raise AssertionError(path)

    def post(self, path, payload=None):
        self.calls.append(("POST", path, payload))
        return {"status": "ok"}

    def delete(self, path):
        self.calls.append(("DELETE", path, None))
        return {"status": "ok"}


def settings(tmp_path: Path, *, splus=False, validation=True):
    return risk_guard.Settings(
        api_url="http://test/api/v1",
        api_username="user",
        api_password="password",
        state_path=tmp_path / "state.json",
        interval_seconds=30,
        validation_approved=validation,
        splus_approved=splus,
        auto_promote=True,
        telegram_token="",
        telegram_chat_id="",
    )


class RiskGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_validation_lock_blocks_entries_and_calls_stopentry(self):
        api = FakeApi()
        guard = risk_guard.RiskGuard(settings(self.tmp_path, validation=False), api)
        state = guard.tick()
        self.assertTrue(state["validation_lock"])
        self.assertTrue(state["entries_blocked"])
        self.assertIn(("POST", "stopentry", None), api.calls)

    def test_dry_run_can_collect_validation_trades_without_live_approval(self):
        api = FakeApi(dry_run=True)
        guard = risk_guard.RiskGuard(settings(self.tmp_path, validation=False), api)
        state = guard.tick()
        self.assertTrue(state["simulation_mode"])
        self.assertFalse(state["validation_lock"])
        self.assertFalse(state["entries_blocked"])

    def test_stage_two_unlocks_after_ten_trades(self):
        api = FakeApi(closed_count=10, profit_factor=1.0)
        guard = risk_guard.RiskGuard(settings(self.tmp_path), api)
        state = guard.tick()
        self.assertEqual(state["deployment_stage"], 2)
        self.assertEqual(state["risk_cap"], 0.035)
        self.assertEqual(state["leverage_cap"], 7.0)

    def test_stage_three_requires_explicit_validation(self):
        api = FakeApi(closed_count=20, profit_factor=1.2)
        guard = risk_guard.RiskGuard(settings(self.tmp_path, splus=False), api)
        guard.state["deployment_stage"] = 2
        self.assertEqual(guard.tick()["deployment_stage"], 2)

        second_path = self.tmp_path / "approved"
        second_path.mkdir()
        guard = risk_guard.RiskGuard(settings(second_path, splus=True), api)
        guard.state["deployment_stage"] = 2
        self.assertEqual(guard.tick()["deployment_stage"], 3)

    def test_two_losses_reduce_risk_one_level(self):
        api = FakeApi(losses=2, closed_count=2)
        guard = risk_guard.RiskGuard(settings(self.tmp_path), api)
        guard.state["deployment_stage"] = 3
        state = guard.tick()
        self.assertEqual(state["risk_cap"], 0.035)
        self.assertEqual(state["max_signal_level"], 2)

    def test_three_losses_pause_and_force_exit(self):
        api = FakeApi(losses=3, closed_count=3)
        guard = risk_guard.RiskGuard(settings(self.tmp_path), api)
        state = guard.tick()
        self.assertTrue(state["entries_blocked"])
        self.assertTrue(state["paused_by_guard"])
        self.assertTrue(any(call[1] == "forceexit" for call in api.calls))
        self.assertTrue(any(call[1] == "stopentry" for call in api.calls))

    def test_repeated_pause_does_not_repeat_force_exit(self):
        api = FakeApi(losses=3, closed_count=3)
        guard = risk_guard.RiskGuard(settings(self.tmp_path), api)
        guard.tick()
        first_count = len([call for call in api.calls if call[1] == "forceexit"])
        guard.tick()
        second_count = len([call for call in api.calls if call[1] == "forceexit"])
        self.assertEqual(first_count, second_count)

    def test_fifty_percent_drawdown_permanently_locks(self):
        api = FakeApi(equity=30)
        guard = risk_guard.RiskGuard(settings(self.tmp_path), api)
        guard.tick()
        api.equity = 15
        state = guard.tick()
        self.assertTrue(state["permanent_lock"])
        self.assertTrue(state["entries_blocked"])
        self.assertTrue(any(call[1] == "locks" for call in api.calls))

    def test_permanent_lock_covers_whitelist_and_open_trade_pairs(self):
        api = FakeApi(
            equity=30,
            whitelist=["QQQ/USDT:USDT", "BTC/USDT:USDT"],
            open_pairs=["ETH/USDT:USDT"],
        )
        guard = risk_guard.RiskGuard(settings(self.tmp_path), api)
        guard.tick()
        api.equity = 15
        guard.tick()
        locked = {
            call[2][0]["pair"]
            for call in api.calls
            if call[0] == "POST" and call[1] == "locks"
        }
        self.assertEqual(
            locked,
            {"QQQ/USDT:USDT", "BTC/USDT:USDT", "ETH/USDT:USDT"},
        )

    def test_recovery_mode_caps_risk_until_drawdown_below_twenty_percent(self):
        api = FakeApi(equity=30)
        guard = risk_guard.RiskGuard(settings(self.tmp_path), api)
        guard.tick()
        api.equity = 19
        state = guard.tick()
        self.assertTrue(state["recovery_mode"])
        self.assertEqual(state["risk_cap"], 0.01)
        self.assertEqual(state["leverage_cap"], 3.0)

    def test_beta_state_is_merged_and_stale_state_fails_closed(self):
        beta_path = self.tmp_path / "beta.json"
        beta_path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "beta_score": 72.0,
                    "beta_regime": "strong_risk_on",
                    "selected_pair": "QQQ/USDT:USDT",
                    "selected_score": 88.0,
                    "market_session": {"us": "us_open"},
                    "cross_asset_data_fresh": True,
                    "group_exposure": {"benchmark": 75.0},
                }
            ),
            encoding="utf-8",
        )
        base = settings(self.tmp_path)
        configured = risk_guard.Settings(
            **{**base.__dict__, "beta_state_path": beta_path}
        )
        guard = risk_guard.RiskGuard(configured, FakeApi(dry_run=True))
        state = guard.tick()
        self.assertEqual(state["beta_score"], 72.0)
        self.assertEqual(state["selected_pair"], "QQQ/USDT:USDT")
        self.assertTrue(state["cross_asset_data_fresh"])

        beta_path.write_text(
            json.dumps(
                {
                    "updated_at": "2020-01-01T00:00:00+00:00",
                    "beta_score": 99.0,
                    "beta_regime": "strong_risk_on",
                    "cross_asset_data_fresh": True,
                }
            ),
            encoding="utf-8",
        )
        state = guard.tick()
        self.assertEqual(state["beta_regime"], "blocked")
        self.assertFalse(state["cross_asset_data_fresh"])
        self.assertTrue(state["beta_entries_blocked"])
        self.assertTrue(state["entries_blocked"])

    def test_v4_direction_protocol_allows_fresh_neutral_residual_selection(self):
        beta_path = self.tmp_path / "beta-v4.json"
        beta_path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "beta_direction_score": 0.05,
                    "beta_regime": "neutral",
                    "risk_quality": 0.75,
                    "selected_pair": "NVDA/USDT:USDT",
                    "selected_side": "short",
                    "selected_model": "residual_short",
                    "selected_score": 84.0,
                    "residual_score": -0.72,
                    "exit_policy": "channel_only",
                    "market_session": {"us": "us_open"},
                    "cross_asset_data_fresh": True,
                    "group_exposure": {"mag7": 0},
                }
            ),
            encoding="utf-8",
        )
        base = settings(self.tmp_path)
        configured = risk_guard.Settings(
            **{**base.__dict__, "beta_state_path": beta_path}
        )
        guard = risk_guard.RiskGuard(configured, FakeApi(dry_run=True))
        state = guard.tick()
        self.assertEqual(state["beta_direction_score"], 0.05)
        self.assertEqual(state["selected_model"], "residual_short")
        self.assertEqual(state["risk_quality"], 0.75)
        self.assertFalse(state["beta_entries_blocked"])

    def test_v4_direction_protocol_rejects_out_of_range_values(self):
        beta_path = self.tmp_path / "beta-v4-invalid.json"
        beta_path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "beta_direction_score": 1.01,
                    "beta_regime": "strong_risk_on",
                    "risk_quality": 1.0,
                    "cross_asset_data_fresh": True,
                    "group_exposure": {},
                }
            ),
            encoding="utf-8",
        )
        base = settings(self.tmp_path)
        configured = risk_guard.Settings(
            **{**base.__dict__, "beta_state_path": beta_path}
        )
        state = risk_guard.RiskGuard(
            configured, FakeApi(dry_run=True)
        ).tick()
        self.assertEqual(state["beta_regime"], "blocked")
        self.assertTrue(state["beta_entries_blocked"])


if __name__ == "__main__":
    unittest.main()
