# OKX Aggressive Trend deployment

The server currently runs `OkxAggressiveTrendV2` and an isolated
`OkxCrossAssetBetaV7` candidate in dry-run. The complete
Chinese V1-V7 inventory, version history, risk comparison, evidence, and
archive map is available in
[STRATEGIES_V1_TO_V7_CN.md](STRATEGIES_V1_TO_V7_CN.md). The detailed V2
specification remains in
[OKX_AGGRESSIVE_TREND_V2_STRATEGY_CN.md](OKX_AGGRESSIVE_TREND_V2_STRATEGY_CN.md).

This deployment is deliberately configured for dry-run first. It trades only
`BTC/USDT:USDT`, uses isolated margin, and delegates account-level drawdown
enforcement to a separate persistent risk-guard service.

Historical V3-V6 runtime entrypoints and documentation are under
[`archive/v1-v6/`](archive/v1-v6/README_CN.md). Their strategy modules remain
in `runtime/strategies` because V7 imports the complete V1-V6 inheritance
chain. V7's identified engineering blockers are fixed and it may run as an
isolated dry-run candidate. It has not completed a new untouched forward
dry-run period and is not approved for unattended live deployment.

## Safety defaults

- `dry_run=true`, `dry_run_wallet=30`
- one open trade maximum
- FreqUI binds to `127.0.0.1:8081`
- exchange/API/Telegram secrets come only from `.env`
- dry-run validation is allowed while live trading remains blocked by both
  `RISK_GUARD_VALIDATION_APPROVED=false` and `RISK_GUARD_LIVE_APPROVED=false`
- S+ / 10x trading cannot unlock until `RISK_GUARD_SPLUS_APPROVED=true`
- a missing or stale risk-state file blocks all new entries

V7 adds a persistent pending-risk ledger, audited OKX contract metadata, and
four independent live-entry gates. Its Compose defaults keep every gate closed.
See [V7_LIVE_ACTIVATION_CN.md](V7_LIVE_ACTIVATION_CN.md) for the read-only
preflight, manual activation, emergency stop, and rollback procedure.

## Setup

1. Copy `.env.example` to `.env`, fill the exchange and API values, then set
   file permissions to `600`.
2. Create runtime directories: `runtime/logs`, `runtime/data`,
   `runtime/backtest_results`, and `runtime/risk_guard`.
   When deploying as root, set the runtime tree owner to the container user:
   `chown -R 1000:1000 runtime`.
3. Start with `docker compose up -d`.
4. Inspect `docker compose logs -f freqtrade risk-guard` and access FreqUI
   through an SSH tunnel to local port 8081.

Telegram stays disabled until both the bot token and chat ID are configured.

## Historical data and validation

Run inside the Freqtrade service:

```bash
docker compose run --rm freqtrade download-data \
  --config /freqtrade/user_data/config.json \
  --trading-mode futures \
  --pairs BTC/USDT:USDT \
  --timeframes 15m 1h 4h 1d \
  --candle-types futures \
  --timerange 20210101- \
  --prepend

docker compose run --rm freqtrade download-data \
  --config /freqtrade/user_data/config.json \
  --trading-mode futures \
  --pairs BTC/USDT:USDT \
  --timeframes 1h \
  --candle-types mark funding_rate \
  --timerange 20210101- \
  --prepend

docker compose run --rm freqtrade backtesting \
  --config /freqtrade/user_data/config.json \
  --strategy OkxAggressiveTrendV2 \
  --timeframe 1h --timeframe-detail 15m \
  --timerange 20210101-20260101 \
  --enable-protections --fee 0.001 \
  --export trades

docker compose run --rm freqtrade lookahead-analysis \
  --config /freqtrade/user_data/config.json \
  --strategy OkxAggressiveTrendV2 \
  --timeframe 1h --timerange 20210101-20260101

docker compose run --rm freqtrade recursive-analysis \
  --config /freqtrade/user_data/config.json \
  --strategy OkxAggressiveTrendV2 \
  --timeframe 1h --timerange 20250101-20260101 \
  --startup-candle 900 1000 1250
```

Do not enable live trading until walk-forward, holdout, stress-cost and signal
grade acceptance criteria are met.

## Stage control

V2 initially emits only A-grade entries at 2% planned equity risk. S and S+
remain disabled until their own samples pass independent validation. The guard
can promote infrastructure limits after ten eligible closed trades, but this
does not manufacture higher-grade strategy signals. Stage 3 additionally
requires 20 trades, profit factor at least 1.15, drawdown below 20%, and
explicit S+ validation approval.

Before changing from dry-run to live, reset promotion counters and the high
water mark:

```bash
docker compose run --rm risk-guard /freqtrade/user_data/risk_guard.py \
  reset --stage 1 --reset-high-water
```

After a permanent 50% drawdown lock, use the same reset command only after an
explicit strategy review. The exchange transfer, `dry_run=false`,
`RISK_GUARD_VALIDATION_APPROVED=true`, and
`RISK_GUARD_LIVE_APPROVED=true` are separate manual release gates.
