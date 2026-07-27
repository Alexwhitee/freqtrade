from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


TOOLS_DIR = Path(__file__).parents[1] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


prepare = _load("prepare_beta_research")
validate = _load("validate_beta_v3")


class ResearchInputTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_krw_prices_are_converted_to_usd(self):
        dates = ["2026-01-02", "2026-01-05"]
        pd.DataFrame({"date": dates, "krw_usd": [0.001, 0.002]}).to_csv(
            self.root / "KRWUSD.csv", index=False
        )
        pd.DataFrame(
            {
                "date": dates,
                "open": [1000, 1000],
                "high": [1100, 1100],
                "low": [900, 900],
                "close": [1000, 1000],
                "volume": [10, 20],
            }
        ).to_csv(self.root / "SAMSUNG.csv", index=False)
        manifest = {
            "required_columns": ["date", "open", "high", "low", "close", "volume"],
            "usd_fx_columns": ["date", "krw_usd"],
            "assets": [
                {
                    "symbol": "SAMSUNG",
                    "group": "memory",
                    "currency": "KRW",
                    "tradable": True,
                }
            ],
        }
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        panel = prepare.build_panel(self.root, manifest_path)
        self.assertEqual(panel["close"].tolist(), [1.0, 2.0])
        self.assertEqual(set(panel["price_currency"]), {"USD"})
        self.assertEqual(set(panel["source_currency"]), {"KRW"})

    def test_duplicate_dates_are_rejected(self):
        path = self.root / "duplicate.csv"
        pd.DataFrame(
            {
                "date": ["2026-01-01", "2026-01-01"],
                "close": [1, 2],
            }
        ).to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            prepare.load_csv(path, {"date", "close"})


class ValidationMetricTests(unittest.TestCase):
    def test_group_report_and_top_five_concentration(self):
        result = {
            "trades": [
                {"pair": "QQQ/USDT:USDT", "profit_abs": 10},
                {"pair": "BTC/USDT:USDT", "profit_abs": 9},
                {"pair": "ETH/USDT:USDT", "profit_abs": 8},
                {"pair": "NVDA/USDT:USDT", "profit_abs": 7},
                {"pair": "MU/USDT:USDT", "profit_abs": 6},
                {"pair": "SAMSUNG/USDT:USDT", "profit_abs": -2},
            ]
        }
        groups = validate.group_report(result)
        self.assertEqual(groups["benchmark"]["trades"], 1)
        self.assertEqual(groups["crypto"]["trades"], 2)
        self.assertEqual(groups["memory"]["profit_abs"], 4)
        self.assertEqual(validate.top_profit_residual(result), -2)


if __name__ == "__main__":
    unittest.main()
