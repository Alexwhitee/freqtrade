# Findings

## Baseline From 2026-07-21
- Remote OKX credentials authenticated through a read-only private API call.
- Deployed configuration remained `dry_run=true`; validation and live approvals were false.
- Remote deployed strategy hash differed from the local EMA30/60 candidate.
- Deployed-version backtest: 33 trades, +62.32%, PF 3.14, max drawdown 5.89%.
- Lookahead: 20 signals, no detected bias.
- Recursive analysis: maximum indicator variance about 0.002%.
- Historical funding-rate data was missing for the main 2021-2025 backtest period.
- Monte Carlo 100-trade drawdown: p95 16.28%, p99 20.32%.
- Deflated Sharpe probability: 76.76%, positive but not strong evidence.

## Open Questions
- How concentrated is profit in the largest trades and market regimes?
- Which half-year/year windows are fragile or loss-making?
- Does the local EMA30/60 candidate improve robustness or merely change fitted behavior?
- Are live risk controls consistent with the intended account-level loss budget?

## 2026-07-23 Runtime Check
- Both containers are still running; Freqtrade is healthy and Risk Guard reports zero API failures.
- Dry-run database still has zero total/open/closed trades after about two days.
- Equity remains 29.7 USDT and Stage 1 remains active.
- Multiple 1h/4h WebSocket exceptions occurred across July 21-22; this is recurring rather than a single transient event.
- Deployed source hash remains `e3ae0e5bd555...`; the local candidate remains a different strategy version.
- The deployed full-history result through 2026-07-19 has 35 trades, +74.05%, PF 3.30, and 5.89% drawdown.
- The strict 2021-2025 result has 33 trades, +62.32%, PF 3.14, and 5.89% drawdown; the 2026 holdout adds only two trades.

## Trade-Level Decomposition
- Full result has 35 trades: 22 winners, 13 losers, gross profit 31.869 USDT, gross loss 9.655 USDT.
- Profit is highly concentrated: the top 1/3/5 winners contribute 20.15%/44.88%/65.11% of gross profit.
- Removing the top five winners leaves only 1.464 USDT net profit. This is consistent with trend following, but makes inference fragile.
- Long side: 28 trades, PF 2.396, net +12.194 USDT. Short side: only 7 trades, PF 11.905, net +10.020 USDT; the short estimate is too sparse to trust.
- Regime weakness is visible: 2025 H1 was almost flat (+0.038 USDT, PF 1.039) and 2025 H2 lost 1.256 USDT (PF 0.461).
- Median gap between entries is 35.75 days and the maximum gap is 171 days, explaining zero dry-run trades after two days.
- All 11 hard stop-loss exits lost a combined 8.928 USDT; 19 trailing exits won a combined 21.225 USDT. Outcome shape is strongly right-skewed.
- The largest winner returned 6.422 USDT; the largest single loss was 0.994 USDT. The strategy depends on preserving rare large trends.

## Initial Implementation Review
- Risk-based sizing formula is conceptually correct for futures margin: `equity * risk_fraction / (price_stop_distance * leverage)`.
- Sizing reads stop distance from the latest analyzed candle, but the distance is persisted only when `custom_stoploss` first runs. A candle transition or callback delay could cause planned and actual risk to diverge.
- Leverage is reduced as NATR rises and capped by Risk Guard; NATR above 3% falls back to 1x instead of blocking entries.
- Runtime state is fail-closed when missing or older than 90 seconds, and entry confirmation also rejects stale candles and spreads above 0.1%.
- Exchange stop orders are limit orders, so a fast gap can exceed modeled loss or remain unfilled.
- Strategy coverage is weak: only one V2 signal-direction test exists. There are no tests for sizing/stop consistency, leverage boundaries, live approval, funding veto, stale state, stale candle, or spread rejection.

## Risk Guard Review
- State persistence is atomic and written before stop/exit RPC actions, so strategy callbacks fail closed first.
- Stage 2 automatically unlocks after 10 eligible trades without a PF threshold. With the current A-only signal implementation this does not raise A risk above 2%, but future S signals would make the assumption important.
- Stage 3 requires 20 trades, PF >= 1.15, drawdown below 20%, and explicit S+ approval.
- Guard actions are intentionally aggressive: daily drawdown >=20%, three consecutive losses, weekly drawdown >=35%, or high-water drawdown >=35% force-exit all positions.
- Live entry independently requires `RISK_GUARD_LIVE_APPROVED`; the existing test suite does not cover this gate.
- Runtime equity uses `total_bot` when positive, otherwise `total`; this should be integration-tested against OKX live account responses before release.

## Cost, Leverage, and Parameter Evidence
- Backtest fee was 0.10% on both entry and exit, which is a useful conservative trading-fee stress.
- Historical funding fees sum to exactly zero, so the test is not a complete holding-cost stress.
- 34 of 35 trades used 5x leverage and one used 3x. Historical risk diversification came mostly from stake sizing, not leverage adaptation.
- Margin stake ranged from 1.900 to 7.888 USDT with a 5.335 USDT median.
- The stored `initial_stop_loss_ratio` is the static -50% safety stop, not evidence that the ATR-derived initial stop used for sizing was persisted correctly.
- The parameter grid contains 65 trials and visibly large dispersion, including near-flat combinations and materially profitable combinations. Stable-neighborhood analysis is still required.

