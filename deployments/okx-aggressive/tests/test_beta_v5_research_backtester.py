from __future__ import annotations

import importlib.util
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


MODULE_PATH = (
    Path(__file__).parents[1] / "tools" / "backtest_beta_v5_research.py"
)
SPEC = importlib.util.spec_from_file_location(
    "backtest_beta_v5_research",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
research = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = research
SPEC.loader.exec_module(research)


def _position(**overrides):
    position = {
        "symbol": "QQQ",
        "group": "benchmark",
        "side": "long",
        "model": "trend_long",
        "signal_date": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_date": pd.Timestamp("2024-01-02", tz="UTC"),
        "entry_price": 100.0,
        "stake": 5.0,
        "leverage": 2.0,
        "notional": 10.0,
        "amount": 0.1,
        "entry_fee": 0.01,
        "risk_amount": 0.5,
        "initial_stop": 95.0,
        "stop": 95.0,
        "extreme": 100.0,
        "mfe_gross": 0.0,
        "mae_gross": 0.0,
        "sessions": 0,
        "pending_exit": None,
        "balance_before": 30.0,
    }
    position.update(overrides)
    return position


def _simulation_panel() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=3, tz="UTC")
    return pd.DataFrame(
        {
            "date": dates,
            "qqq_fresh": [True, True, True],
            "qqq_open": [100.0, 101.0, 102.0],
            "qqq_high": [101.0, 102.0, 103.0],
            "qqq_low": [99.0, 100.0, 101.0],
            "qqq_close": [100.0, 101.0, 102.0],
            "qqq_atr": [2.5, 2.5, 2.5],
            "qqq_channel_low_exit": [90.0, 90.0, 90.0],
            "qqq_channel_high_exit": [110.0, 110.0, 110.0],
            "qqq_ema20": [101.0, 101.0, 101.0],
            "qqq_ema60": [100.0, 100.0, 100.0],
        }
    )


def _candidate(model: str = "trend_long") -> dict:
    return {
        "symbol": "QQQ",
        "group": "benchmark",
        "model": model,
        "side": "long",
        "score": 80.0,
        "regime": "risk_on",
        "risk_quality": 1.0,
        "atr": 2.5,
        "signal_close": 100.0,
        "volatility": 0.2,
        "liquidity": 1.0,
    }


