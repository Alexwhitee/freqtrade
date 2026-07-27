"""Cross-asset beta rotation for OKX linear perpetuals.

V3 keeps V2's fail-closed execution and exact initial-risk persistence, but
adds a completed-daily cross-asset regime, deterministic single-winner
selection, cash-session entry gates for equity contracts, and Stage-1 account
risk limits.  It never requests third-party market data in the trading loop.
"""

import calendar
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame, Series

from freqtrade.persistence import Trade
from freqtrade.strategy import informative
from OkxAggressiveTrendV2 import OkxAggressiveTrendV2


logger = logging.getLogger(__name__)
UTC = timezone.utc

Group = Literal["benchmark", "mag7", "memory", "crypto"]
Session = Literal["us", "kr", "always"]
Regime = Literal["strong_risk_on", "risk_on", "neutral", "risk_off", "blocked"]


@dataclass(frozen=True)
class AssetSpec:
    pair: str
    group: Group
    session: Session
    tradable: bool
    max_leverage: float
    stop_min: float
    stop_max: float


@dataclass(frozen=True)
class BetaRegimeState:
    updated_at: datetime
    score: float
    regime: Regime
    benchmark_score: float
    mag7_score: float
    memory_score: float
    crypto_score: float
    valid_assets: dict[str, int]
    data_complete: bool