## Parameter Robustness
- The deployed default `(adx_offset=0, channel_scale=1.0, atr_multiplier=2.0, trail_atr_multiplier=3.5)` ranked 2nd of 65 by total profit in the recorded grid.
- The best neighbor differed only by `atr_multiplier=1.8` and improved recorded profit by 0.24 USDT, with slightly higher drawdown (5.94% vs 5.90%). This is not a meaningful reason to switch.
- `adx_offset=0` is the strongest broad region (27 trials, median +12.4 USDT, 100% positive); `adx_offset=3` is fragile (17 trials, median +1.12 USDT, 82.4% positive).
- `channel_scale=1.0` and `1.2` were broadly positive; `0.8` was less stable.
- `trail_atr_multiplier=3.5` has the strongest median among the three trail settings, but its edge is still based on the same small, correlated sample.
- Parameter evidence supports keeping the current defaults while validating on fresh data, not tuning them further on the same history.

## Decision
- Do not optimize entry/exit parameters yet. The main problem is evidence quality and execution consistency, not an obvious parameter defect.
- Highest-priority engineering validation: persist the exact initial stop distance at entry and test that sizing, stoploss, and exchange stop order use the same value.
- Highest-priority operational validation: investigate recurring OKX WebSocket disconnects and prove REST fallback does not leave stale candles or missed stop updates.
- Highest-priority research validation: rebuild a funding-cost-aware dataset, run rolling walk-forward and untouched holdouts, and require materially positive results across multiple windows rather than one aggregate PF.
- Only consider a parameter change if it improves the fresh holdout without degrading the 2025 weak regime and survives fee/slippage/funding stress.
- The dry-run acceptance protocol itself needs revision: 35 trades over about 5.4 years is roughly 6.5 trades/year, so collecting 10 dry-run trades would take about 18 months at the historical rate. A 14/30-day infrastructure soak and a trade-count evidence gate should be separate requirements.
- Establish one canonical strategy version before any further optimization. The deployed EMA20/50/100/200 build and local EMA30/60 candidate must not share validation claims.

## 2026-07-23 Implementation Result
- V2 now records the exact stop distance used by stake sizing in a short-lived entry risk plan and persists it to the trade on the first entry fill.
- Runtime entry confirmation rejects missing, expired, or materially rate-mismatched risk plans before an order is placed.
- Later entry-side fills cannot overwrite an existing initial stop distance; exit fills are ignored.
- A missing fill plan or legacy trade without persisted data uses a conservative 2.5% price-distance fail-safe instead of recalculating from a later candle.
- Coverage expanded from one V2 direction test to 14 V2 tests, plus 9 Risk Guard tests (23 total deployment tests).
- Final tests passed, Python compilation passed, and Freqtrade 2026.6 strategy discovery reported V1 and V2 as `OK`.
- Local container validation requires non-empty API username/password and a JWT secret of at least 32 characters, as already represented by `.env.example`; secrets were not written into configuration.

## 2026-07-26 Deployment Blocker
- CloudCone preflight passed with `dry_run=true`, zero trades/open positions, all live gates false, protected environment permissions, synchronized time, and expected egress IP.
- The supplied key/secret match the existing remote values, but OKX returns `50111 Invalid OK-ACCESS-KEY` for both live and sandbox private API calls.
- The supplied account login password is not an API passphrase; the full-width punctuation form cannot be encoded as an OKX authentication header.
- Deployment was intentionally stopped before any container shutdown. A newly created OKX API key, secret, and dedicated API passphrase are required.

## 2026-07-27 Credential And Release Check
- The ignored local `.env` has valid syntax, no duplicate keys, protected Windows ACLs, and remains Git-ignored.
- The current OKX API key, secret, and dedicated API passphrase passed a read-only `fetch_balance` call from the whitelisted CloudCone egress IP.
- The local Freqtrade API username/password pair matches the Risk Guard pair and authenticated successfully against the current remote read-only balance endpoint.
- Docker Compose configuration validation passed and `runtime/config.json` remains `dry_run=true`.
- The three Risk Guard approval flags in the local `.env` are currently true and must be forced false before any deployment.
- Existing aggregate backtest claims belong to the old deployed strategy hash. The hardened candidate requires its own backtest before it can replace the active deployment.

## 2026-07-27 Hardened V2 Backtest
- Current candidate `OkxAggressiveTrendV2` was executed from `/root/freqtrade-candidate-20260720` with the CloudCone historical dataset, 1h main timeframe, 15m detail timeframe, protections enabled, and 0.1% fee per side.
- Full range (effective 2021-02-22 through 2026-01-01): 35 trades, +9.834 USDT / +32.78%, PF 2.08, win rate 54.3%, absolute drawdown 3.177 USDT / 9.57%, Sharpe 0.09, mean-profit p-value 0.1908.
- The full-range result has a 514-day drawdown period and no historical funding data for the tested period, so it is not a complete futures-cost test.
- 2025 holdout: 9 trades, -0.219 USDT / -0.73%, PF 0.83, Sharpe -0.04, mean-profit p-value 0.8337, drawdown 2.19%.
- Decision: evidence is insufficient for live trading. Keep live approvals disabled and use dry-run only for operational observation.