class ResearchBacktesterContractTests(unittest.TestCase):
    def test_signal_executes_at_next_session_open(self):
        panel = _simulation_panel()

        def candidates(row, _params, _options):
            return [_candidate()] if row["date"] == panel.iloc[0]["date"] else []

        with patch.object(research, "daily_candidates", side_effect=candidates):
            result = research.simulate(
                panel,
                research.ResearchParameters(),
                research.SimulationOptions(
                    start="2024-01-01",
                    end="2024-01-03",
                    fee_rate=0.0,
                    slippage_rate=0.0,
                    carry_bps_per_day=0.0,
                ),
            )

        trade = result["trades"][0]
        self.assertEqual(trade["signal_date"], "2024-01-01T00:00:00+00:00")
        self.assertEqual(trade["entry_date"], "2024-01-02T00:00:00+00:00")
        self.assertEqual(trade["entry_price"], 101.0)

    def test_one_slot_rejects_overlapping_next_session_candidate(self):
        panel = _simulation_panel()

        def candidates(row, _params, _options):
            return [_candidate()] if row["date"] < panel.iloc[-1]["date"] else []

        with patch.object(research, "daily_candidates", side_effect=candidates):
            result = research.simulate(
                panel,
                research.ResearchParameters(),
                research.SimulationOptions(
                    start="2024-01-01",
                    end="2024-01-03",
                    fee_rate=0.0,
                    slippage_rate=0.0,
                    carry_bps_per_day=0.0,
                ),
            )

        self.assertEqual(result["summary"]["trades"], 1)
        self.assertEqual(
            result["summary"]["rejected_by_slot"]["trend_long"],
            1,
        )

    def test_portfolio_risk_scale_reduces_trade_risk(self):
        panel = _simulation_panel()

        def candidates(row, _params, _options):
            return [_candidate()] if row["date"] == panel.iloc[0]["date"] else []

        with patch.object(research, "daily_candidates", side_effect=candidates):
            full = research.simulate(
                panel,
                research.ResearchParameters(),
                research.SimulationOptions(
                    start="2024-01-01",
                    end="2024-01-03",
                    fee_rate=0.0,
                    slippage_rate=0.0,
                    carry_bps_per_day=0.0,
                ),
            )
            half = research.simulate(
                panel,
                research.ResearchParameters(),
                research.SimulationOptions(
                    start="2024-01-01",
                    end="2024-01-03",
                    portfolio_risk_scale=0.5,
                    fee_rate=0.0,
                    slippage_rate=0.0,
                    carry_bps_per_day=0.0,
                ),
            )

        self.assertAlmostEqual(
            half["trades"][0]["risk_amount"],
            full["trades"][0]["risk_amount"] * 0.5,
        )

    def test_stop_and_risk_accounting_include_all_costs(self):
        balance, trade = research.close_position(
            _position(),
            pd.Timestamp("2024-01-03", tz="UTC"),
            95.0,
            "initial_or_trailing_stop",
            29.99,
            fee=0.001,
            carry_bps=0.5,
        )
        expected_exit_fee = 0.1 * 95.0 * 0.001
        expected_carry = 10.0 * 0.5 / 10000
        expected_net = -0.5 - 0.01 - expected_exit_fee - expected_carry
        self.assertAlmostEqual(trade["profit_abs"], expected_net)
        self.assertAlmostEqual(trade["r_multiple"], expected_net / 0.5)
        self.assertAlmostEqual(
            balance,
            29.99 - 0.5 - expected_exit_fee - expected_carry,
        )

    def test_annual_attribution_buckets_exit_year(self):
        trades = [
            {
                "symbol": "QQQ",
                "profit_abs": 1.0,
                "r_multiple": 1.0,
                "mfe_capture": 0.5,
                "entry_fee": 0.0,
                "exit_fee": 0.0,
                "carry_cost": 0.0,
                "side": "long",
                "model": "trend_long",
                "group": "benchmark",
                "exit_reason": "channel_exit",
                "exit_date": "2024-02-03T00:00:00+00:00",
            },
            {
                "symbol": "BTC",
                "profit_abs": -0.5,
                "r_multiple": -1.0,
                "mfe_capture": None,
                "entry_fee": 0.0,
                "exit_fee": 0.0,
                "carry_cost": 0.0,
                "side": "short",
                "model": "systemic_short",
                "group": "crypto",
                "exit_reason": "initial_or_trailing_stop",
                "exit_date": "2025-03-04T00:00:00+00:00",
            },
        ]
        options = research.SimulationOptions(
            start="2024-01-01",
            end="2025-12-31",
        )
        panel = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    ["2024-01-01", "2025-12-31"],
                    utc=True,
                ),
                "qqq_fresh": [True, True],
                "qqq_open": [100.0, 100.0],
                "qqq_close": [100.0, 100.0],
            }
        )
        summary = research.summarize(
            trades,
            [
                {"date": "2024-01-01", "equity": 30.0},
                {"date": "2025-12-31", "equity": 30.5},
            ],
            options,
            Counter(),
            Counter(),
            panel,
        )
        self.assertEqual(set(summary["by_year"]), {"2024", "2025"})
        self.assertEqual(summary["by_year"]["2024"]["trades"], 1)

    def test_future_price_changes_do_not_rewrite_past_features(self):
        dates = pd.date_range("2020-01-01", periods=320, tz="UTC")
        frame = pd.DataFrame(
            {
                "date": dates,
                "open": np.linspace(100.0, 150.0, len(dates)),
                "high": np.linspace(101.0, 151.0, len(dates)),
                "low": np.linspace(99.0, 149.0, len(dates)),
                "close": np.linspace(100.0, 150.0, len(dates)),
                "volume": np.full(len(dates), 1_000_000.0),
            }
        )
        changed = frame.copy()
        changed.loc[changed.index[-20:], "close"] *= 4.0
        base_features = research.asset_features(
            frame,
            research.ResearchParameters(),
        )
        changed_features = research.asset_features(
            changed,
            research.ResearchParameters(),
        )
        pd.testing.assert_series_equal(
            base_features.loc[:299, "absolute_trend"],
            changed_features.loc[:299, "absolute_trend"],
        )

    def test_higher_costs_cannot_improve_same_trade(self):
        position = _position()
        _, base = research.close_position(
            position.copy(),
            pd.Timestamp("2024-01-05", tz="UTC"),
            110.0,
            "channel_exit",
            29.99,
            fee=0.001,
            carry_bps=0.5,
        )
        _, stressed = research.close_position(
            position.copy(),
            pd.Timestamp("2024-01-05", tz="UTC"),
            110.0,
            "channel_exit",
            29.99,
            fee=0.002,
            carry_bps=1.0,
        )
        self.assertLess(stressed["profit_abs"], base["profit_abs"])

    def test_v6_selection_enforces_group_and_correlation_limits(self):
        candidates = [
            {
                **_candidate(),
                "symbol": "QQQ",
                "group": "benchmark",
                "score": 95.0,
            },
            {
                **_candidate(),
                "symbol": "AAPL",
                "group": "mag7",
                "score": 90.0,
            },
            {
                **_candidate(),
                "symbol": "NVDA",
                "group": "mag7",
                "score": 89.0,
            },
            {
                **_candidate(),
                "symbol": "BTC",
                "group": "crypto",
                "score": 85.0,
            },
            {
                **_candidate(),
                "symbol": "MU",
                "group": "memory",
                "score": 100.0,
            },
        ]
        row = pd.Series(
            {
                research.correlation_column("QQQ", "AAPL"): 0.70,
                research.correlation_column("QQQ", "NVDA"): 0.75,
                research.correlation_column("AAPL", "NVDA"): 0.95,
                research.correlation_column("QQQ", "BTC"): 0.30,
                research.correlation_column("AAPL", "BTC"): 0.25,
            }
        )
        selected = research.select_candidates(
            candidates,
            3,
            row=row,
            profile="v6",
        )
        self.assertEqual(
            [candidate["symbol"] for candidate in selected],
            ["QQQ", "AAPL", "BTC"],
        )


if __name__ == "__main__":
    unittest.main()
