#!/usr/bin/env python3
"""Install the validated dry-run deployment on the CloudCone host.

This script is intended to run as root on the target host after the candidate
tree has been copied to /root/freqtrade-candidate-20260720.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


ROOT = Path("/root/freqtrade")
CANDIDATE = Path("/root/freqtrade-candidate-20260720")
BACKUP_ROOT = Path("/root/freqtrade-backups")


def run(*args: str, cwd: Optional[Path] = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def quote_env(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Environment values must be single-line")
    return json.dumps(value, ensure_ascii=False)


def required(value: object, name: str) -> str:
    result = str(value or "")
    if not result:
        raise RuntimeError(f"Existing configuration is missing {name}")
    return result


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
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


def resolve_credentials(
    old_config: dict[str, object],
    old_env: dict[str, str],
    candidate_env: dict[str, str],
) -> dict[str, str]:
    """Resolve candidate secrets first, with the active deployment as fallback."""
    exchange = old_config.get("exchange", {})
    api = old_config.get("api_server", {})
    if not isinstance(exchange, dict) or not isinstance(api, dict):
        raise RuntimeError("Existing configuration has invalid exchange/api sections")

    def value(env_name: str, config_value: object = "", default: str = "") -> str:
        return str(
            candidate_env.get(env_name) or config_value or old_env.get(env_name) or default
        )

    return {
        "exchange_key": required(
            value("FREQTRADE__EXCHANGE__KEY", exchange.get("key")), "exchange.key"
        ),
        "exchange_secret": required(
            value("FREQTRADE__EXCHANGE__SECRET", exchange.get("secret")), "exchange.secret"
        ),
        "exchange_password": required(
            value("FREQTRADE__EXCHANGE__PASSWORD", exchange.get("password")), "exchange.password"
        ),
        "api_username": value(
            "FREQTRADE__API_SERVER__USERNAME", api.get("username"), "freqtrader"
        ),
        "api_password": value(
            "FREQTRADE__API_SERVER__PASSWORD", api.get("password"), secrets.token_urlsafe(32)
        ),
        "jwt_secret": value(
            "FREQTRADE__API_SERVER__JWT_SECRET_KEY",
            api.get("jwt_secret_key"),
            secrets.token_urlsafe(48),
        ),
    }


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Run this installer as root")
    if ROOT.resolve() != Path("/root/freqtrade"):
        raise RuntimeError("Unexpected deployment root")

    old_config_path = ROOT / "user_data/config.json"
    old_config = json.loads(old_config_path.read_text(encoding="utf-8"))
    old_env = read_env_file(ROOT / ".env")
    candidate_env = read_env_file(CANDIDATE / ".env")
    credentials = resolve_credentials(old_config, old_env, candidate_env)
    exchange_key = credentials["exchange_key"]
    exchange_secret = credentials["exchange_secret"]
    exchange_password = credentials["exchange_password"]
    api_username = credentials["api_username"]
    api_password = credentials["api_password"]
    jwt_secret = credentials["jwt_secret"]

    database = ROOT / "user_data/tradesv3.sqlite"
    if database.exists():
        with sqlite3.connect(database) as connection:
            open_trades = int(
                connection.execute("SELECT COUNT(*) FROM trades WHERE is_open = 1").fetchone()[0]
            )
        if open_trades:
            raise RuntimeError(f"Refusing deployment with {open_trades} open trade(s)")

    required_candidates = [
        CANDIDATE / "docker-compose.yml",
        CANDIDATE / "runtime/config.json",
        CANDIDATE / "runtime/risk_guard.py",
        CANDIDATE / "runtime/strategies/OkxAggressiveTrendV1.py",
        CANDIDATE / "runtime/strategies/OkxAggressiveTrendV2.py",
    ]
    missing = [str(path) for path in required_candidates if not path.is_file()]
    if missing:
        raise RuntimeError(f"Candidate files missing: {missing}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_ROOT / f"aggressive-{stamp}"
    BACKUP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)

    run("docker", "compose", "down", cwd=ROOT)
    shutil.copytree(ROOT, backup, symlinks=True)

    shutil.copy2(CANDIDATE / "docker-compose.yml", ROOT / "docker-compose.yml")
    shutil.copy2(CANDIDATE / "runtime/config.json", ROOT / "user_data/config.json")
    shutil.copy2(CANDIDATE / "runtime/risk_guard.py", ROOT / "user_data/risk_guard.py")
    shutil.copy2(
        CANDIDATE / "runtime/strategies/OkxAggressiveTrendV1.py",
        ROOT / "user_data/strategies/OkxAggressiveTrendV1.py",
    )
    shutil.copy2(
        CANDIDATE / "runtime/strategies/OkxAggressiveTrendV2.py",
        ROOT / "user_data/strategies/OkxAggressiveTrendV2.py",
    )

    runtime_link = ROOT / "runtime"
    if runtime_link.exists() or runtime_link.is_symlink():
        if not runtime_link.is_symlink() or runtime_link.resolve() != (ROOT / "user_data").resolve():
            raise RuntimeError(f"Refusing to replace unexpected path: {runtime_link}")
    else:
        runtime_link.symlink_to("user_data", target_is_directory=True)

    guard_dir = ROOT / "user_data/risk_guard"
    guard_dir.mkdir(mode=0o750, exist_ok=True)
    os.chown(guard_dir, 1000, 1000)

    env_values = {
        "FREQTRADE__EXCHANGE__KEY": exchange_key,
        "FREQTRADE__EXCHANGE__SECRET": exchange_secret,
        "FREQTRADE__EXCHANGE__PASSWORD": exchange_password,
        "FREQTRADE__API_SERVER__USERNAME": api_username,
        "FREQTRADE__API_SERVER__PASSWORD": api_password,
        "FREQTRADE__API_SERVER__JWT_SECRET_KEY": jwt_secret,
        "RISK_GUARD_API_USERNAME": api_username,
        "RISK_GUARD_API_PASSWORD": api_password,
        "RISK_GUARD_STATE_PATH": "/freqtrade/user_data/risk_guard/state.json",
        "RISK_GUARD_INTERVAL_SECONDS": "30",
        "RISK_GUARD_VALIDATION_APPROVED": "false",
        "RISK_GUARD_LIVE_APPROVED": "false",
        "RISK_GUARD_SPLUS_APPROVED": "false",
        "RISK_GUARD_TELEGRAM_TOKEN": "",
        "RISK_GUARD_TELEGRAM_CHAT_ID": "",
    }
    env_text = "".join(f"{key}={quote_env(value)}\n" for key, value in env_values.items())
    env_tmp = ROOT / ".env.tmp"
    env_tmp.write_text(env_text, encoding="utf-8")
    os.chmod(env_tmp, 0o600)
    env_tmp.replace(ROOT / ".env")

    deployed_files = {
        ROOT / "user_data/config.json": 0o640,
        ROOT / "user_data/risk_guard.py": 0o750,
        ROOT / "user_data/strategies/OkxAggressiveTrendV1.py": 0o640,
        ROOT / "user_data/strategies/OkxAggressiveTrendV2.py": 0o640,
    }
    for path, mode in deployed_files.items():
        os.chown(path, 1000, 1000)
        os.chmod(path, mode)

    run("docker", "compose", "config", "--quiet", cwd=ROOT)
    run("docker", "compose", "up", "-d", cwd=ROOT)
    print(f"Installed dry-run deployment. Backup: {backup}")


if __name__ == "__main__":
    main()
