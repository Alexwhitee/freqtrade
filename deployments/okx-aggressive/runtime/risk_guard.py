"""Persistent account-equity guard for the OKX aggressive Freqtrade deployment."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


LOG = logging.getLogger("risk_guard")
UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_url: str
    api_username: str
    api_password: str
    state_path: Path
    interval_seconds: int
    validation_approved: bool
    splus_approved: bool
    auto_promote: bool
    telegram_token: str
    telegram_chat_id: str
    beta_state_path: Path | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        username = os.getenv("RISK_GUARD_API_USERNAME", "")
        password = os.getenv("RISK_GUARD_API_PASSWORD", "")
        if not username or not password:
            raise ValueError("RISK_GUARD_API_USERNAME and RISK_GUARD_API_PASSWORD are required")
        interval = int(os.getenv("RISK_GUARD_INTERVAL_SECONDS", "30"))
        if not 5 <= interval <= 300:
            raise ValueError("RISK_GUARD_INTERVAL_SECONDS must be between 5 and 300")
        return cls(
            api_url=os.getenv("RISK_GUARD_API_URL", "http://freqtrade:8080/api/v1").rstrip("/"),
            api_username=username,
            api_password=password,
            state_path=Path(
                os.getenv("RISK_GUARD_STATE_PATH", "/freqtrade/user_data/risk_guard/state.json")
            ),
            interval_seconds=interval,
            validation_approved=env_bool("RISK_GUARD_VALIDATION_APPROVED", False),
            splus_approved=env_bool("RISK_GUARD_SPLUS_APPROVED", False),
            auto_promote=env_bool("RISK_GUARD_AUTO_PROMOTE", True),
            telegram_token=os.getenv("RISK_GUARD_TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.getenv("RISK_GUARD_TELEGRAM_CHAT_ID", ""),
            beta_state_path=(
                Path(os.environ["RISK_GUARD_BETA_STATE_PATH"])
                if os.getenv("RISK_GUARD_BETA_STATE_PATH")
                else None
            ),
        )


class ApiClient:
    def __init__(self, settings: Settings):
        self.base_url = settings.api_url
        self.session = requests.Session()
        self.session.auth = (settings.api_username, settings.api_password)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def request(self, method: str, path: str, **kwargs) -> Any:
        response = self.session.request(
            method, f"{self.base_url}/{path.lstrip('/')}", timeout=10, **kwargs
        )
        response.raise_for_status()
        return response.json()

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, payload: Any | None = None) -> Any:
        return self.request("POST", path, json=payload)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)


class RiskGuard:
    STAGE_LIMITS = {
        1: {"max_signal_level": 1, "risk_cap": 0.02, "leverage_cap": 5.0, "margin_cap": 10.0},
        2: {"max_signal_level": 2, "risk_cap": 0.035, "leverage_cap": 7.0, "margin_cap": 15.0},
        3: {"max_signal_level": 3, "risk_cap": 0.05, "leverage_cap": 10.0, "margin_cap": 20.0},
    }

    def __init__(self, settings: Settings, api: ApiClient | None = None):
        self.settings = settings
        self.api = api or ApiClient(settings)
        self.state = self._load_state()
        self._startup_lock_replayed = False

    def _default_state(self) -> dict[str, Any]:
        return {
            "version": 2,
            "updated_at": None,
            "high_water": None,
            "day_key": None,
            "day_start_equity": None,
            "week_key": None,
            "week_start_equity": None,
            "deployment_stage": 1,
            "promotion_trade_offset": 0,
            "entries_blocked": True,
            "validation_lock": True,
            "simulation_mode": False,
            "permanent_lock": False,
            "pause_until": None,
            "paused_by_guard": False,
            "recovery_mode": False,
            "consecutive_losses": 0,
            "api_failures": 0,
            "guard_trigger_count": 0,
            "last_trigger": None,
            "last_trigger_key": None,
            "equity": None,
            "drawdown": 0.0,
            "beta_score": None,
            "beta_regime": "blocked",
            "selected_pair": None,
            "selected_score": None,
            "market_session": "unavailable",
            "cross_asset_data_fresh": False,
            "group_exposure": {},
            "beta_entries_blocked": False,
            **self.STAGE_LIMITS[1],
        }

    def _load_state(self) -> dict[str, Any]:
        try:
            with self.settings.state_path.open(encoding="utf-8") as handle:
                loaded = json.load(handle)
            merged = {**self._default_state(), **loaded}
            merged["version"] = 2
            return merged
        except (OSError, ValueError, TypeError):
            return self._default_state()

    def _write_state(self) -> None:
        self.settings.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.settings.state_path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(self.state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, self.settings.state_path)

    def send_alert(self, message: str) -> None:
        LOG.warning("%s", message)
        if not self.settings.telegram_token or not self.settings.telegram_chat_id:
            return
        url = f"https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={"chat_id": self.settings.telegram_chat_id, "text": message},
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            LOG.error("Telegram alert failed: %s", exc.__class__.__name__)

    @staticmethod
    def _equity(balance: dict[str, Any]) -> float:
        bot_value = balance.get("total_bot")
        value = bot_value if bot_value is not None and float(bot_value) > 0 else balance.get("total")
        equity = float(value)
        if equity < 0:
            raise ValueError("Negative account equity returned by API")
        return equity

    @staticmethod
    def _closed_trades(payload: dict[str, Any]) -> list[dict[str, Any]]:
        trades = [trade for trade in payload.get("trades", []) if not trade.get("is_open", False)]
        return sorted(
            trades,
            key=lambda trade: (
                trade.get("close_timestamp") or 0,
                trade.get("trade_id") or trade.get("id") or 0,
            ),
        )

    @staticmethod
    def _loss_streak(trades: list[dict[str, Any]]) -> int:
        streak = 0
        for trade in reversed(trades):
            profit = float(trade.get("close_profit_abs") or trade.get("profit_abs") or 0.0)
            if profit >= 0:
                break
            streak += 1
        return streak

    def _reset_periods(self, now: datetime, equity: float) -> None:
        day_key = now.date().isoformat()
        week = now.isocalendar()
        week_key = f"{week.year}-W{week.week:02d}"
        if self.state.get("day_key") != day_key:
            self.state.update({"day_key": day_key, "day_start_equity": equity})
        if self.state.get("week_key") != week_key:
            self.state.update({"week_key": week_key, "week_start_equity": equity})

    def _cancel_open_orders(self) -> None:
        try:
            for trade in self.api.get("status"):
                if trade.get("has_open_orders"):
                    trade_id = trade.get("trade_id") or trade.get("id")
                    if trade_id is not None:
                        self.api.delete(f"trades/{trade_id}/open-order")
        except (requests.RequestException, ValueError, TypeError, KeyError):
            LOG.exception("Unable to cancel all open orders")

    def _lock_pairs(self) -> list[str]:
        """Return every configured or open pair without assuming a single market."""
        pairs: set[str] = set()
        try:
            payload = self.api.get("whitelist")
            if isinstance(payload, dict):
                pairs.update(
                    str(pair)
                    for pair in payload.get("whitelist", [])
                    if isinstance(pair, str) and pair
                )
        except (requests.RequestException, ValueError, TypeError, KeyError, AssertionError):
            LOG.warning("Unable to read API whitelist; using runtime/open-trade pairs")
        try:
            runtime = self.api.get("show_config")
            configured = runtime.get("pair_whitelist")
            if configured is None and isinstance(runtime.get("exchange"), dict):
                configured = runtime["exchange"].get("pair_whitelist")
            pairs.update(
                str(pair)
                for pair in (configured or [])
                if isinstance(pair, str) and pair
            )
        except (requests.RequestException, ValueError, TypeError, KeyError):
            LOG.warning("Unable to read configured pair whitelist")
        try:
            status = self.api.get("status")
            pairs.update(
                str(trade["pair"])
                for trade in status
                if isinstance(trade, dict) and isinstance(trade.get("pair"), str)
            )
        except (requests.RequestException, ValueError, TypeError, KeyError):
            LOG.warning("Unable to read open-trade pairs")
        return sorted(pairs)

    def _merge_beta_state(self, now: datetime) -> None:
        allowed = {
            "beta_score",
            "beta_regime",
            "selected_pair",
            "selected_score",
            "market_session",
            "cross_asset_data_fresh",
            "group_exposure",
        }
        path = self.settings.beta_state_path
        if path is None:
            return
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            updated = parse_time(payload.get("updated_at"))
            if (
                updated is None
                or updated > now + timedelta(seconds=5)
                or now - updated > timedelta(seconds=90)
            ):
                raise ValueError("beta state is stale")
            regime = str(payload.get("beta_regime", "blocked"))
            if regime not in {
                "strong_risk_on",
                "risk_on",
                "neutral",
                "risk_off",
                "blocked",
            }:
                raise ValueError("invalid beta regime")
            score = payload.get("beta_score")
            if score is not None and not math.isfinite(float(score)):
                raise ValueError("invalid beta score")
            if not isinstance(payload.get("group_exposure", {}), dict):
                raise ValueError("invalid group exposure")
            self.state.update({key: payload[key] for key in allowed if key in payload})
            fresh = bool(payload.get("cross_asset_data_fresh", False))
            self.state["beta_entries_blocked"] = not fresh or regime in {"blocked", "neutral"}
            if not fresh:
                self.state["beta_regime"] = "blocked"
        except (OSError, ValueError, TypeError):
            self.state.update(
                {
                    "beta_score": None,
                    "beta_regime": "blocked",
                    "selected_pair": None,
                    "selected_score": None,
                    "market_session": "unavailable",
                    "cross_asset_data_fresh": False,
                    "group_exposure": {},
                    "beta_entries_blocked": True,
                }
            )

    def _stop_trading(self, reason: str, pause_until: datetime | None, permanent: bool) -> None:
        existing_pause = parse_time(self.state.get("pause_until"))
        if self.state.get("last_trigger") == reason and (
            self.state.get("permanent_lock")
            or (
                self.state.get("paused_by_guard")
                and existing_pause is not None
                and existing_pause > utcnow()
            )
        ):
            return
        trigger_key = f"{reason}:{iso(pause_until)}:{permanent}"
        self.state.update(
            {
                "entries_blocked": True,
                "permanent_lock": permanent or self.state.get("permanent_lock", False),
                "pause_until": iso(pause_until),
                "paused_by_guard": True,
                "last_trigger": reason,
                "last_trigger_key": trigger_key,
                "guard_trigger_count": int(self.state.get("guard_trigger_count", 0)) + 1,
                "updated_at": iso(utcnow()),
            }
        )
        # Resolve open-trade pairs before force-exit can remove them from
        # status. Configured whitelist pairs are included by the same call.
        permanent_pairs = self._lock_pairs() if permanent else []
        # Persist first so strategy entry callbacks fail closed before RPC actions begin.
        self._write_state()
        try:
            self.api.post("stopentry")
        except requests.RequestException:
            LOG.exception("Stop-entry request failed")
        self._cancel_open_orders()
        try:
            self.api.post("forceexit", {"tradeid": "all", "ordertype": "market"})
        except requests.RequestException:
            LOG.exception("Force-exit request failed")
        for _ in range(3):
            try:
                if not self.api.get("status"):
                    break
            except requests.RequestException:
                break
            time.sleep(1)
        if permanent:
            if not permanent_pairs:
                LOG.error("Permanent lock could not resolve any configured or open pair")
            for pair in permanent_pairs:
                try:
                    self.api.post(
                        "locks",
                        [
                            {
                                "pair": pair,
                                "side": "*",
                                "until": "2099-12-31T23:59:59Z",
                                "reason": "risk_guard_50pct_drawdown",
                            }
                        ],
                    )
                except requests.RequestException:
                    LOG.exception("Permanent pair-lock request failed for %s", pair)
        self.send_alert(f"RISK GUARD: {reason}; trading stopped; permanent={permanent}")

    def _replay_startup_lock(self, now: datetime) -> None:
        if self._startup_lock_replayed:
            return
        self._startup_lock_replayed = True
        pause_until = parse_time(self.state.get("pause_until"))
        lock_active = bool(
            self.state.get("validation_lock")
            or self.state.get("permanent_lock")
            or (
                self.state.get("paused_by_guard")
                and (pause_until is None or pause_until > now)
            )
        )
        if not lock_active:
            return
        self.state["entries_blocked"] = True
        self._write_state()
        try:
            self.api.post("stopentry")
        except requests.RequestException:
            LOG.exception("Unable to replay persisted stop-entry lock")

    def _maybe_resume(self, now: datetime) -> None:
        if (
            self.state.get("validation_lock")
            or self.state.get("permanent_lock")
            or not self.state.get("paused_by_guard")
        ):
            return
        pause_until = parse_time(self.state.get("pause_until"))
        if pause_until and now < pause_until:
            return
        try:
            self.api.post("start")
        except requests.RequestException:
            LOG.exception("Unable to resume after temporary guard pause")
            return
        self.state.update(
            {
                "paused_by_guard": False,
                "pause_until": None,
                "last_trigger_key": None,
            }
        )
        self.send_alert("RISK GUARD: temporary pause ended; bot resumed with current risk caps")

    def _apply_stage_and_recovery_limits(self, drawdown: float, loss_streak: int) -> None:
        stage = int(self.state.get("deployment_stage", 1))
        limits = dict(self.STAGE_LIMITS.get(stage, self.STAGE_LIMITS[1]))
        if drawdown >= 0.20:
            limits = dict(self.STAGE_LIMITS[1])
            limits.update({"risk_cap": 0.01, "leverage_cap": 3.0, "margin_cap": 5.0})
            self.state["deployment_stage"] = 1
        if self.state.get("recovery_mode") or drawdown >= 0.35:
            self.state["recovery_mode"] = True
            limits.update(
                {"max_signal_level": 1, "risk_cap": 0.01, "leverage_cap": 3.0, "margin_cap": 5.0}
            )
        if self.state.get("recovery_mode") and drawdown < 0.20:
            self.state["recovery_mode"] = False
            limits = dict(self.STAGE_LIMITS[1])
        if loss_streak >= 2:
            if limits["risk_cap"] > 0.035:
                limits["risk_cap"] = 0.035
                limits["max_signal_level"] = min(limits["max_signal_level"], 2)
            elif limits["risk_cap"] > 0.02:
                limits["risk_cap"] = 0.02
                limits["max_signal_level"] = 1
            else:
                limits["risk_cap"] = min(limits["risk_cap"], 0.01)
                limits["max_signal_level"] = 1
        self.state.update(limits)

    def _maybe_promote(self, profit: dict[str, Any], closed_count: int, drawdown: float) -> None:
        if not self.settings.auto_promote or drawdown >= 0.20 or self.state.get("recovery_mode"):
            return
        offset = int(self.state.get("promotion_trade_offset", 0))
        eligible_count = max(0, closed_count - offset)
        stage = int(self.state.get("deployment_stage", 1))
        if stage == 1 and eligible_count >= 10:
            self.state["deployment_stage"] = 2
            self.send_alert("RISK GUARD: stage 2 unlocked (S signals, 7x, 15 USDT margin)")
        profit_factor = float(profit.get("profit_factor") or 0.0)
        if (
            int(self.state.get("deployment_stage", 1)) == 2
            and eligible_count >= 20
            and profit_factor >= 1.15
            and self.settings.splus_approved
        ):
            self.state["deployment_stage"] = 3
            self.send_alert("RISK GUARD: stage 3 unlocked (S+ signals, up to 10x)")

    def tick(self) -> dict[str, Any]:
        now = utcnow()
        self._merge_beta_state(now)
        runtime = self.api.get("show_config")
        dry_run = runtime.get("dry_run") is True
        self.state["simulation_mode"] = dry_run
        self.state["validation_lock"] = not (dry_run or self.settings.validation_approved)
        self._replay_startup_lock(now)
        balance = self.api.get("balance")
        profit = self.api.get("profit")
        trade_payload = self.api.get("trades", params={"limit": 500, "order_by_id": False})
        equity = self._equity(balance)
        trades = self._closed_trades(trade_payload)
        closed_count = int(profit.get("closed_trade_count") or len(trades))
        loss_streak = self._loss_streak(trades)
        self._reset_periods(now, equity)

        high_water = max(float(self.state.get("high_water") or equity), equity)
        drawdown = 0.0 if high_water <= 0 else max(0.0, 1.0 - equity / high_water)
        day_start = float(self.state.get("day_start_equity") or equity)
        week_start = float(self.state.get("week_start_equity") or equity)
        daily_drawdown = 0.0 if day_start <= 0 else max(0.0, 1.0 - equity / day_start)
        weekly_drawdown = 0.0 if week_start <= 0 else max(0.0, 1.0 - equity / week_start)
        self.state.update(
            {
                "updated_at": iso(now),
                "equity": equity,
                "high_water": high_water,
                "drawdown": drawdown,
                "daily_drawdown": daily_drawdown,
                "weekly_drawdown": weekly_drawdown,
                "consecutive_losses": loss_streak,
                "closed_trade_count": closed_count,
                "profit_factor": float(profit.get("profit_factor") or 0.0),
                "simulation_mode": dry_run,
                "api_failures": 0,
            }
        )

        self._maybe_promote(profit, closed_count, drawdown)
        self._apply_stage_and_recovery_limits(drawdown, loss_streak)

        if drawdown >= 0.50:
            self._stop_trading("50% high-water drawdown", None, True)
        elif weekly_drawdown >= 0.35:
            self._stop_trading("35% weekly drawdown", now + timedelta(days=7), False)
        elif drawdown >= 0.35:
            self._stop_trading("35% high-water recovery pause", now + timedelta(hours=72), False)
        elif daily_drawdown >= 0.20:
            tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
            self._stop_trading("20% daily drawdown", tomorrow, False)
        elif loss_streak >= 3:
            self._stop_trading("three consecutive losses", now + timedelta(hours=24), False)
        else:
            self._maybe_resume(now)

        pause_until = parse_time(self.state.get("pause_until"))
        self.state["entries_blocked"] = bool(
            self.state.get("validation_lock")
            or self.state.get("permanent_lock")
            or self.state.get("beta_entries_blocked")
            or (self.state.get("paused_by_guard") and (pause_until is None or now < pause_until))
        )
        self._write_state()
        return self.state

    def record_failure(self, exc: Exception) -> None:
        failures = int(self.state.get("api_failures", 0)) + 1
        self.state.update(
            {
                "updated_at": iso(utcnow()),
                "api_failures": failures,
                "entries_blocked": True,
                "reason": "risk_guard_api_failure",
            }
        )
        self._write_state()
        LOG.error("Risk guard tick failed: %s", exc.__class__.__name__)
        if failures == 3:
            self.send_alert("RISK GUARD: API unavailable for three checks; new entries blocked")

    def reset(self, stage: int, reset_high_water: bool) -> None:
        if stage not in self.STAGE_LIMITS:
            raise ValueError("stage must be 1, 2, or 3")
        balance = self.api.get("balance")
        profit = self.api.get("profit")
        dry_run = self.api.get("show_config").get("dry_run") is True
        equity = self._equity(balance)
        high_water = equity if reset_high_water else max(float(self.state.get("high_water") or 0), equity)
        self.state = {
            **self._default_state(),
            "updated_at": iso(utcnow()),
            "equity": equity,
            "high_water": high_water,
            "deployment_stage": stage,
            "promotion_trade_offset": int(profit.get("closed_trade_count") or 0),
            "simulation_mode": dry_run,
            "validation_lock": not (dry_run or self.settings.validation_approved),
            "entries_blocked": not (dry_run or self.settings.validation_approved),
            **self.STAGE_LIMITS[stage],
        }
        self._write_state()
        if dry_run or self.settings.validation_approved:
            self.api.post("start")
        self.send_alert(f"RISK GUARD manually reset to stage {stage}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("once", help="Run one guard evaluation and exit")
    reset = sub.add_parser("reset", help="Clear locks/counters and set a deployment stage")
    reset.add_argument("--stage", type=int, choices=(1, 2, 3), default=1)
    reset.add_argument("--reset-high-water", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("RISK_GUARD_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
    except (ValueError, TypeError) as exc:
        LOG.error("Invalid settings: %s", exc)
        return 2
    guard = RiskGuard(settings)
    if args.command == "reset":
        guard.reset(args.stage, args.reset_high_water)
        return 0
    if args.command == "once":
        try:
            guard.tick()
            return 0
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            guard.record_failure(exc)
            return 1
    while True:
        started = time.monotonic()
        try:
            guard.tick()
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            guard.record_failure(exc)
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, settings.interval_seconds - elapsed))


if __name__ == "__main__":
    sys.exit(main())