## 2026-07-27 Dry-Run Deployment Result
- Candidate `.env` was uploaded only to the protected candidate directory, validated with Compose, and removed after installation.
- Installer now gives the candidate `.env` precedence over the old deployment credentials, preventing a stale passphrase from being silently reused.
- Existing deployment was backed up at `/root/freqtrade-backups/aggressive-20260726T173719Z` before replacement.
- Freqtrade is `running|healthy`; Risk Guard is `running`; API read-only balance request returned HTTP 200.
- Runtime remains `dry_run=true`, all three approval flags are false, total/open trades are both zero, and Risk Guard reports `api_failures=0`, `entries_blocked=false`, `simulation_mode=true`.
- Startup log scan found no runtime error, traceback, exception, or failed startup message.

## 2026-07-27 Cross-Asset V3 Baseline
- The current OKX account and CCXT 4.5.61 expose active linear swaps for QQQ,
  all Magnificent Seven names, BTC, ETH, MU, SNDK, SAMSUNG, SKHYNIX, and the
  observation-only DRAM/LITE/SKHY instruments.
- CCXT normalizes the contracts to Freqtrade symbols such as
  `QQQ/USDT:USDT`, `NVDA/USDT:USDT`, and `SAMSUNG/USDT:USDT`.
- OKX contract history is short: the first major stock/ETF contracts begin
  2026-03-04, DRAM begins 2026-05-08, SAMSUNG/SKHYNIX begin 2026-06-10, and
  SKHY begins 2026-07-10.
- The repository and active Python environment do not include an exchange
  calendar package. V3 will use a small standard-library session gate plus a
  reviewed holiday data file, avoiding a new Freqtrade-wide dependency.
- The existing Risk Guard contains a hard-coded BTC pair lock and must become
  whitelist/open-trade driven before V3 can be considered safe.
- V3 implementation is authorized for code and offline validation only. The
  active V2 dry-run must remain untouched.
- V2 exposes reusable fail-closed entry checks through `_confirm_trade_entry`,
  exact stake-time stop persistence, and fill-safe initial-risk storage. V3 can
  inherit these controls while overriding asset risk ceilings, sizing, ranking,
  and session eligibility.
- The current Risk Guard permanently locks only `BTC/USDT:USDT`. V3 needs a
  robust union of configured whitelist pairs and currently open-trade pairs
  before posting permanent locks.
- The existing test harness stubs Freqtrade and TA-Lib modules, so V3 pure
  scoring, calendar, classification, and ranking logic can be tested without a
  Docker image or network connection.
- V3 now shifts every daily feature to its next-day availability timestamp and
  performs backward-only alignment with a 36-hour tolerance. Missing QQQ,
  BTC, ETH, fewer than five Mag7 members, or fewer than two memory members
  produces a blocked state.
- V3 enforces its Stage-1 caps inside the strategy even if a shared Risk Guard
  reports looser V2 stage limits: 0.75% planned risk, 5 USDT margin, 15 USDT
  reserve, 2x equity/ETF leverage, and 3x crypto leverage.
- Permanent Risk Guard locks now resolve the configured whitelist and snapshot
  open-trade pairs before force-exit, eliminating the BTC-only assumption.
- Underlying research is explicitly separated from OKX execution validation.
  Korean research prices are converted using date-aligned KRW/USD data, and
  missing/duplicate input dates fail validation.
- Forty local deployment tests pass. Compose and all JSON files validate; the
  V3 config is ignored by the repository's broad `config*.json` rule and must
  be force-added after review, exactly as the reviewed V2 config was.
- The 2026.6 Freqtrade container now discovers V1, V2, and V3 with `OK` status.
- Public OKX execution data was downloaded in an isolated validation directory
  for 15m/1h/4h/1d futures candles plus 1h mark/funding data. No private API or
  order endpoint was used.
- The completed 2026 execution backtest covered 2026-04-22 through 2026-07-27
  after warm-up and produced zero trades. This is an explicit failed evidence
  gate, not a profitability result; thresholds were not weakened to create
  trades.
- Recursive analysis at 1000/1125/1250 candles found no recursive variance and
  its indicator-only lookahead check found no lookahead bias.
- Freqtrade's full Lookahead command cannot complete for the entire stock-perp
  universe because version 2026.6 unconditionally changes stake to 10,000,
  which exceeds META's OKX leverage-tier maximum. The blocker is recorded and
  must not be bypassed by changing production risk limits.
- Final review found and fixed missing V3 daily-informative registration,
  cross-sectional relative-strength ranking, live spread scoring, strict beta
  state validation, and Pandas 3 timestamp-unit compatibility.
- After isolated validation, the active V2 Freqtrade container remained
  running/healthy and its Risk Guard remained running. No V3 service exists or
  was started.
