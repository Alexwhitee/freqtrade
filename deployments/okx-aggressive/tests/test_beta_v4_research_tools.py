from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).parents[1] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


analyze = _load("analyze_beta_v4")
select = _load("select_beta_v4_exit_policy")
validate = _load("validate_beta_v4")
compare = _load("compare_beta_backtests")


def _trade(
    pair: str,
    profit_abs: float,
    profit_ratio: float,
    *,
    tag: str = "trend_long",
    is_short: bool = False,
    open_date: str = "2025-01-01 00:00:00+00:00",
):
    return {
        "pair": pair,
        "open_date": open_date,
        "close_date": "2025-01-02 00:00:00+00:00",
        "open_rate": 100.0,
        "close_rate": 100.0 * (1 + (-profit_ratio if is_short else profit_ratio)),
        "max_rate": 110.0 if not is_short else 102.0,
        "min_rate": 98.0 if not is_short else 90.0,
        "profit_abs": profit_abs,
        "profit_ratio": profit_ratio,
        "stake_amount": 5.0,
        "leverage": 1.0,
        "is_short": is_short,
        "is_open": False,
        "enter_tag": tag,
        "exit_reason": "channel_exit",
        "trade_duration": 1440,
        "fee_open": 0.001,
        "fee_close": 0.001,
        "funding_fees": -0.01,
        "orders": [
            {"ft_is_entry": True, "cost": 5.0},
            {"ft_is_entry": False, "cost": 5.1},
        ],
    }


