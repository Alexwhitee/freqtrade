#!/usr/bin/env python3
"""Validate a V7 deployment without changing configuration or placing orders."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


APPROVAL_GATES = (
    "RISK_GUARD_VALIDATION_APPROVED",
    "RISK_GUARD_LIVE_APPROVED",
    "RISK_GUARD_SPLUS_APPROVED",
    "BETA_V7_LIVE_APPROVED",
)
REQUIRED_SECRETS = (
    "FREQTRADE__EXCHANGE__KEY",
    "FREQTRADE__EXCHANGE__SECRET",
    "FREQTRADE__EXCHANGE__PASSWORD",
    "FREQTRADE__API_SERVER__USERNAME",
    "FREQTRADE__API_SERVER__PASSWORD",
    "FREQTRADE__API_SERVER__JWT_SECRET_KEY",
    "RISK_GUARD_API_USERNAME",
    "RISK_GUARD_API_PASSWORD",
)
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
OKX_ALGO_ORDER_TYPES = (
    "conditional",
    "oco",
    "trigger",
    "move_order_stop",
    "iceberg",
    "twap",
)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        values[key] = str(value)
    return values


def parse_bool(value: object, name: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be an explicit boolean")


def database_state(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        open_trades = (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM trades WHERE is_open = 1"
                ).fetchone()[0]
            )
            if "trades" in tables
            else 0
        )
        open_orders = (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM orders WHERE ft_is_open = 1"
                ).fetchone()[0]
            )
            if "orders" in tables
            else 0
        )
        return open_trades, open_orders
    finally:
        connection.close()


def validate_snapshot(
    path: Path,
    pairs: list[str],
    now: datetime,
    max_age: timedelta,
) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    captured_at = datetime.fromisoformat(str(payload["captured_at"]))
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    captured_at = captured_at.astimezone(timezone.utc)
    age = now.astimezone(timezone.utc) - captured_at
    if age < -timedelta(minutes=5) or age > max_age:
        raise ValueError("OKX market snapshot is stale")
    markets = {
        str(row["symbol"]): row
        for row in payload.get("markets", [])
        if isinstance(row, dict) and "symbol" in row
    }
    for pair in pairs:
        market = markets.get(pair)
        if not market:
            raise ValueError(f"market snapshot is missing {pair}")
        if not all(market.get(key) is True for key in ("active", "swap", "linear")):
            raise ValueError(f"market snapshot rejects {pair}")
        if float(market.get("amount_min") or 0) <= 0:
            raise ValueError(f"market snapshot has invalid amount_min for {pair}")
        if float(market.get("contract_size") or 0) <= 0:
            raise ValueError(f"market snapshot has invalid contract_size for {pair}")
    return markets


def validate_risk_ledger(
    path: Path,
    pairs: list[str],
    now: datetime,
) -> str:
    if not path.exists():
        return "absent"
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("V7 risk ledger must not be group/world accessible")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or payload.get("valid") is not True:
        raise ValueError("V7 risk ledger header is invalid")
    reservations = payload.get("reservations")
    if not isinstance(reservations, list):
        raise ValueError("V7 risk ledger reservations are invalid")
    pending_risk = 0.0
    seen: set[tuple[str, str]] = set()
    for row in reservations:
        if not isinstance(row, dict):
            raise ValueError("V7 risk ledger row is invalid")
        pair = str(row.get("pair", ""))
        side = str(row.get("side", "")).lower()
        key = (pair, side)
        stop_distance = float(row.get("stop_distance") or 0)
        rate = float(row.get("rate") or 0)
        risk_fraction = float(row.get("risk_fraction") or 0)
        created_at = datetime.fromisoformat(str(row.get("created_at", "")))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age = now.astimezone(timezone.utc) - created_at.astimezone(timezone.utc)
        if (
            pair not in pairs
            or side != "long"
            or key in seen
            or not 0.025 <= stop_distance <= 0.080
            or rate <= 0
            or not 0 < risk_fraction <= 0.005
            or age < -timedelta(minutes=5)
            or age > timedelta(minutes=10)
        ):
            raise ValueError("V7 risk ledger contains an invalid reservation")
        seen.add(key)
        pending_risk += risk_fraction
    if pending_risk > 0.0075 + 1e-12 or not math.isclose(
        pending_risk,
        float(payload.get("pending_risk_fraction") or 0),
        abs_tol=1e-12,
    ):
        raise ValueError("V7 risk ledger total is invalid")
    return "valid"


def validate_local(
    root: Path,
    env_path: Path,
    *,
    require_live: bool,
    now: datetime,
    max_snapshot_age: timedelta,
) -> dict[str, Any]:
    config = json.loads(
        (root / "runtime/config.beta-v7.json").read_text(encoding="utf-8")
    )
    compose = (root / "docker-compose.beta-v7.yml").read_text(encoding="utf-8")
    env = read_env(env_path)
    if os.name != "nt" and stat.S_IMODE(env_path.stat().st_mode) & 0o077:
        raise ValueError("environment file must not be group/world accessible")
    if config.get("max_open_trades") != 3:
        raise ValueError("V7 max_open_trades must remain 3")
    if config.get("order_types", {}).get("stoploss") != "market":
        raise ValueError("V7 requires market stoploss orders")
    if config.get("order_types", {}).get("stoploss_on_exchange") is not True:
        raise ValueError("V7 requires stoploss_on_exchange")
    pairs = list(config.get("exchange", {}).get("pair_whitelist", []))
    if not pairs:
        raise ValueError("V7 pair whitelist is empty")
    for gate in APPROVAL_GATES:
        if f"${{{gate}:-false}}" not in compose:
            raise ValueError(f"Compose does not default {gate} closed")
    if "BETA_V7_RISK_LEDGER_PATH" not in compose:
        raise ValueError("Compose does not configure the V7 risk ledger")
    validate_snapshot(
        root / "runtime/okx_beta_markets.snapshot.json",
        pairs,
        now,
        max_snapshot_age,
    )
    open_trades, open_orders = database_state(
        root / "runtime/trades-beta-v7.sqlite"
    )
    ledger_state = validate_risk_ledger(
        root / "runtime/risk_guard/beta-v7-risk-ledger.json",
        pairs,
        now,
    )
    if require_live and (open_trades or open_orders):
        raise ValueError("V7 database is not flat")
    effective_dry_run = parse_bool(
        env.get("FREQTRADE__DRY_RUN", config.get("dry_run", True)),
        "FREQTRADE__DRY_RUN",
    )
    if require_live:
        missing = [name for name in REQUIRED_SECRETS if not env.get(name)]
        if missing:
            raise ValueError("required credentials are missing: " + ", ".join(missing))
        if effective_dry_run:
            raise ValueError("FREQTRADE__DRY_RUN must be false for live validation")
        closed = [name for name in APPROVAL_GATES if not parse_bool(env.get(name, "false"), name)]
        if closed:
            raise ValueError("approval gates are closed: " + ", ".join(closed))
        if env["FREQTRADE__API_SERVER__USERNAME"] != env["RISK_GUARD_API_USERNAME"]:
            raise ValueError("Risk Guard API username does not match Freqtrade")
        if env["FREQTRADE__API_SERVER__PASSWORD"] != env["RISK_GUARD_API_PASSWORD"]:
            raise ValueError("Risk Guard API password does not match Freqtrade")
    return {
        "effective_dry_run": effective_dry_run,
        "configured_pairs": len(pairs),
        "database_open_trades": open_trades,
        "database_open_orders": open_orders,
        "risk_ledger": ledger_state,
        "approval_gates_open": all(
            parse_bool(env.get(name, "false"), name) for name in APPROVAL_GATES
        ),
        "snapshot": "valid",
    }


def validate_okx(
    env: dict[str, str],
    pairs: list[str],
    expected_markets: dict[str, dict[str, Any]],
    exchange_factory=None,
) -> dict[str, Any]:
    try:
        if exchange_factory is None:
            import ccxt

            exchange_factory = ccxt.okx
        exchange = exchange_factory(
            {
                "apiKey": env["FREQTRADE__EXCHANGE__KEY"],
                "secret": env["FREQTRADE__EXCHANGE__SECRET"],
                "password": env["FREQTRADE__EXCHANGE__PASSWORD"],
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            }
        )
        markets = exchange.load_markets()
        for pair in pairs:
            market = markets.get(pair)
            if not market or not market.get("active"):
                raise ValueError(f"OKX market is unavailable: {pair}")
            if not market.get("swap") or not market.get("linear"):
                raise ValueError(f"OKX market contract changed: {pair}")
            expected = expected_markets[pair]
            actual_amount_min = float(
                market.get("limits", {}).get("amount", {}).get("min") or 0
            )
            actual_contract_size = float(market.get("contractSize") or 0)
            if not math.isclose(
                actual_amount_min,
                float(expected["amount_min"]),
                rel_tol=1e-12,
            ) or not math.isclose(
                actual_contract_size,
                float(expected["contract_size"]),
                rel_tol=1e-12,
            ):
                raise ValueError(f"OKX contract metadata changed: {pair}")
        positions = exchange.fetch_positions()
        open_positions = [
            position
            for position in positions
            if abs(float(position.get("contracts") or 0)) > 0
        ]
        open_orders = list(exchange.fetch_open_orders())
        for order_type in OKX_ALGO_ORDER_TYPES:
            open_orders.extend(
                exchange.fetch_open_orders(params={"ordType": order_type})
            )
        if open_positions or open_orders:
            raise ValueError("OKX account has open positions or orders")
        exchange.fetch_balance()
        return {
            "private_authentication": "valid",
            "okx_open_positions": 0,
            "okx_open_orders": 0,
            "live_markets": len(pairs),
        }
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"OKX read-only preflight failed: {exc.__class__.__name__}"
        ) from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--check-okx", action="store_true")
    parser.add_argument("--max-snapshot-age-hours", type=float, default=168.0)
    args = parser.parse_args()
    try:
        report = validate_local(
            args.root,
            args.env_file,
            require_live=args.require_live,
            now=datetime.now(timezone.utc),
            max_snapshot_age=timedelta(hours=args.max_snapshot_age_hours),
        )
        if args.require_live and not args.check_okx:
            raise ValueError("--require-live also requires --check-okx")
        if args.check_okx:
            config = json.loads(
                (args.root / "runtime/config.beta-v7.json").read_text(
                    encoding="utf-8"
                )
            )
            pairs = list(config["exchange"]["pair_whitelist"])
            expected_markets = validate_snapshot(
                args.root / "runtime/okx_beta_markets.snapshot.json",
                pairs,
                datetime.now(timezone.utc),
                timedelta(hours=args.max_snapshot_age_hours),
            )
            report.update(
                validate_okx(
                    read_env(args.env_file),
                    pairs,
                    expected_markets,
                )
            )
        report["ok"] = True
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                indent=2,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
