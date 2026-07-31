"""Equal-total-risk long-only portfolio experiment for OKX perpetuals.

V6 keeps V5's signals, exits, and execution vetoes. It admits up to three
ranked long candidates while dividing every model risk budget by three.
Memory contracts remain regime inputs but are quarantined from execution.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from pandas import DataFrame

from OkxAggressiveTrendV2 import OkxAggressiveTrendV2
from OkxCrossAssetBetaV3 import (
    ASSET_BY_PAIR,
    BETA_INPUT_PAIRS,
    TRADABLE_PAIRS,
    _pair,
)
from OkxCrossAssetBetaV5 import OkxCrossAssetBetaV5


logger = logging.getLogger(__name__)

V6_EXECUTION_PAIRS = tuple(
    pair
    for pair in TRADABLE_PAIRS
    if ASSET_BY_PAIR[pair].group != "memory"
)


class OkxCrossAssetBetaV6(OkxCrossAssetBetaV5):
    """V5 long signals in a capped, equal-total-risk three-slot portfolio."""

    can_short = False
    _PORTFOLIO_SLOTS = 3
    _PORTFOLIO_RISK_SCALE = 1.0 / _PORTFOLIO_SLOTS
    _MAX_PAIR_CORRELATION = 0.90
    _MIN_CORRELATION_OBSERVATIONS = 40
    _CORRELATION_LOOKBACK = 60
    _GROUP_SLOT_CAP = {
        "benchmark": 1,
        "mag7": 2,
        "memory": 0,
        "crypto": 1,
    }

    def informative_pairs(self):
        funding = [
            (pair, "1h", "funding_rate")
            for pair in V6_EXECUTION_PAIRS
        ]
        daily = [(pair, "1d") for pair in BETA_INPUT_PAIRS]
        return funding + daily

    def populate_entry_trend(
        self,
        dataframe: DataFrame,
        metadata: dict,
    ) -> DataFrame:
        dataframe = OkxCrossAssetBetaV5.populate_entry_trend(
            self,
            dataframe,
            metadata,
        )
        dataframe["enter_short"] = 0
        pair = str(metadata.get("pair", ""))
        spec = self.asset_spec(pair)
        if spec is not None and spec.group == "memory":
            dataframe["enter_long"] = 0
            dataframe["enter_tag"] = None
        return dataframe

    def _runtime_candidates_all(
        self,
        current_time: datetime,
    ) -> list[dict[str, Any]]:
        candidates = OkxCrossAssetBetaV5._runtime_candidates_all(
            self,
            current_time,
        )
        result: list[dict[str, Any]] = []
        for candidate in candidates:
            pair = str(candidate.get("pair", ""))
            spec = self.asset_spec(pair)
            if (
                spec is None
                or spec.group == "memory"
                or candidate.get("side") != "long"
                or candidate.get("model")
                not in {"trend_long", "continuation_long"}
            ):
                continue
            result.append({**candidate, "group": spec.group})
        return result

    @staticmethod
    def _open_pairs() -> tuple[str, ...]:
        try:
            from freqtrade.persistence import Trade

            trades = Trade.get_open_trades()
        except (AttributeError, TypeError):
            return ()
        return tuple(
            sorted(
                {
                    str(getattr(trade, "pair", ""))
                    for trade in trades
                    if str(getattr(trade, "pair", ""))
                }
            )
        )

    def _completed_daily_returns(
        self,
        pair: str,
        current_time: datetime,
    ) -> pd.Series:
        frame = self._daily_frame(pair)
        if frame.empty or "daily_return" not in frame:
            return pd.Series(dtype=float)
        now = pd.Timestamp(current_time)
        if now.tzinfo is None:
            now = now.tz_localize("UTC")
        else:
            now = now.tz_convert("UTC")
        available = frame.loc[
            pd.to_datetime(frame["available_at"], utc=True) <= now,
            ["date", "daily_return"],
        ].copy()
        available["daily_return"] = pd.to_numeric(
            available["daily_return"],
            errors="coerce",
        )
        return (
            available.dropna()
            .drop_duplicates("date", keep="last")
            .set_index("date")["daily_return"]
            .tail(self._CORRELATION_LOOKBACK)
        )

    def _pair_correlation(
        self,
        left: str,
        right: str,
        current_time: datetime,
    ) -> float | None:
        cache = getattr(self, "_v6_return_cache", None)
        timestamp = pd.Timestamp(current_time)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        cache_key = int(timestamp.floor("1h").value)
        if cache is None or cache[0] != cache_key:
            cache = (cache_key, {})
            self._v6_return_cache = cache
        series_cache: dict[str, pd.Series] = cache[1]
        for pair in (left, right):
            if pair not in series_cache:
                series_cache[pair] = self._completed_daily_returns(
                    pair,
                    current_time,
                )
        aligned = pd.concat(
            [series_cache[left], series_cache[right]],
            axis=1,
            join="inner",
        ).dropna()
        if len(aligned) < self._MIN_CORRELATION_OBSERVATIONS:
            return None
        if aligned.iloc[:, 0].std() <= 0 or aligned.iloc[:, 1].std() <= 0:
            return None
        value = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
        return value if np.isfinite(value) else None

    @classmethod
    def _select_v6_candidates(
        cls,
        candidates: list[dict[str, Any]],
        open_pairs: tuple[str, ...] = (),
        correlation_lookup: Callable[[str, str], float | None] | None = None,
    ) -> list[dict[str, Any]]:
        open_set = set(open_pairs)
        group_count = {
            group: 0 for group in cls._GROUP_SLOT_CAP
        }
        for pair in open_set:
            spec = ASSET_BY_PAIR.get(pair)
            if spec is not None:
                group_count[spec.group] += 1
        remaining = max(0, cls._PORTFOLIO_SLOTS - len(open_set))
        if remaining == 0:
            return []

        pair_preference = {_pair("QQQ"): 0, _pair("BTC"): 1}
        model_preference = {"trend_long": 0, "continuation_long": 1}
        ordered = sorted(
            (
                candidate
                for candidate in candidates
                if candidate.get("side") == "long"
                and candidate.get("model")
                in {"trend_long", "continuation_long"}
                and np.isfinite(float(candidate.get("score", np.nan)))
            ),
            key=lambda item: (
                -float(item["score"]),
                pair_preference.get(str(item["pair"]), 2),
                model_preference.get(str(item["model"]), 2),
                float(item.get("volatility", 999.0)),
                str(item["pair"]),
            ),
        )
        selected: list[dict[str, Any]] = []
        selected_pairs: set[str] = set()
        for candidate in ordered:
            pair = str(candidate["pair"])
            spec = ASSET_BY_PAIR.get(pair)
            if (
                spec is None
                or spec.group == "memory"
                or pair in open_set
                or pair in selected_pairs
                or group_count[spec.group] >= cls._GROUP_SLOT_CAP[spec.group]
            ):
                continue
            references = [*open_pairs, *selected_pairs]
            if references:
                if correlation_lookup is None:
                    continue
                correlations = [
                    correlation_lookup(pair, reference)
                    for reference in references
                ]
                if any(
                    value is None
                    or abs(float(value)) > cls._MAX_PAIR_CORRELATION
                    for value in correlations
                ):
                    continue
            chosen = {**candidate, "group": spec.group}
            selected.append(chosen)
            selected_pairs.add(pair)
            group_count[spec.group] += 1
            if len(selected) >= remaining:
                break
        return selected

    def _stable_v6_candidates(
        self,
        current_time: datetime,
    ) -> list[dict[str, Any]]:
        timestamp = pd.Timestamp(current_time)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        cache_key = int(timestamp.floor("1h").value)
        cached = getattr(self, "_v6_candidate_cache", None)
        if cached is not None and cached[0] == cache_key:
            return [dict(candidate) for candidate in cached[1]]
        open_pairs = self._open_pairs()
        selected = self._select_v6_candidates(
            self._runtime_candidates_all(current_time),
            open_pairs,
            lambda left, right: self._pair_correlation(
                left,
                right,
                current_time,
            ),
        )
        self._v6_candidate_cache = (
            cache_key,
            tuple(dict(candidate) for candidate in selected),
        )
        return [dict(candidate) for candidate in selected]

    def _confirm_trade_entry(self, *args, **kwargs) -> bool:
        pair = str(args[0] if args else kwargs.get("pair"))
        current_time = args[5] if len(args) > 5 else kwargs.get("current_time")
        side = str(args[7] if len(args) > 7 else kwargs.get("side"))
        spec = self.asset_spec(pair)
        if (
            side != "long"
            or spec is None
            or not spec.tradable
            or spec.group == "memory"
        ):
            return False
        if not self._calendar().is_open_for_entry(
            spec.session,
            current_time,
        ):
            return False
        candle = self._last_candle(pair)
        if candle is None or not bool(
            candle.get("cross_asset_data_fresh", False)
        ):
            return False
        if (
            not self._is_backtest()
            and not self._critical_data_fresh(current_time)
        ):
            return False
        selected = {
            candidate["pair"]: candidate
            for candidate in self._stable_v6_candidates(current_time)
        }.get(pair)
        if selected is None:
            return False
        self._selected_pair = pair
        self._selected_side = side
        self._selected_model = selected["model"]
        self._selected_score = float(selected["score"])
        self._selected_residual = selected.get("residual_momentum")
        return OkxAggressiveTrendV2._confirm_trade_entry(
            self,
            *args,
            **kwargs,
        )

    def _custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        spec = self.asset_spec(pair)
        limits = self._limits()
        if (
            side != "long"
            or spec is None
            or not spec.tradable
            or spec.group == "memory"
            or limits.get("entries_blocked", True)
        ):
            return 0.0
        candle = self._last_candle(pair)
        stop_distance = self._price_stop_distance(candle, current_rate)
        if candle is None or stop_distance <= 0 or leverage <= 0:
            return 0.0
        model = str(entry_tag or candle.get("enter_tag", ""))
        regime = str(candle.get("beta_regime", "blocked"))
        if (
            model not in {"trend_long", "continuation_long"}
            or regime not in {"risk_on", "strong_risk_on"}
        ):
            return 0.0
        risk_key = model
        if regime == "strong_risk_on":
            risk_key = f"{model}_strong"
        base_risk = self._MODEL_RISK[risk_key]
        if spec.group == "crypto":
            base_risk = min(base_risk, self._CRYPTO_RISK_CAP)
        quality = float(
            np.clip(
                self._safe_float(candle.get("risk_quality"), 0.0),
                0.0,
                1.0,
            )
        )
        risk_fraction = min(
            base_risk * quality * self._PORTFOLIO_RISK_SCALE,
            self._safe_float(limits.get("risk_cap"), 0.0),
        )
        equity = self._account_equity()
        if risk_fraction <= 0 or equity <= 0:
            return 0.0
        calculated = equity * risk_fraction / (
            stop_distance * leverage
        )
        stake = min(
            calculated,
            self._MARGIN_CAP,
            self._safe_float(limits.get("margin_cap"), 0.0),
            self._safe_float(max_stake, 0.0),
            max(0.0, equity - self._CASH_RESERVE),
        )
        stake = round(stake, 8)
        if (
            stake <= 0
            or min_stake is not None
            and stake < float(min_stake)
        ):
            return 0.0
        self._store_entry_risk_plan(
            pair,
            side,
            stop_distance,
            current_rate,
            current_time,
        )
        return stake

    def _active_exit_policy(self):
        configured = str(
            (getattr(self, "config", {}) or {}).get(
                "beta_v6_exit_policy",
                os.getenv("BETA_V6_EXIT_POLICY", "channel_only"),
            )
        )
        if configured != "channel_only":
            logger.error("Invalid V6 exit policy; using channel_only")
        return "channel_only"

    def _beta_state_path(self) -> Path:
        return Path(
            os.getenv(
                "BETA_REGIME_STATE_PATH",
                "/freqtrade/user_data/risk_guard/beta-v6-regime.json",
            )
        )