class AttributionTests(unittest.TestCase):
    def test_trade_attribution_uses_sidecar_r_and_mfe(self):
        trade = _trade("QQQ/USDT:USDT", 0.2, 0.04)
        key = analyze.trade_key(trade)
        row = analyze.attribute_trade(trade, {key: 0.1})
        self.assertEqual(row["group"], "benchmark")
        self.assertEqual(row["side"], "long")
        self.assertAlmostEqual(row["r_multiple"], 2.0)
        self.assertAlmostEqual(row["mfe_ratio"], 0.10)
        self.assertAlmostEqual(row["mfe_capture"], 0.40)
        self.assertGreater(row["fee_and_funding_abs"], 0.02)

    def test_missing_r_is_explicit_not_inferred(self):
        report = analyze.build_report(
            {
                "trades": [_trade("BTC/USDT:USDT", -0.1, -0.02)],
                "max_drawdown_account": 0.05,
            }
        )
        self.assertIsNone(report["trades"][0]["r_multiple"])
        self.assertEqual(report["summary"]["r_coverage"], 0.0)
        self.assertEqual(report["summary"]["max_drawdown"], 0.05)
        self.assertTrue(
            report["methodology"]["missing_r_is_never_inferred_from_final_stop"]
        )

    def test_risk_ledger_rejects_nonpositive_values(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ledger.json"
            path.write_text(
                json.dumps({"trades": {"PAIR|DATE": 0}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid"):
                analyze.load_risk_ledger(path)


class ComparisonTests(unittest.TestCase):
    def test_comparison_reports_shared_trade_and_cost_stress(self):
        shared = _trade("AAPL/USDT:USDT", 0.2, 0.04)
        rejected = _trade(
            "BTC/USDT:USDT",
            -0.3,
            -0.06,
            open_date="2025-02-01 00:00:00+00:00",
        )
        base = {
            "max_drawdown_account": 0.05,
            "profit_total": -0.01,
            "sharpe": -1.0,
            "rejected_signals": 10,
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            v3_path = root / "v3.json"
            v4_path = root / "v4.json"
            v3_path.write_text(
                json.dumps(
                    {
                        "strategy": {
                            "OkxCrossAssetBetaV3": dict(
                                base,
                                trades=[shared, rejected],
                            )
                        }
                    }
                ),
                encoding="utf-8",
            )
            v4_path.write_text(
                json.dumps(
                    {
                        "strategy": {
                            "OkxCrossAssetBetaV4": dict(
                                base,
                                trades=[shared],
                                profit_total=0.01,
                            )
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = compare.compare(v3_path, v4_path)
        self.assertEqual(result["trade_overlap"]["shared_count"], 1)
        self.assertEqual(result["trade_overlap"]["v3_omitted"]["trades"], 1)
        self.assertLess(
            result["V4"]["conservative_cost_stress_profit_abs"]["2.0x"],
            result["V4"]["profit_abs"],
        )


class ExitSelectionTests(unittest.TestCase):
    @staticmethod
    def _report(r_expectancy, pf=1.3, profit=1.0, drawdown=0.10):
        return {
            "profit_abs": profit,
            "profit_factor": pf,
            "profit_without_top_5": 0.1,
            "r_coverage": 1.0,
            "r_expectancy": r_expectancy,
            "max_drawdown": drawdown,
        }

    def test_selector_uses_median_r_then_drawdown(self):
        reports = {
            "channel_only": [
                self._report(0.15),
                self._report(0.20),
                self._report(0.10),
            ],
            "delayed_break_even": [
                self._report(0.25),
                self._report(0.20),
                self._report(0.30),
            ],
            "slow_atr": [
                self._report(0.20, drawdown=0.08),
                self._report(0.20, drawdown=0.08),
                self._report(0.20, drawdown=0.08),
            ],
        }
        result = select.select_policy(reports)
        self.assertEqual(result["selected_policy"], "delayed_break_even")

    def test_selector_fails_closed_without_r_coverage(self):
        report = self._report(0.2)
        report["r_coverage"] = 0.9
        result = select.select_policy(
            {
                "channel_only": [report, report, report],
                "delayed_break_even": [],
                "slow_atr": [],
            }
        )
        self.assertIsNone(result["selected_policy"])
        self.assertTrue(result["release_stopped"])


class ReleaseGateTests(unittest.TestCase):
    @staticmethod
    def _attr():
        return {
            "by_side": {
                "long": {"trades": 50},
                "short": {"trades": 40},
            },
            "by_model": {
                "systemic_short": {"trades": 15},
                "residual_short": {"trades": 25},
            },
            "by_group": {
                "benchmark": {"trades": 20},
                "mag7": {"trades": 30},
                "memory": {"trades": 20},
                "crypto": {"trades": 20},
            },
            "payoff_ratio": 1.2,
            "r_coverage": 1.0,
            "r_expectancy": 0.15,
            "median_winner_mfe_capture": 0.25,
            "profit_without_top_5": 0.1,
        }

    def test_all_gates_can_pass_with_complete_evidence(self):
        result = {
            "total_trades": 120,
            "profit_total_abs": 2.0,
            "profit_factor": 1.3,
            "sharpe": 0.8,
            "max_drawdown_account": 0.10,
        }
        report = validate.build_report(
            result,
            self._attr(),
            result,
            self._attr(),
            {
                "1.5x": dict(result, profit_total_abs=1.0),
                "2x": dict(result, profit_total_abs=0.1),
            },
            0.14,
        )
        self.assertTrue(report["backtest_release_candidate"])
        self.assertTrue(all(report["gates"].values()))

    def test_missing_cost_and_monte_carlo_evidence_blocks_release(self):
        result = {
            "total_trades": 120,
            "profit_total_abs": 2.0,
            "profit_factor": 1.3,
            "sharpe": 0.8,
            "max_drawdown_account": 0.10,
        }
        report = validate.build_report(
            result,
            self._attr(),
            result,
            self._attr(),
            {},
            None,
        )
        self.assertFalse(report["backtest_release_candidate"])
        self.assertFalse(report["gates"]["one_point_five_cost_stress_positive"])
        self.assertFalse(
            report["gates"]["monte_carlo_p95_drawdown_at_most_15pct"]
        )


if __name__ == "__main__":
    unittest.main()
