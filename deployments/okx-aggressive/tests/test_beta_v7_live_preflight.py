from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


DEPLOYMENT_DIR = Path(__file__).parents[1]
MODULE_PATH = DEPLOYMENT_DIR / "tools" / "validate_beta_v7_live.py"
SPEC = importlib.util.spec_from_file_location("validate_beta_v7_live", MODULE_PATH)
preflight = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(preflight)


class V7LivePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "runtime").mkdir()
        for relative in (
            "runtime/config.beta-v7.json",
            "runtime/okx_beta_markets.snapshot.json",
            "docker-compose.beta-v7.yml",
        ):
            source = DEPLOYMENT_DIR / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        snapshot = json.loads(
            (self.root / "runtime/okx_beta_markets.snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        self.now = datetime.fromisoformat(snapshot["captured_at"]) + timedelta(days=1)
        self.env_path = self.root / ".env"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_env(self, values: dict[str, str]) -> None:
        self.env_path.write_text(
            "".join(f'{key}={json.dumps(value)}\n' for key, value in values.items()),
            encoding="utf-8",
        )
        self.env_path.chmod(0o600)

    @staticmethod
    def live_env() -> dict[str, str]:
        values = {name: "present" for name in preflight.REQUIRED_SECRETS}
        values.update(
            {
                "FREQTRADE__DRY_RUN": "false",
                "FREQTRADE__API_SERVER__USERNAME": "api-user",
                "RISK_GUARD_API_USERNAME": "api-user",
                "FREQTRADE__API_SERVER__PASSWORD": "api-password",
                "RISK_GUARD_API_PASSWORD": "api-password",
                **{name: "true" for name in preflight.APPROVAL_GATES},
            }
        )
        return values

    def validate(self, require_live: bool):
        return preflight.validate_local(
            self.root,
            self.env_path,
            require_live=require_live,
            now=self.now,
            max_snapshot_age=timedelta(days=7),
        )

    def test_safe_dry_run_configuration_passes(self):
        self.write_env({"FREQTRADE__DRY_RUN": "true"})

        report = self.validate(require_live=False)

        self.assertTrue(report["effective_dry_run"])
        self.assertFalse(report["approval_gates_open"])

    def test_live_configuration_requires_every_gate(self):
        values = self.live_env()
        values["BETA_V7_LIVE_APPROVED"] = "false"
        self.write_env(values)

        with self.assertRaisesRegex(ValueError, "BETA_V7_LIVE_APPROVED"):
            self.validate(require_live=True)

    def test_live_configuration_passes_offline_checks(self):
        self.write_env(self.live_env())

        report = self.validate(require_live=True)

        self.assertFalse(report["effective_dry_run"])
        self.assertTrue(report["approval_gates_open"])
        self.assertEqual(report["database_open_trades"], 0)

    def test_stale_snapshot_is_rejected(self):
        self.write_env({"FREQTRADE__DRY_RUN": "true"})

        with self.assertRaisesRegex(ValueError, "snapshot is stale"):
            preflight.validate_local(
                self.root,
                self.env_path,
                require_live=False,
                now=self.now + timedelta(days=8),
                max_snapshot_age=timedelta(days=7),
            )

    def test_open_v7_database_blocks_live_validation(self):
        self.write_env(self.live_env())
        database = self.root / "runtime/trades-beta-v7.sqlite"
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE trades (is_open INTEGER NOT NULL)")
            connection.execute("CREATE TABLE orders (ft_is_open INTEGER NOT NULL)")
            connection.execute("INSERT INTO trades VALUES (1)")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(ValueError, "database is not flat"):
            self.validate(require_live=True)

    def test_corrupt_risk_ledger_is_rejected(self):
        self.write_env({"FREQTRADE__DRY_RUN": "true"})
        ledger = self.root / "runtime/risk_guard/beta-v7-risk-ledger.json"
        ledger.parent.mkdir()
        ledger.write_text("not-json", encoding="utf-8")
        ledger.chmod(0o600)

        with self.assertRaises(json.JSONDecodeError):
            self.validate(require_live=False)

    def test_okx_preflight_accepts_matching_contract_metadata(self):
        pair = "AAPL/USDT:USDT"
        expected = {
            pair: {"amount_min": 0.01, "contract_size": 1.0}
        }

        class Exchange:
            def __init__(self, _config):
                pass

            def load_markets(self):
                return {
                    pair: {
                        "active": True,
                        "swap": True,
                        "linear": True,
                        "contractSize": 1.0,
                        "limits": {"amount": {"min": 0.01}},
                    }
                }

            def fetch_positions(self):
                return []

            def fetch_open_orders(self, params=None):
                return []

            def fetch_balance(self):
                return {}

        report = preflight.validate_okx(
            self.live_env(),
            [pair],
            expected,
            exchange_factory=Exchange,
        )

        self.assertEqual(report["private_authentication"], "valid")

    def test_okx_preflight_rejects_changed_contract_metadata(self):
        pair = "AAPL/USDT:USDT"

        class Exchange:
            def __init__(self, _config):
                pass

            def load_markets(self):
                return {
                    pair: {
                        "active": True,
                        "swap": True,
                        "linear": True,
                        "contractSize": 2.0,
                        "limits": {"amount": {"min": 0.01}},
                    }
                }

        with self.assertRaisesRegex(ValueError, "metadata changed"):
            preflight.validate_okx(
                self.live_env(),
                [pair],
                {pair: {"amount_min": 0.01, "contract_size": 1.0}},
                exchange_factory=Exchange,
            )

    def test_okx_preflight_rejects_position_outside_whitelist(self):
        pair = "AAPL/USDT:USDT"

        class Exchange:
            def __init__(self, _config):
                pass

            def load_markets(self):
                return {
                    pair: {
                        "active": True,
                        "swap": True,
                        "linear": True,
                        "contractSize": 1.0,
                        "limits": {"amount": {"min": 0.01}},
                    }
                }

            def fetch_positions(self):
                return [{"symbol": "DOGE/USDT:USDT", "contracts": 1.0}]

            def fetch_open_orders(self, params=None):
                return []

        with self.assertRaisesRegex(ValueError, "open positions or orders"):
            preflight.validate_okx(
                self.live_env(),
                [pair],
                {pair: {"amount_min": 0.01, "contract_size": 1.0}},
                exchange_factory=Exchange,
            )

    def test_okx_preflight_rejects_algo_order_outside_whitelist(self):
        pair = "AAPL/USDT:USDT"

        class Exchange:
            def __init__(self, _config):
                pass

            def load_markets(self):
                return {
                    pair: {
                        "active": True,
                        "swap": True,
                        "linear": True,
                        "contractSize": 1.0,
                        "limits": {"amount": {"min": 0.01}},
                    }
                }

            def fetch_positions(self):
                return []

            def fetch_open_orders(self, params=None):
                if params == {"ordType": "trigger"}:
                    return [{"symbol": "DOGE/USDT:USDT", "id": "pending"}]
                return []

        with self.assertRaisesRegex(ValueError, "open positions or orders"):
            preflight.validate_okx(
                self.live_env(),
                [pair],
                {pair: {"amount_min": 0.01, "contract_size": 1.0}},
                exchange_factory=Exchange,
            )


if __name__ == "__main__":
    unittest.main()
