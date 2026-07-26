from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "runtime" / "risk_guard.py"
SPEC = importlib.util.spec_from_file_location("risk_guard", MODULE_PATH)
risk_guard = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = risk_guard
SPEC.loader.exec_module(risk_guard)


class FakeApi:
    def __init__(self, equity=30.0, losses=0, closed_count=0, profit_factor=0.0, dry_run=False):
        self.equity = equity
        self.closed_count = closed_count
        self.profit_factor = profit_factor
        self.dry_run = dry_run
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
            return []
        if path == "show_config":
            return {"dry_run": self.dry_run}
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

    def test_recovery_mode_caps_risk_until_drawdown_below_twenty_percent(self):
        api = FakeApi(equity=30)
        guard = risk_guard.RiskGuard(settings(self.tmp_path), api)
        guard.tick()
        api.equity = 19
        state = guard.tick()
        self.assertTrue(state["recovery_mode"])
        self.assertEqual(state["risk_cap"], 0.01)
        self.assertEqual(state["leverage_cap"], 3.0)


if __name__ == "__main__":
    unittest.main()