def _pair(base: str) -> str:
    return f"{base}/USDT:USDT"


ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec(_pair("QQQ"), "benchmark", "us", True, 2.0, 0.025, 0.060),
    AssetSpec(_pair("SPY"), "benchmark", "us", False, 1.0, 0.025, 0.060),
    *(
        AssetSpec(_pair(base), "mag7", "us", True, 2.0, 0.025, 0.060)
        for base in ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA")
    ),
    *(
        AssetSpec(_pair(base), "memory", session, True, 2.0, 0.025, 0.060)
        for base, session in (
            ("MU", "us"),
            ("SNDK", "us"),
            ("SAMSUNG", "kr"),
            ("SKHYNIX", "kr"),
        )
    ),
    AssetSpec(_pair("BTC"), "crypto", "always", True, 3.0, 0.025, 0.080),
    AssetSpec(_pair("ETH"), "crypto", "always", True, 3.0, 0.025, 0.080),
)
ASSET_BY_PAIR = {asset.pair: asset for asset in ASSETS}
TRADABLE_PAIRS = tuple(asset.pair for asset in ASSETS if asset.tradable)
BETA_INPUT_PAIRS = tuple(asset.pair for asset in ASSETS)
OBSERVATION_PAIRS = (_pair("DRAM"), _pair("LITE"), _pair("SKHY"))


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first_weekday, _ = calendar.monthrange(year, month)
    day = 1 + (weekday - first_weekday) % 7 + (occurrence - 1) * 7
    return date(year, month, day)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    _, last_day = calendar.monthrange(year, month)
    value = date(year, month, last_day)
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def _observed(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm, used only to derive Good Friday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


class MarketSessionCalendar:
    """Fail-closed US/KR cash-session entry calendar.

    Regular recurring US holidays are calculated. Exchange-specific closures,
    Korean lunar holidays, and early closes are supplied in a reviewed JSON
    file so they are auditable and can be extended without changing strategy
    code.
    """

    _ZONE = {"us": ZoneInfo("America/New_York"), "kr": ZoneInfo("Asia/Seoul")}
    _HOURS = {
        "us": (time(9, 45), time(15, 45)),
        "kr": (time(9, 15), time(15, 15)),
    }

    def __init__(self, calendar_path: Path | None = None):
        path = calendar_path or Path(
            os.getenv(
                "BETA_MARKET_CALENDAR_PATH",
                "/freqtrade/user_data/market_calendars.beta.json",
            )
        )
        self.closed: dict[str, set[date]] = {"us": set(), "kr": set()}
        self.early_close: dict[str, dict[date, time]] = {"us": {}, "kr": {}}
        self.covered_years: dict[str, set[int]] = {"us": set(), "kr": set()}
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            for session in ("us", "kr"):
                values = payload.get(session, {})
                self.closed[session] = {
                    date.fromisoformat(value) for value in values.get("closed", [])
                }
                self.covered_years[session] = {
                    int(year) for year in values.get("covered_years", [])
                }
                self.early_close[session] = {
                    date.fromisoformat(day): time.fromisoformat(close)
                    for day, close in values.get("early_close", {}).items()
                }
        except (OSError, ValueError, TypeError):
            # US recurring rules remain available. KR fails closed unless the
            # requested day is an ordinary weekday explicitly covered below.
            logger.error("Reviewed market-calendar file is unavailable: %s", path)
            self._calendar_loaded = False
        else:
            self._calendar_loaded = True

    @staticmethod
    def _us_recurring_holidays(year: int) -> set[date]:
        holidays = {
            _observed(date(year, 1, 1)),
            _nth_weekday(year, 1, calendar.MONDAY, 3),
            _nth_weekday(year, 2, calendar.MONDAY, 3),
            _easter_sunday(year) - timedelta(days=2),
            _last_weekday(year, 5, calendar.MONDAY),
            _observed(date(year, 6, 19)),
            _observed(date(year, 7, 4)),
            _nth_weekday(year, 9, calendar.MONDAY, 1),
            _nth_weekday(year, 11, calendar.THURSDAY, 4),
            _observed(date(year, 12, 25)),
        }
        return holidays

    def is_open_for_entry(self, session: Session, current_time: datetime) -> bool:
        if session == "always":
            return True
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        local = current_time.astimezone(self._ZONE[session])
        local_date = local.date()
        if local.weekday() >= 5:
            return False
        if session == "us" and local_date in self._us_recurring_holidays(local_date.year):
            return False
        if local_date in self.closed[session]:
            return False
        # KR lunar/exchange holidays cannot be derived safely. Missing reviewed
        # calendar coverage therefore blocks KR entries.
        if session == "kr" and (
            not self._calendar_loaded or local_date.year not in self.covered_years["kr"]
        ):
            return False
        start, regular_end = self._HOURS[session]
        end = self.early_close[session].get(local_date, regular_end)
        return start <= local.timetz().replace(tzinfo=None) < end

    def label(self, session: Session, current_time: datetime) -> str:
        return f"{session}_{'open' if self.is_open_for_entry(session, current_time) else 'closed'}"


class OkxCrossAssetBetaV3(OkxAggressiveTrendV2):
    """Stage-1 cross-asset beta rotation with a single selected position."""

    # OKX exposes at most 1500 candles per request. 1250 covers the inherited
    # 4h/1d warm-up while remaining within Freqtrade's exchange limit.
    startup_candle_count = 1250
    _ACCOUNT_RISK = 0.0075
    _MARGIN_CAP = 5.0
    _CASH_RESERVE = 15.0
    _GROUP_WEIGHT = {"benchmark": 0.35, "mag7": 0.30, "memory": 0.20, "crypto": 0.15}
    _MIN_VALID = {"benchmark": 1, "mag7": 5, "memory": 2, "crypto": 2}
    _BETA_DAILY_TTL = pd.Timedelta(hours=36)
    _CRITICAL_INTRADAY_TTL = pd.Timedelta(hours=2, minutes=5)

    plot_config = {
        "main_plot": {
            "ema30_4h": {"color": "blue"},
            "ema60_4h": {"color": "orange"},
        },
        "subplots": {
            "Beta": {"beta_score": {"color": "green"}},
            "Candidate": {"candidate_score": {"color": "purple"}},
            "Funding": {"funding_rate": {"color": "gray"}},
        },
    }

    @classmethod
    def asset_spec(cls, pair: str) -> AssetSpec | None:
        return ASSET_BY_PAIR.get(pair)

    def _calendar(self) -> MarketSessionCalendar:
        value = getattr(self, "_market_session_calendar", None)
        if value is None:
            value = MarketSessionCalendar()
            self._market_session_calendar = value
        return value

    def informative_pairs(self):
        funding = [(pair, "1h", "funding_rate") for pair in TRADABLE_PAIRS]
        daily = [(pair, "1d") for pair in BETA_INPUT_PAIRS]
        return funding + daily

    @staticmethod
    def _daily_asset_features(dataframe: DataFrame) -> DataFrame:
        required = {"date", "close"}
        if dataframe is None or dataframe.empty or not required.issubset(dataframe.columns):
            return DataFrame()
        result = dataframe[["date", "close"]].copy()
        result["date"] = pd.to_datetime(
            result["date"], utc=True, errors="coerce"
        ).astype("datetime64[ns, UTC]")
        result["close"] = pd.to_numeric(result["close"], errors="coerce")
        result = result.dropna().sort_values("date").drop_duplicates("date")
        result["ema20"] = result["close"].ewm(span=20, adjust=False, min_periods=20).mean()
        result["ema60"] = result["close"].ewm(span=60, adjust=False, min_periods=60).mean()
        result["mom20"] = result["close"].pct_change(20)
        result["mom60"] = result["close"].pct_change(60)
        result["realized_vol20"] = (
            np.log(result["close"]).diff().rolling(20).std() * np.sqrt(252)
        )
        result["drawdown60"] = result["close"] / result["close"].rolling(60).max() - 1.0
        result["above20"] = result["close"] > result["ema20"]
        result["above60"] = result["close"] > result["ema60"]
        trend = result["above20"].astype(float) * 10 + (
            result["ema20"] > result["ema60"]
        ).astype(float) * 20
        momentum = (
            (result["mom20"].clip(-0.20, 0.20) + 0.20) / 0.40 * 15
            + (result["mom60"].clip(-0.40, 0.40) + 0.40) / 0.80 * 15
        )
        vol_quality = (1.0 - result["realized_vol20"].clip(0.15, 1.00) / 1.00) * 20
        drawdown_quality = (1.0 + result["drawdown60"].clip(-0.40, 0.0) / 0.40) * 20
        result["asset_score"] = (trend + momentum + vol_quality + drawdown_quality).clip(0, 100)
        # A daily candle becomes available only after it has closed.
        result["available_at"] = result["date"] + pd.Timedelta(days=1)
        return result

    def _daily_frame(self, pair: str) -> DataFrame:
        try:
            frame = self.dp.get_pair_dataframe(pair=pair, timeframe="1d")
        except Exception as exc:  # noqa: BLE001 - absence must fail closed.
            logger.warning("Daily beta input unavailable for %s: %s", pair, exc.__class__.__name__)
            return DataFrame()
        return self._daily_asset_features(frame)

    @staticmethod
    def _group_score(row: Series, pairs: list[str]) -> tuple[float, int]:
        scores: list[float] = []
        above20: list[float] = []
        above60: list[float] = []
        momenta: list[float] = []
        for pair in pairs:
            prefix = pair.split("/", 1)[0].lower()
            value = row.get(f"{prefix}_score")
            if pd.notna(value):
                scores.append(float(value))
                above20.append(float(bool(row.get(f"{prefix}_above20", False))))
                above60.append(float(bool(row.get(f"{prefix}_above60", False))))
                momentum = row.get(f"{prefix}_mom20")
                if pd.notna(momentum):
                    momenta.append(float(momentum))
        if not scores:
            return 0.0, 0
        breadth = (np.mean(above20) + np.mean(above60)) * 12.5
        dispersion = min(15.0, float(np.std(momenta)) * 100.0) if momenta else 15.0
        score = np.clip(np.mean(scores) * 0.75 + breadth - dispersion, 0.0, 100.0)
        return float(score), len(scores)

    @staticmethod
    def _regime(score: float, complete: bool) -> Regime:
        if not complete or not np.isfinite(score):
            return "blocked"
        if score >= 70:
            return "strong_risk_on"
        if score >= 55:
            return "risk_on"
        if score >= 40:
            return "neutral"
        if score >= 25:
            return "risk_off"
        return "blocked"

    def _beta_regime_frame(self, dates: Series) -> DataFrame:
        base = DataFrame(
            {
                "date": pd.to_datetime(dates, utc=True, errors="coerce").astype(
                    "datetime64[ns, UTC]"
                )
            }
        )
        cache_key = (
            len(base),
            str(base["date"].iloc[0]) if len(base) else "",
            str(base["date"].iloc[-1]) if len(base) else "",
        )
        cached = getattr(self, "_beta_frame_cache", None)
        if cached and cached[0] == cache_key:
            return cached[1].copy()

        merged = base.sort_values("date")
        for pair in BETA_INPUT_PAIRS:
            prefix = pair.split("/", 1)[0].lower()
            features = self._daily_frame(pair)
            columns = {
                "asset_score": f"{prefix}_score",
                "above20": f"{prefix}_above20",
                "above60": f"{prefix}_above60",
                "mom20": f"{prefix}_mom20",
                "mom60": f"{prefix}_mom60",
                "realized_vol20": f"{prefix}_vol20",
                "available_at": f"{prefix}_available_at",
            }
            if features.empty:
                for renamed in columns.values():
                    merged[renamed] = np.nan
                continue
            right = features[list(columns)].rename(columns=columns)
            merged = pd.merge_asof(
                merged.sort_values("date"),
                right.sort_values(f"{prefix}_available_at"),
                left_on="date",
                right_on=f"{prefix}_available_at",
                direction="backward",
                tolerance=self._BETA_DAILY_TTL,
            )

        group_pairs = {
            group: [asset.pair for asset in ASSETS if asset.group == group]
            for group in self._GROUP_WEIGHT
        }
        rows: list[dict[str, Any]] = []
        for _, row in merged.iterrows():
            scores: dict[str, float] = {}
            counts: dict[str, int] = {}
            for group, pairs in group_pairs.items():
                scores[group], counts[group] = self._group_score(row, pairs)
            required_pairs_valid = all(
                pd.notna(row.get(f"{base}_score")) for base in ("qqq", "btc", "eth")
            )
            complete = required_pairs_valid and all(
                counts[group] >= minimum for group, minimum in self._MIN_VALID.items()
            )
            score = sum(scores[group] * weight for group, weight in self._GROUP_WEIGHT.items())
            rows.append(
                {
                    "beta_score": score,
                    "beta_regime": self._regime(score, complete),
                    "benchmark_score": scores["benchmark"],
                    "mag7_score": scores["mag7"],
                    "memory_score": scores["memory"],
                    "crypto_score": scores["crypto"],
                    "valid_benchmark": counts["benchmark"],
                    "valid_mag7": counts["mag7"],
                    "valid_memory": counts["memory"],
                    "valid_crypto": counts["crypto"],
                    "cross_asset_data_fresh": complete,
                }
            )
        result = pd.concat([base.reset_index(drop=True), DataFrame(rows)], axis=1)
        self._beta_frame_cache = (cache_key, result.copy())
        return result

    @staticmethod
    def _candidate_components(dataframe: DataFrame, side: str) -> dict[str, Series]:
        def numeric_column(name: str, default: float = 0.0) -> Series:
            if name not in dataframe:
                return pd.Series(default, index=dataframe.index, dtype=float)
            return pd.to_numeric(dataframe[name], errors="coerce").fillna(default)

        beta = pd.to_numeric(dataframe["beta_score"], errors="coerce").fillna(0)
        regime_match = beta if side == "long" else 100 - beta
        mom20 = numeric_column("mom20_1d")
        mom60 = numeric_column("mom60_1d")
        relative = ((mom20.clip(-0.20, 0.20) + 0.20) / 0.40 * 50) + (
            (mom60.clip(-0.40, 0.40) + 0.40) / 0.80 * 50
        )
        atr = pd.to_numeric(dataframe["atr_4h"], errors="coerce").replace(0, np.nan)
        if side == "long":
            breakout = (dataframe["close_4h"] - dataframe["channel_high_30_4h"]) / atr
        else:
            breakout = (dataframe["channel_low_80_4h"] - dataframe["close_4h"]) / atr
        breakout = (breakout.clip(0, 2).fillna(0) / 2 * 100)
        adx = ((pd.to_numeric(dataframe["adx_4h"], errors="coerce") - 20) / 30 * 100).clip(
            0, 100
        )
        volume_ratio = (
            dataframe["volume_4h"] / dataframe["volume_median_30_4h"].replace(0, np.nan)
        )
        confirmation = (adx * 0.65 + volume_ratio.clip(0, 2).fillna(0) / 2 * 35)
        funding = pd.to_numeric(dataframe["funding_rate"], errors="coerce").fillna(0)
        funding_quality = (
            (0.0005 - funding) / 0.001 * 100
            if side == "long"
            else (funding + 0.0005) / 0.001 * 100
        ).clip(0, 100)
        liquidity = (volume_ratio.clip(0, 2).fillna(0) / 2 * 100)
        execution = funding_quality * 0.5 + liquidity * 0.5
        return {
            "regime": regime_match.clip(0, 100),
            "relative": relative.clip(0, 100),
            "breakout": breakout.clip(0, 100),
            "confirmation": confirmation.clip(0, 100),
            "funding": funding_quality.clip(0, 100),
            "liquidity": liquidity.clip(0, 100),
            "execution": execution.clip(0, 100),
        }

    @classmethod
    def _candidate_score(cls, dataframe: DataFrame, side: str) -> Series:
        components = cls._candidate_components(dataframe, side)
        return (
            components["regime"] * 0.30
            + components["relative"] * 0.25
            + components["breakout"] * 0.20
            + components["confirmation"] * 0.15
            + components["execution"] * 0.10
        ).clip(0, 100)

    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema30"] = ta.EMA(dataframe, timeperiod=30)
        dataframe["ema60"] = ta.EMA(dataframe, timeperiod=60)
        dataframe["mom20"] = dataframe["close"].pct_change(20)
        dataframe["mom60"] = dataframe["close"].pct_change(60)
        dataframe["realized_vol20"] = np.log(dataframe["close"]).diff().rolling(20).std() * np.sqrt(
            252
        )
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        beta = self._beta_regime_frame(dataframe["date"])
        for column in (
            "beta_score",
            "beta_regime",
            "benchmark_score",
            "mag7_score",
            "memory_score",
            "crypto_score",
            "valid_benchmark",
            "valid_mag7",
            "valid_memory",
            "valid_crypto",
            "cross_asset_data_fresh",
        ):
            dataframe[column] = beta[column].to_numpy()
        for side in ("long", "short"):
            components = self._candidate_components(dataframe, side)
            for name, values in components.items():
                dataframe[f"candidate_{name}_{side}"] = values
            dataframe[f"candidate_score_{side}"] = self._candidate_score(dataframe, side)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long_signal = (
            (dataframe["close_4h"] > dataframe["channel_high_30_4h"])
            & (dataframe["ema30_4h"] > dataframe["ema60_4h"])
            & (dataframe["adx_4h"] >= 20)
            & (dataframe["volume_4h"] >= dataframe["volume_median_30_4h"])
            & (dataframe["beta_score"] >= 55)
            & dataframe["cross_asset_data_fresh"].fillna(False)
            & (dataframe["volume"] > 0)
        )
        short_signal = (
            (dataframe["close_4h"] < dataframe["channel_low_80_4h"])
            & (dataframe["ema30_4h"] < dataframe["ema60_4h"])
            & (dataframe["adx_4h"] >= 25)
            & (dataframe["volume_4h"] >= dataframe["volume_median_30_4h"])
            & (dataframe["beta_score"] >= 25)
            & (dataframe["beta_score"] <= 40)
            & dataframe["cross_asset_data_fresh"].fillna(False)
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[long_signal, ["enter_long", "enter_tag"]] = (1, "long_beta")
        dataframe.loc[short_signal, ["enter_short", "enter_tag"]] = (1, "short_beta")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["close_4h"] < dataframe["channel_low_15_4h"])
                | (dataframe["ema30_4h"] < dataframe["ema60_4h"])
            )
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "long_channel_exit")
        dataframe.loc[
            (
                (dataframe["close_4h"] > dataframe["channel_high_40_4h"])
                | (dataframe["ema30_4h"] > dataframe["ema60_4h"])
            )
            & (dataframe["volume"] > 0),
            ["exit_short", "exit_tag"],
        ] = (1, "short_channel_exit")
        return dataframe

    def _price_stop_distance(self, candle: Series | None, rate: float) -> float:
        pair = str(candle.get("pair", "")) if candle is not None else ""
        spec = self.asset_spec(pair)
        if spec is None:
            return 0.0
        atr = self._safe_float(candle.get("atr_4h"), 0.0)
        if atr <= 0 or rate <= 0:
            return 0.0
        return float(np.clip(atr * float(self.atr_multiplier.value) / rate, spec.stop_min, spec.stop_max))

    def _last_candle(self, pair: str) -> Series | None:
        candle = super()._last_candle(pair)
        if candle is not None:
            candle = candle.copy()
            candle["pair"] = pair
        return candle

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        spec = self.asset_spec(pair)
        if spec is None or not spec.tradable:
            return 1.0
        limits = self._limits()
        cap = min(
            spec.max_leverage,
            self._safe_float(limits.get("leverage_cap"), 0.0),
            self._safe_float(max_leverage, 1.0),
        )
        return max(1.0, cap) if cap > 0 else 1.0

    @staticmethod
    def _regime_risk_multiplier(regime: str) -> float:
        return {
            "strong_risk_on": 1.0,
            "risk_on": 0.65,
            "risk_off": 0.50,
        }.get(regime, 0.0)

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
        if spec is None or not spec.tradable or limits.get("entries_blocked", True):
            return 0.0
        candle = self._last_candle(pair)
        stop_distance = self._price_stop_distance(candle, current_rate)
        if stop_distance <= 0 or leverage <= 0 or candle is None:
            return 0.0
        regime = str(candle.get("beta_regime", "blocked"))
        multiplier = self._regime_risk_multiplier(regime)
        if multiplier <= 0 or (side == "long" and regime not in {"risk_on", "strong_risk_on"}):
            return 0.0
        if side == "short" and regime != "risk_off":
            return 0.0
        try:
            equity = float(self.wallets.get_total_stake_amount())
        except Exception:
            return 0.0
        risk_fraction = min(
            self._ACCOUNT_RISK * multiplier,
            self._safe_float(limits.get("risk_cap"), 0.0),
        )
        calculated = equity * risk_fraction / (stop_distance * leverage)
        stake = min(
            calculated,
            self._MARGIN_CAP,
            self._safe_float(limits.get("margin_cap"), 0.0),
            self._safe_float(max_stake, 0.0),
            max(0.0, equity - self._CASH_RESERVE),
        )
        if stake <= 0 or (min_stake is not None and stake < float(min_stake)):
            return 0.0
        self._store_entry_risk_plan(pair, side, stop_distance, current_rate, current_time)
        return round(stake, 8)

    @staticmethod
    def _select_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        valid = [candidate for candidate in candidates if np.isfinite(float(candidate["score"]))]
        if not valid:
            return None
        ordered = sorted(valid, key=lambda item: (-float(item["score"]), str(item["pair"])))
        best = ordered[0]
        tied = [item for item in ordered if float(best["score"]) - float(item["score"]) < 5.0]
        if len(tied) == 1:
            return best
        preference = {_pair("QQQ"): 0, _pair("BTC"): 1}
        return min(
            tied,
            key=lambda item: (
                preference.get(str(item["pair"]), 2),
                float(item.get("volatility", 999.0)),
                str(item["pair"]),
            ),
        )

    def _runtime_candidates(self, side: str, current_time: datetime) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for pair in TRADABLE_PAIRS:
            try:
                frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            except Exception:
                continue
            if frame is None or frame.empty:
                continue
            candle = frame.iloc[-1]
            candle_time = pd.Timestamp(candle.get("date"))
            if candle_time.tzinfo is None:
                candle_time = candle_time.tz_localize("UTC")
            now = pd.Timestamp(current_time)
            if now.tzinfo is None:
                now = now.tz_localize("UTC")
            if now - candle_time > self._CRITICAL_INTRADAY_TTL:
                continue
            signal_column = "enter_long" if side == "long" else "enter_short"
            if int(self._safe_float(candle.get(signal_column), 0)) != 1:
                continue
            spec = self.asset_spec(pair)
            if spec is None or not self._calendar().is_open_for_entry(spec.session, current_time):
                continue
            spread_quality = 50.0
            if not self._is_backtest():
                try:
                    orderbook = self.dp.orderbook(pair, 1)
                    bid = float(orderbook["bids"][0][0])
                    ask = float(orderbook["asks"][0][0])
                    mid = (ask + bid) / 2
                    spread = (ask - bid) / mid if mid > 0 else 1.0
                    spread_quality = float(np.clip(1.0 - spread / 0.001, 0.0, 1.0) * 100)
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
            candidates.append(
                {
                    "pair": pair,
                    "score": self._safe_float(candle.get(f"candidate_score_{side}"), np.nan),
                    "volatility": self._safe_float(candle.get("realized_vol20_1d"), 999.0),
                    "relative_raw": self._safe_float(
                        candle.get(f"candidate_relative_{side}"), 0.0
                    ),
                    "execution_raw": self._safe_float(
                        candle.get(f"candidate_execution_{side}"), 0.0
                    ),
                    "funding_quality": self._safe_float(
                        candle.get(f"candidate_funding_{side}"), 0.0
                    ),
                    "liquidity_quality": self._safe_float(
                        candle.get(f"candidate_liquidity_{side}"), 0.0
                    ),
                    "spread_quality": spread_quality,
                }
            )
        return self._finalize_candidate_scores(candidates)

    @staticmethod
    def _finalize_candidate_scores(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return candidates
        relative = pd.Series(
            [float(candidate["relative_raw"]) for candidate in candidates],
            dtype=float,
        )
        percentile = relative.rank(method="average", pct=True) * 100
        for index, candidate in enumerate(candidates):
            execution = (
                float(candidate["funding_quality"])
                + float(candidate["liquidity_quality"])
                + float(candidate["spread_quality"])
            ) / 3
            candidate["score"] = float(
                np.clip(
                    float(candidate["score"])
                    - float(candidate["relative_raw"]) * 0.25
                    - float(candidate["execution_raw"]) * 0.10
                    + float(percentile.iloc[index]) * 0.25
                    + execution * 0.10,
                    0.0,
                    100.0,
                )
            )
        return candidates

    def _critical_data_fresh(self, current_time: datetime) -> bool:
        for pair in (_pair("QQQ"), _pair("BTC"), _pair("ETH")):
            candle = self._last_candle(pair)
            if candle is None:
                return False
            value = pd.Timestamp(candle.get("date"))
            if value.tzinfo is None:
                value = value.tz_localize("UTC")
            now = pd.Timestamp(current_time)
            if now.tzinfo is None:
                now = now.tz_localize("UTC")
            if now - value > self._CRITICAL_INTRADAY_TTL:
                return False
        return True

    def _confirm_trade_entry(self, *args, **kwargs) -> bool:
        pair = args[0] if args else kwargs.get("pair")
        current_time = args[5] if len(args) > 5 else kwargs.get("current_time")
        side = args[7] if len(args) > 7 else kwargs.get("side")
        spec = self.asset_spec(str(pair))
        if spec is None or not spec.tradable:
            return False
        if not self._calendar().is_open_for_entry(spec.session, current_time):
            return False
        candle = self._last_candle(str(pair))
        if candle is None or not bool(candle.get("cross_asset_data_fresh", False)):
            return False
        if not self._is_backtest() and not self._critical_data_fresh(current_time):
            return False
        selected = self._select_candidate(self._runtime_candidates(str(side), current_time))
        if selected is None or selected["pair"] != pair:
            return False
        self._selected_pair = str(pair)
        self._selected_score = float(selected["score"])
        return super()._confirm_trade_entry(*args, **kwargs)

    def _beta_state_path(self) -> Path:
        return Path(
            os.getenv(
                "BETA_REGIME_STATE_PATH",
                "/freqtrade/user_data/risk_guard/beta-v3-regime.json",
            )
        )

    @staticmethod
    def _open_group_exposure() -> dict[str, int]:
        exposure = {group: 0 for group in ("benchmark", "mag7", "memory", "crypto")}
        try:
            trades = Trade.get_open_trades()
        except (AttributeError, TypeError):
            return exposure
        for trade in trades:
            spec = ASSET_BY_PAIR.get(str(getattr(trade, "pair", "")))
            if spec is not None:
                exposure[spec.group] += 1
        return exposure

    def _write_beta_state(self, current_time: datetime) -> None:
        anchor = self._last_candle(_pair("QQQ"))
        if anchor is None:
            payload: dict[str, Any] = {
                "updated_at": current_time.astimezone(UTC).isoformat(),
                "beta_score": 0.0,
                "beta_regime": "blocked",
                "selected_pair": None,
                "selected_score": None,
                "market_session": "unavailable",
                "cross_asset_data_fresh": False,
                "group_exposure": self._open_group_exposure(),
            }
        else:
            score = self._safe_float(anchor.get("beta_score"), 0.0)
            payload = {
                "updated_at": current_time.astimezone(UTC).isoformat(),
                "beta_score": score,
                "beta_regime": str(anchor.get("beta_regime", "blocked")),
                "selected_pair": getattr(self, "_selected_pair", None),
                "selected_score": getattr(self, "_selected_score", None),
                "market_session": {
                    "us": self._calendar().label("us", current_time),
                    "kr": self._calendar().label("kr", current_time),
                    "crypto": "always_open",
                },
                "cross_asset_data_fresh": bool(
                    anchor.get("cross_asset_data_fresh", False)
                    and self._critical_data_fresh(current_time)
                ),
                "group_exposure": self._open_group_exposure(),
            }
        path = self._beta_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        super().bot_loop_start(current_time, **kwargs)
        if not self._is_backtest():
            try:
                self._write_beta_state(current_time)
            except (OSError, ValueError, TypeError) as exc:
                logger.error("Unable to publish beta state: %s", exc.__class__.__name__)
