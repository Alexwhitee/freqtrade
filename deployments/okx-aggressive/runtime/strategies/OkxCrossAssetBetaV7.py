"""Capital-aware, moderately bolder V6 portfolio experiment.

V7 raises each candidate's share of the model risk budget from one third to
one half, while a hard 0.75% aggregate initial-risk ceiling covers open trades
and pending entry plans. Minimum-contract feasibility is checked before
ranking so an unaffordable leader does not block a feasible runner-up.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from OkxCrossAssetBetaV3 import ASSET_BY_PAIR
from OkxCrossAssetBetaV6 import OkxCrossAssetBetaV6


logger = logging.getLogger(__name__)


class OkxCrossAssetBetaV7(OkxCrossAssetBetaV6):
    """Three-slot long portfolio with a shared, capital-aware risk ledger."""

    order_types = {
        "entry": "market",
        "exit": "market",
        "emergency_exit": "market",
        "force_entry": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
    }

    _PORTFOLIO_RISK_SCALE = 0.50
    _AGGREGATE_RISK_CAP = 0.0075
    _SINGLE_RISK_CAP = 0.0050
    _UNVERIFIED_STOP_KEY = "v7_initial_stop_unverified"
    _RISK_LEDGER_VERSION = 1
    _MARKET_SNAPSHOT_MAX_AGE = timedelta(days=7)
    # Keep restart-recovered reservations for the full entry-plan TTL.  The
    # exchange order may still be pending after Freqtrade restarts.
    _ORPHAN_PLAN_GRACE = timedelta(minutes=10)

    @staticmethod
    def _open_trades():
        from freqtrade.persistence import Trade

        return Trade.get_open_trades()

    def _risk_ledger_path(self) -> Path:
        return Path(
            os.getenv(
                "BETA_V7_RISK_LEDGER_PATH",
                "/freqtrade/user_data/risk_guard/beta-v7-risk-ledger.json",
            )
        )

    @staticmethod
    def _utc_timestamp(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _load_risk_plans(self) -> dict[tuple[str, str], dict[str, Any]]:
        path = self._risk_ledger_path()
        if not path.exists():
            self._v7_risk_ledger_valid = True
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != self._RISK_LEDGER_VERSION:
                raise ValueError("unsupported risk ledger version")
            reservations = payload.get("reservations")
            if not isinstance(reservations, list):
                raise ValueError("invalid risk ledger reservations")
            plans: dict[tuple[str, str], dict[str, Any]] = {}
            for row in reservations:
                if not isinstance(row, dict):
                    raise ValueError("invalid risk ledger row")
                pair = str(row.get("pair", ""))
                side = str(row.get("side", "")).lower()
                stop_distance = self._safe_float(row.get("stop_distance"), 0.0)
                rate = self._safe_float(row.get("rate"), 0.0)
                risk_fraction = self._safe_float(row.get("risk_fraction"), -1.0)
                created_at = datetime.fromisoformat(str(row.get("created_at", "")))
                created_at = self._utc_timestamp(created_at)
                if (
                    pair not in ASSET_BY_PAIR
                    or side != "long"
                    or not 0.025 <= stop_distance <= 0.080
                    or rate <= 0
                    or not 0 < risk_fraction <= self._SINGLE_RISK_CAP
                ):
                    raise ValueError("invalid risk ledger values")
                plans[(pair, side)] = {
                    "stop_distance": stop_distance,
                    "rate": rate,
                    "risk_fraction": risk_fraction,
                    "created_at": created_at,
                }
            self._v7_risk_ledger_valid = True
            return plans
        except (OSError, TypeError, ValueError) as exc:
            logger.error(
                "V7 risk ledger is invalid; blocking new entries: %s",
                exc.__class__.__name__,
            )
            self._v7_risk_ledger_valid = False
            return {}

    def _risk_plans(self) -> dict[tuple[str, str], dict[str, Any]]:
        plans = getattr(self, "_entry_risk_plans", None)
        if plans is None:
            plans = self._load_risk_plans()
            self._entry_risk_plans = plans
        return plans

    def _persist_risk_plans(self) -> None:
        path = self._risk_ledger_path()
        try:
            path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
            reservations = []
            for (pair, side), plan in sorted(self._entry_risk_plans.items()):
                created_at = plan.get("created_at")
                if not isinstance(created_at, datetime):
                    raise ValueError("risk plan has no creation time")
                reservations.append(
                    {
                        "pair": pair,
                        "side": side,
                        "stop_distance": self._safe_float(
                            plan.get("stop_distance"),
                            0.0,
                        ),
                        "rate": self._safe_float(plan.get("rate"), 0.0),
                        "risk_fraction": self._safe_float(
                            plan.get("risk_fraction"),
                            0.0,
                        ),
                        "created_at": self._utc_timestamp(created_at).isoformat(),
                    }
                )
            pending_risk = min(
                self._AGGREGATE_RISK_CAP,
                sum(row["risk_fraction"] for row in reservations),
            )
            payload = {
                "version": self._RISK_LEDGER_VERSION,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "valid": True,
                "pending_risk_fraction": pending_risk,
                "remaining_risk_fraction": max(
                    0.0,
                    self._AGGREGATE_RISK_CAP - pending_risk,
                ),
                "reservations": reservations,
            }
            temporary = path.with_name(f".{path.name}.tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            self._v7_risk_ledger_valid = True
        except (OSError, TypeError, ValueError):
            self._v7_risk_ledger_valid = False
            raise

    def _target_risk_fraction(
        self,
        pair: str,
        candle: pd.Series,
        model: str,
    ) -> float:
        spec = self.asset_spec(pair)
        regime = str(candle.get("beta_regime", "blocked"))
        if (
            spec is None
            or model not in {"trend_long", "continuation_long"}
            or regime not in {"risk_on", "strong_risk_on"}
        ):
            return 0.0
        risk_key = (
            f"{model}_strong"
            if regime == "strong_risk_on"
            else model
        )
        base_risk = self._MODEL_RISK[risk_key]
        quality = float(
            np.clip(
                self._safe_float(candle.get("risk_quality"), 0.0),
                0.0,
                1.0,
            )
        )
        scaled = base_risk * quality * self._PORTFOLIO_RISK_SCALE
        if spec.group == "crypto":
            scaled = min(scaled, self._CRYPTO_RISK_CAP)
        return min(scaled, self._SINGLE_RISK_CAP)

    def _invalidate_candidate_cache(self) -> None:
        self._v7_candidate_cache = None

    def _store_entry_risk_plan(
        self,
        pair: str,
        side: str,
        stop_distance: float,
        rate: float,
        current_time: datetime,
        risk_fraction: float | None = None,
    ) -> None:
        super()._store_entry_risk_plan(
            pair,
            side,
            stop_distance,
            rate,
            current_time,
        )
        plan = self._risk_plans()[self._risk_plan_key(pair, side)]
        plan["risk_fraction"] = min(
            self._SINGLE_RISK_CAP,
            max(
                0.0,
                self._safe_float(risk_fraction, self._SINGLE_RISK_CAP),
            ),
        )
        self._persist_risk_plans()
        self._invalidate_candidate_cache()

    def _release_entry_risk_plan(self, pair: str, side: str) -> None:
        removed = self._risk_plans().pop(
            self._risk_plan_key(pair, side),
            None,
        )
        if removed is not None:
            self._persist_risk_plans()
        self._invalidate_candidate_cache()

    def _entry_risk_plan(
        self,
        pair: str,
        side: str,
        current_time: datetime,
    ) -> dict[str, Any] | None:
        key = self._risk_plan_key(pair, side)
        plan = self._risk_plans().get(key)
        if not plan:
            return None
        created_at = plan.get("created_at")
        now = self._utc_timestamp(current_time)
        if (
            not isinstance(created_at, datetime)
            or now - self._utc_timestamp(created_at) > self._ENTRY_RISK_PLAN_TTL
        ):
            self._release_entry_risk_plan(pair, side)
            return None
        return plan

    def _reserved_risk_fraction(
        self,
        equity: float,
        exclude_pair: str | None = None,
        current_time: datetime | None = None,
    ) -> float:
        plans = self._risk_plans()
        if equity <= 0 or not getattr(
            self,
            "_v7_risk_ledger_valid",
            True,
        ):
            return self._AGGREGATE_RISK_CAP
        reserved = 0.0
        for trade in self._open_trades():
            pair = str(getattr(trade, "pair", ""))
            if pair == exclude_pair:
                continue
            try:
                raw_distance = trade.get_custom_data(
                    "initial_stop_distance",
                    None,
                )
                unverified = bool(
                    trade.get_custom_data(
                        self._UNVERIFIED_STOP_KEY,
                        False,
                    )
                )
            except AttributeError:
                raw_distance = None
                unverified = True
            distance = self._safe_float(raw_distance, 0.0)
            if unverified or not 0.025 <= distance <= 0.080:
                try:
                    trade.set_custom_data(
                        self._UNVERIFIED_STOP_KEY,
                        True,
                    )
                except AttributeError:
                    pass
                logger.error(
                    "Open trade %s has no verified initial stop; "
                    "blocking new entries",
                    getattr(trade, "id", "unknown"),
                )
                return self._AGGREGATE_RISK_CAP
            stake = self._safe_float(
                getattr(trade, "stake_amount", 0.0),
                0.0,
            )
            leverage = self._safe_float(
                getattr(trade, "leverage", 1.0),
                1.0,
            )
            reserved += stake * leverage * distance / equity

        now = pd.Timestamp(
            current_time or datetime.now().astimezone()
        )
        if now.tzinfo is None:
            now = now.tz_localize("UTC")
        else:
            now = now.tz_convert("UTC")
        stale_keys = []
        for (pair, side), plan in plans.items():
            if side != "long" or pair == exclude_pair:
                continue
            created_at = plan.get("created_at")
            if (
                not isinstance(created_at, datetime)
                or now - pd.Timestamp(created_at) > self._ENTRY_RISK_PLAN_TTL
            ):
                stale_keys.append((pair, side))
                continue
            reserved += max(
                0.0,
                self._safe_float(plan.get("risk_fraction"), 0.0),
            )
        for key in stale_keys:
            self._release_entry_risk_plan(*key)
        return min(reserved, self._AGGREGATE_RISK_CAP)

    def _market_snapshot_path(self) -> Path:
        return Path(
            os.getenv(
                "BETA_MARKET_SNAPSHOT_PATH",
                "/freqtrade/user_data/okx_beta_markets.snapshot.json",
            )
        )

    def _minimum_underlying_amount(
        self,
        pair: str,
        current_time: datetime,
    ) -> float:
        path = self._market_snapshot_path()
        try:
            modified = path.stat().st_mtime_ns
            cached = getattr(self, "_v7_market_snapshot_cache", None)
            if cached is None or cached[0] != modified:
                payload = json.loads(path.read_text(encoding="utf-8"))
                captured_at = datetime.fromisoformat(str(payload["captured_at"]))
                markets = {
                    str(row["symbol"]): row
                    for row in payload["markets"]
                    if isinstance(row, dict) and "symbol" in row
                }
                cached = (modified, self._utc_timestamp(captured_at), markets)
                self._v7_market_snapshot_cache = cached
            _, captured_at, markets = cached
            now = self._utc_timestamp(current_time)
            age = now - captured_at
            if not self._is_backtest() and (
                age < -timedelta(minutes=5)
                or age > self._MARKET_SNAPSHOT_MAX_AGE
            ):
                raise ValueError("market snapshot is stale")
            market = markets[pair]
            amount_min = self._safe_float(market.get("amount_min"), 0.0)
            contract_size = self._safe_float(
                market.get("contract_size"),
                0.0,
            )
            if (
                not market.get("active")
                or not market.get("swap")
                or not market.get("linear")
                or amount_min <= 0
                or contract_size <= 0
            ):
                raise ValueError("invalid market contract metadata")
            return amount_min * contract_size
        except (KeyError, OSError, TypeError, ValueError) as exc:
            logger.error(
                "V7 market snapshot is unavailable for %s; blocking entry: %s",
                pair,
                exc.__class__.__name__,
            )
            return float("inf")

    def _minimum_margin(
        self,
        pair: str,
        rate: float,
        leverage: float,
        current_time: datetime,
    ) -> float:
        amount = self._minimum_underlying_amount(pair, current_time)
        if rate <= 0 or leverage <= 0:
            return float("inf")
        return rate * amount / leverage

    def _candidate_is_capital_feasible(
        self,
        candidate: dict[str, Any],
        current_time: datetime,
    ) -> bool:
        pair = str(candidate.get("pair", ""))
        spec = ASSET_BY_PAIR.get(pair)
        candle = self._last_candle(pair)
        equity = self._account_equity()
        if spec is None or candle is None or equity <= 0:
            return False
        rate = self._safe_float(candle.get("close"), 0.0)
        leverage = spec.max_leverage
        stop_distance = self._price_stop_distance(candle, rate)
        model = str(candidate.get("model", ""))
        target = self._target_risk_fraction(pair, candle, model)
        remaining = max(
            0.0,
            self._AGGREGATE_RISK_CAP
            - self._reserved_risk_fraction(
                equity,
                exclude_pair=pair,
                current_time=current_time,
            ),
        )
        risk_fraction = min(target, remaining)
        if stop_distance <= 0 or risk_fraction <= 0:
            return False
        planned_margin = min(
            equity * risk_fraction / (stop_distance * leverage),
            self._MARGIN_CAP,
            max(0.0, equity - self._CASH_RESERVE),
        )
        return planned_margin + 1e-9 >= self._minimum_margin(
            pair,
            rate,
            leverage,
            current_time,
        )

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
        cached = getattr(self, "_v7_candidate_cache", None)
        if cached is not None and cached[0] == cache_key:
            return [dict(candidate) for candidate in cached[1]]
        open_pairs = self._open_pairs()
        feasible = [
            candidate
            for candidate in self._runtime_candidates_all(current_time)
            if self._candidate_is_capital_feasible(
                candidate,
                current_time,
            )
        ]
        selected = self._select_v6_candidates(
            feasible,
            open_pairs,
            lambda left, right: self._pair_correlation(
                left,
                right,
                current_time,
            ),
        )
        self._v7_candidate_cache = (
            cache_key,
            tuple(dict(candidate) for candidate in selected),
        )
        return [dict(candidate) for candidate in selected]

    def _confirm_trade_entry(self, *args, **kwargs) -> bool:
        pair = str(args[0] if args else kwargs.get("pair", ""))
        side = str(args[7] if len(args) > 7 else kwargs.get("side", ""))
        accepted = False
        try:
            if not bool((getattr(self, "config", {}) or {}).get("dry_run", False)):
                required = (
                    "RISK_GUARD_VALIDATION_APPROVED",
                    "RISK_GUARD_LIVE_APPROVED",
                    "RISK_GUARD_SPLUS_APPROVED",
                    "BETA_V7_LIVE_APPROVED",
                )
                if any(
                    os.getenv(name, "false").strip().lower()
                    not in {"1", "true", "yes", "on"}
                    for name in required
                ):
                    logger.error("V7 live entry rejected: approval gates are closed")
                    return False
            accepted = super()._confirm_trade_entry(*args, **kwargs)
            return accepted
        finally:
            if not accepted:
                self._release_entry_risk_plan(pair, side)

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
        candle = self._last_candle(pair)
        equity = self._account_equity()
        stop_distance = self._price_stop_distance(candle, current_rate)
        model = str(entry_tag or (
            candle.get("enter_tag", "") if candle is not None else ""
        ))
        if (
            side != "long"
            or spec is None
            or not spec.tradable
            or spec.group == "memory"
            or limits.get("entries_blocked", True)
            or candle is None
            or equity <= 0
            or stop_distance <= 0
            or leverage <= 0
        ):
            return 0.0
        reserved = self._reserved_risk_fraction(
            equity,
            exclude_pair=pair,
            current_time=current_time,
        )
        remaining = max(0.0, self._AGGREGATE_RISK_CAP - reserved)
        risk_fraction = min(
            self._target_risk_fraction(pair, candle, model),
            remaining,
            self._safe_float(limits.get("risk_cap"), 0.0),
        )
        if risk_fraction <= 0:
            return 0.0
        stake = min(
            equity * risk_fraction / (stop_distance * leverage),
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
            risk_fraction=stake * leverage * stop_distance / equity,
        )
        return stake

    def order_filled(
        self,
        pair: str,
        trade: Any,
        order: Any,
        current_time: datetime,
        **kwargs,
    ) -> None:
        self._invalidate_candidate_cache()
        if getattr(order, "ft_order_side", None) != trade.entry_side:
            return
        side = "short" if trade.is_short else "long"
        try:
            stored = self._safe_float(
                trade.get_custom_data("initial_stop_distance", None),
                0.0,
            )
            unverified = bool(
                trade.get_custom_data(self._UNVERIFIED_STOP_KEY, False)
            )
        except AttributeError:
            stored = 0.0
            unverified = True
        if 0.025 <= stored <= 0.080 and not unverified:
            self._release_entry_risk_plan(pair, side)
            return
        plan = self._entry_risk_plan(pair, side, current_time)
        if plan is None:
            logger.error(
                "Entry filled without a valid risk plan for %s %s; "
                "blocking new entries until the trade closes",
                pair,
                side,
            )
            trade.set_custom_data("initial_stop_distance", 0.025)
            trade.set_custom_data(self._UNVERIFIED_STOP_KEY, True)
            return
        trade.set_custom_data(
            "initial_stop_distance",
            plan["stop_distance"],
        )
        trade.set_custom_data(self._UNVERIFIED_STOP_KEY, False)
        self._release_entry_risk_plan(pair, side)

    def _initial_stop_distance(
        self,
        trade: Any,
        _candle: pd.Series | None = None,
    ) -> float:
        try:
            stored = self._safe_float(
                trade.get_custom_data("initial_stop_distance", None),
                0.0,
            )
        except AttributeError:
            stored = 0.0
        if 0.025 <= stored <= 0.080:
            return stored
        logger.error(
            "Trade %s has no persisted initial stop; using a 2.5%% "
            "protective stop and blocking new entries",
            getattr(trade, "id", "unknown"),
        )
        trade.set_custom_data("initial_stop_distance", 0.025)
        trade.set_custom_data(self._UNVERIFIED_STOP_KEY, True)
        return 0.025

    def _reconcile_entry_risk_plans(self, current_time: datetime) -> None:
        open_pairs = {
            str(getattr(trade, "pair", ""))
            for trade in self._open_trades()
        }
        stale = [
            key
            for key, plan in self._risk_plans().items()
            if key[0] not in open_pairs
            and isinstance(plan.get("created_at"), datetime)
            and self._utc_timestamp(current_time)
            - self._utc_timestamp(plan["created_at"])
            > self._ORPHAN_PLAN_GRACE
        ]
        for pair, side in stale:
            logger.warning(
                "Releasing orphaned entry risk plan for %s %s",
                pair,
                side,
            )
            self._release_entry_risk_plan(pair, side)

    def bot_loop_start(
        self,
        current_time: datetime,
        **kwargs,
    ) -> None:
        super().bot_loop_start(current_time, **kwargs)
        self._reconcile_entry_risk_plans(current_time)

    def _active_exit_policy(self):
        configured = str(
            (getattr(self, "config", {}) or {}).get(
                "beta_v7_exit_policy",
                os.getenv("BETA_V7_EXIT_POLICY", "channel_only"),
            )
        )
        if configured != "channel_only":
            logger.error("Invalid V7 exit policy; using channel_only")
        return "channel_only"

    def _beta_state_path(self) -> Path:
        return Path(
            os.getenv(
                "BETA_REGIME_STATE_PATH",
                "/freqtrade/user_data/risk_guard/beta-v7-regime.json",
            )
        )
