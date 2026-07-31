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

## 2026-07-27 V3 Zero-Trade Diagnosis
- The zero-trade result is not caused by missing entry signals. Across the real
  OKX interval, the direct 4h pipeline produced 320 raw long breakouts, 171
  volume-confirmed long candidates, 121 candidates with complete beta state and
  score at least 55, and 34 candidates inside the applicable cash session.
- The full Freqtrade indicator pipeline also produced entry flags for every
  sufficiently seasoned tradable pair except SAMSUNG and SKHYNIX.
- The direct blocker is a double reserve: Freqtrade's
  `get_total_stake_amount()` applies `tradable_balance_ratio=0.50`, turning the
  30 USDT wallet into 15 USDT, after which V3 subtracts its fixed 15 USDT cash
  reserve and returns zero stake for every signal.
- A single temporary backtest override of `tradable_balance_ratio=0.99` changed
  no strategy logic and produced 13 trades, proving the sizing blocker.
- The unblocked result remained economically unacceptable: -1.657%, PF 0.245,
  Sharpe -1.09, with all 13 trades long. Four full stop losses overwhelmed
  eight small winners.
- The short pipeline found 91 technically complete short candidates but none
  overlapped the narrow beta score range 25-40; only 12 complete 4h bars were
  classified risk-off.
- Three AAPL long exits simultaneously set `exit_long` and `exit_short`; the
  later shared `exit_tag` assignment mislabeled them as `short_channel_exit`.
  Their long-channel exit condition was valid, so this is a reporting/contract
  defect rather than the cause of the zero-trade result.
- SAMSUNG and SKHYNIX lack sufficient daily history for 60-day momentum. They
  should remain fail-closed until their informative history is seasoned.

## V3 Optimization Design Decision
- Do not tune breakout length, ADX, Beta thresholds, or stop distances against
  the failed 2026 execution interval. That interval remains a frozen
  engineering/forward check rather than a parameter-training set.
- Stake sizing needs two distinct values: raw account equity for the 0.75% risk
  budget and reserve calculation, and Freqtrade's ratio-adjusted/max-stake
  allowance for executable capacity. Reusing ratio-adjusted equity for both
  concepts caused the zero-stake defect.
- Keep `tradable_balance_ratio=0.50` as an additional Freqtrade capacity ceiling
  for the 30 USDT Stage-1 account. Compute the 15 USDT reserve from raw wallet
  total exactly once and retain `max_stake` as a separate upper bound.
- Entry direction should consume the categorical `beta_regime`, not repeat
  numeric thresholds. Long entries require `risk_on` or `strong_risk_on`;
  shorts require `risk_off`. This removes boundary drift between scoring,
  signals, and stake sizing without weakening the intended policy.
- Shared `exit_tag` cannot safely represent simultaneous long and short exit
  masks. Make the masks mutually exclusive for tagging while preserving the
  side-relevant exit signal, so long trades cannot be reported as short exits.

## Optimized V3 First Execution Revalidation
- Freqtrade 2026.6 discovers V1, V2, and the optimized V3 with `OK` status in
  the isolated CloudCone validation directory.
- The optimized V3 now executes with the reviewed
  `tradable_balance_ratio=0.50`; the zero-stake blocker is fixed without a
  temporary ratio override.
- The exact comparable 2026 run produced the same 13 trades and -0.497 USDT
  (-1.66%), PF 0.24, closed-trade Sharpe -1.09, and 1.66% maximum drawdown.
  This is expected because the engineering fix makes the production
  configuration equivalent to the prior ratio-only diagnostic.
- The exit-tag correction is visible: the three affected AAPL exits now report
  `dual_channel_exit` instead of the false `short_channel_exit`.
- All 13 trades remain long; the categorical regime contract removes the
  score-40 neutral mismatch but does not manufacture short trades.
- Engineering correctness is restored, but economic quality is unchanged.
  Further work must diagnose candidate quality and loss asymmetry rather than
  weakening signal thresholds to increase sample count.

## Trade-Level Structural Diagnosis
- The four full stops are two BTC trades (-0.437 USDT combined), one NVDA
  (-0.115), and one GOOGL (-0.101). BTC therefore contributes about 67% of
  gross stop-loss damage.
- BTC was stopped and re-entered at the same 04:00 UTC slot on consecutive
  days. The inherited three-candle cooldown is only three hours, while the
  global StoplossGuard activates after two losses. It cannot prevent the second
  same-pair whipsaw.
- The strategy is a cross-asset rotation system but has no one-loss,
  pair-specific quarantine. A 48-hour pair-specific StoplossGuard is a
  structural risk control: it forces rotation away from a failed breakout
  without changing entry thresholds or using future data.
- The NVDA loss exited with Beta Score 50.33 (`neutral`), even though new long
  exposure is prohibited in neutral regimes. Existing long positions currently
  ignore that state deterioration until channel/EMA/stop exits.
- Regime-consistent exits should close a long when fresh beta state becomes
  neutral or risk-off, and close a short when fresh state leaves risk-off.
  Missing/blocked data should continue to allow normal protective exits rather
  than automatically forcing a market order.
- Entry candidate scores span winners and losers broadly in this 13-trade
  sample; adding a score cutoff based on the same 2026 observations would be
  direct overfitting. Keep ranking relative and do not add a fitted threshold.

## Structural-Exit Experiment Result
- The fresh-regime exit plus pair quarantine experiment worsened the frozen
  2026 result: 16 trades, -2.15%, PF 0.29, Sharpe -1.35, and 2.49% drawdown.
- Earlier regime exits freed the single position slot and allowed additional
  losing entries in MSFT, GOOGL, and NVDA. This raised turnover and gross stop
  damage instead of improving loss asymmetry.
- A logically plausible risk rule is not automatically beneficial in this
  architecture because `max_open_trades=1` couples exits to the next
  cross-sectional selection. Exit changes must be evaluated as portfolio
  sequence changes, not isolated trade improvements.
- Do not retain the regime-exit experiment. Restore channel/EMA exits while
  keeping the proven capital fix, regime entry contract, and truthful
  side-agnostic channel tag.
- Verify whether the pair quarantine actually moved or blocked the second BTC
  entry before deciding whether it belongs in the final engineering patch.

## Final Optimization Decision
- The pair quarantine delayed the second BTC entry from 2026-05-05 04:00 UTC
  to 2026-05-06 12:00 UTC but it still ended in a full stop. It was removed
  together with the harmful regime-exit experiment.
- The final candidate contains only changes supported by deterministic
  correctness evidence: raw-equity risk sizing, ratio-adjusted execution
  capacity, categorical regime entry validation, and truthful side-agnostic
  channel-exit metadata.
- Final real-OKX result is reproducibly 13 trades, -0.497 USDT / -1.66%, PF
  0.24, Sharpe -1.09, and 1.66% drawdown. The zero-trade bug is fixed; the
  profitability gate is not.
- Final local validation passes 17 focused V3 tests and 45 complete deployment
  tests, plus compilation and whitespace checks.
- Active CloudCone V2 remains running/healthy in dry-run, its Risk Guard remains
  running, and no `freqtrade-beta-v3` container exists.
- Release verdict: V3 remains research-only. Do not deploy it even as the
  independent 30-day dry-run until a pre-2026 underlying walk-forward study is
  implemented and a newly frozen OKX forward interval passes the stated gates.

## 2026-07-27 Final-Result Attribution Baseline
- The final result archive is
  `backtest-result-2026-07-27_07-10-57.zip`; it contains exactly 13 closed
  trades and zero open trades.
- Every trade has `is_short=false`: BTC 2, NVDA 2, AAPL 4, SNDK 3, GOOGL 1,
  and TSLA 1. QQQ, AMZN, META, MSFT, MU, SAMSUNG, SKHYNIX, and ETH executed no
  trades.
- Both BTC trades used 3x leverage and roughly 2.68-2.69 USDT margin. Equity
  trades used 2x leverage and roughly 1.02-3.41 USDT margin, confirming the
  absolute-risk sizing worked as designed.
- The final archive exposes min/max rates, exact fees, stake, leverage, funding,
  duration, and orders for trade-level MFE/MAE and payoff analysis.
- The previously exported indicator table matches all 13 final entry
  timestamps, so Beta regime, group scores, candidate components, ADX, and
  realized volatility can be joined without rerunning or modifying the
  strategy.

## Final Payoff and BTC Attribution
- Gross profit is 0.161141 USDT and gross loss is 0.658258 USDT, yielding PF
  0.2448. Average winner is 0.020143 USDT while average loser is 0.131652 USDT:
  the payoff ratio is only 0.153.
- At that payoff ratio, the break-even win rate is 86.73%; the observed 61.54%
  win rate is therefore nowhere near enough. The primary failure is payoff
  asymmetry, not nominal win rate.
- BTC lost 0.437320 USDT in two trades: 66.44% of gross losses and 87.97% of
  the final net loss. Removing BTC leaves -0.059797 USDT and PF 0.729, so BTC is
  the dominant loss source but not the only reason the strategy is negative.
- Four hard-stop exits lost 0.652913 USDT. Five custom trailing-stop exits made
  only 0.026262 USDT, three channel exits made 0.134879 USDT, and the 96-hour
  time stop lost 0.005346 USDT.
- Winning trailing-stop trades captured very little of their favorable path:
  NVDA captured 2.2% of its 11.88% leveraged MFE; SNDK captured about
  1.3%-2.6% of 11.47%-21.36% MFE; TSLA captured 2.9% of 10.48% MFE.
- The three channel exits captured materially more on the two meaningful AAPL
  winners (about 41%-43% of MFE). This strongly implicates winner-management
  logic rather than entry frequency alone.
- Every full stop was approximately the 2.5% minimum price stop multiplied by
  leverage, plus fees/detail-candle slippage. BTC's losses are sized as planned,
  not caused by a broken or uncapped stop; their impact comes from two full-risk
  losses against many tiny harvested winners.

## Entry-Score and Cost Diagnostics
- Entry-score separation is weak in this tiny sample. Winners average Beta
  66.94 and candidate score 62.17; losers average Beta 69.07 and candidate
  score 58.67. Breakout, confirmation, ADX, and volatility means also overlap.
  No defensible cutoff emerges from 13 observations.
- `strong_risk_on` produced five trades, three wins, but -0.345430 USDT because
  the two BTC stops dominated. `risk_on` produced eight trades, five wins, and
  -0.151687 USDT. A higher Beta score did not imply better realized payoff.
- The test interval's underlying market change was +6.88%, reinforcing that
  zero short trades are partly regime-consistent, but it also means the short
  model received almost no empirical test.
- Ten trades incurred non-zero funding and total funding was -0.016254 USDT.
  Funding is not the main loss source, but the current backtest entry ranking
  sees zero funding rates at the sampled entry rows, so its execution score is
  not being meaningfully differentiated by carry at selection time.
- Both entry and exit fee rates are 0.10%. With many realized winners near
  0.3% leveraged return, two-sided fees and funding consume a material share
  of the small captured edge.
- There were 1,291 rejected entry signals because one-position selection and
  protections serialize the portfolio. Any earlier exit can materially change
  the next selected trade; exit optimization must be evaluated at portfolio
  sequence level.

## Mature-Method Evidence Corpus
- Moskowitz, Ooi, and Pedersen (JFE 2012) document time-series momentum across
  58 liquid futures with return persistence over roughly one to twelve months.
  V3's 30x4h long breakout is about five days, materially faster than the core
  horizon in this evidence.
- Hurst, Ooi, and Pedersen (JPM 2017) extend trend-following evidence across
  global markets back to 1880 and emphasize broad diversification. V3 instead
  serializes one position across a short, highly correlated technology/crypto
  universe.
- Moreira and Muir (JF 2017) find that scaling factor exposure down when
  realized volatility is high can improve risk-adjusted results because
  expected returns do not rise proportionally with volatility. This supports a
  volatility-target overlay, not a fitted asset blacklist.
- Asness, Moskowitz, and Pedersen (JF 2013) find momentum premia across several
  markets and a common global factor structure. The evidence supports combining
  cross-sectional and time-series information while explicitly controlling
  common factor exposure.
- Blitz, Huij, and Martens (JEF 2011) show that ranking on residual rather than
  total returns reduces time-varying factor exposures. This is directly relevant
  to ranking Mag7/memory contracts whose raw returns are dominated by QQQ beta.
- Daniel and Moskowitz (JFE 2016) show momentum can suffer crash states and that
  dynamic mean/variance scaling matters. It is a warning against treating a
  higher uncalibrated Beta Score as automatically deserving more risk.
- Peer-reviewed trend/momentum evidence is robust at diversified portfolio
  scale; it does not validate a single-winner, five-day breakout implementation
  or guarantee profitability in a 95-day OKX sample.

## Strategy-Mechanics Diagnosis
- All 13 trades are long. This is not a hidden Freqtrade-side rejection:
  the short pipeline produced 136 raw breakdowns and 91 technically complete
  candidates, but none coincided with a complete global Beta state in the
  required 25-40 risk-off band.
- Only 12 of 504 complete 4h Beta observations were risk-off. The current
  asset score structurally tilts upward because flat momentum receives a
  mid-point contribution, low volatility is rewarded, and proximity to a
  60-day high is always positive. Those risk-quality features are therefore
  also acting as directional bullish evidence.
- The short contract is a conjunction of a broad cross-asset crisis and an
  individual 80x4h bearish breakdown. It is not an idiosyncratic or
  market-neutral short model. A rising underlying market (+6.88% in the
  execution interval) makes few shorts reasonable, but leaves the entire short
  design empirically unvalidated.
- The entry rank has little observed separation: winners entered at an average
  Beta score of 66.94 versus 69.07 for losers. Raising a score threshold from
  these 13 trades would be sample fitting rather than evidence.
- The dominant economic defect is the exit payoff shape. Four complete stops
  consumed 0.652913 USDT while five trailing winners returned only 0.026262
  USDT. The inherited stop ladder frequently allowed double-digit leveraged
  MFE before realizing approximately 0.3%, so it did not preserve the rare
  large trends on which breakout systems depend.
- The prior fresh-regime-exit experiment is direct counterevidence to isolated
  trade reasoning: it increased turnover, exposed new losing selections, and
  worsened the result to -2.15%. With one position, an exit is also a decision
  about which candidate becomes eligible next.
- BTC should not be blacklisted from two observations. Its two losses are
  dominant, but the non-BTC book remains negative with PF 0.729. BTC needs an
  independently validated crypto trend forecast and group risk budget rather
  than a fitted exclusion.
- Costs are secondary in absolute dollars but important relative to the tiny
  winners. Historical selection also treats unavailable funding as zero and
  cannot reconstruct historical order-book spread quality, so the execution
  component is materially less informative in backtest than in runtime.

## Optimization Research Roadmap
- First redesign measurement, not thresholds: report every trade in R units,
  MFE/MAE, MFE capture, group, side, regime, fee, funding, and selection rank.
  Compare exit policies at full portfolio-sequence level.
- Restore trend payoff convexity by testing a small predeclared exit ensemble:
  channel-only, delayed break-even, and a monotonic peak-MFE/ATR ratchet.
  Do not optimize individual trailing constants on the 2026 interval.
- Separate direction from risk quality. Build a centered directional regime
  from signed trend, momentum, and breadth votes; use volatility, drawdown,
  dispersion, spread, and carry to scale or veto risk rather than award
  bullish score points. Add hysteresis between regimes.
- Require an absolute multi-horizon time-series trend before ranking candidates.
  Rank equity contracts on momentum residualized against QQQ or their group,
  so cross-sectional selection does not merely select the highest common
  technology Beta.
- Give BTC/ETH a separate crypto forecast and group loss budget. Test a lower
  crypto risk allocation only as a robustness control; it reduces damage but
  cannot create positive expectancy.
- Choose the short architecture explicitly. Either keep a rare broad-crisis
  short sleeve and validate it over much longer underlying history, or create
  a separate residual/idiosyncratic short forecast. Do not manufacture shorts
  by merely widening the current Beta threshold.
- Add inverse-volatility risk targeting with a volatility-jump cap. Treat
  missing funding/carry as unavailable evidence instead of neutral evidence,
  and repeat fee, slippage, and funding tests at 1.0x, 1.5x, and 2.0x.
- Research diversification at equal total risk. The current 30 USDT/minimum
  contract constraint justifies one live position, but it also prevents the
  diversification on which mature trend evidence relies. Do not increase
  position count until capital supports multiple minimum-sized positions
  without increasing account risk.
- Freeze economic hypotheses using 2018-2023 underlying data, validate on
  2024-2025, and reserve data after 2026-07-27 as the next untouched OKX
  execution forward period. The already inspected 2026 interval must not be
  reused as an economic holdout.
- Acceptance must include at least 100 closed trades, PF >= 1.20, Sharpe >=
  0.70, drawdown <= 15%, positive 1.5x cost stress, no structural loss after
  removing the top five winners, stable group/side attribution, and materially
  better winner-to-loser payoff. V3 remains research-only until those gates
  and the independent dry-run gate pass.

## V4 Implementation Baseline
- V4 starts from repository HEAD `f85738ddc0216d2ecb716d47f95fb8efc1ca6f2d`
  on `codex/okx-strategy-hardening`, while preserving the pre-existing V3
  working-tree corrections.
- Frozen V3 strategy SHA-256 is
  `ed23dab3bdd67d5e2fbdae0e6408a2d6fdf78fe7ddc6583ffaa9a333a7d76015`.
- Frozen V3 configuration SHA-256 is
  `07f382b96902603b13bb060078706f4bbf3e6b5dea2887960c561176b716dfea`.
- The economic baseline remains 13 trades, -0.497117 USDT / -1.66%, PF
  0.2448, Sharpe -1.09, all long, with BTC contributing 87.97% of net loss.
- V4 must use a new strategy class, config, database, ports, logs, Risk Guard
  state, test module, and result manifest. No cloud service is started during
  implementation.

## V4 Core Implementation Notes
- V4 subclasses V3 only to reuse reviewed asset/session/exchange controls; it
  calls V2 directly where V3's old Beta scoring or side-only candidate
  selection would otherwise run.
- Daily direction is centered and requires 120 seasoned observations. A flat
  or unseasoned series cannot receive a positive directional score.
- Equity residual momentum uses a rolling 60-day beta with 40-observation
  minimum; QQQ uses SPY, other equity contracts use QQQ, and crypto retains an
  explicitly separate group-relative path.
- The global risk-quality state is distinct from direction, while stake sizing
  uses the minimum of global and current-asset quality.
- Regime changes are stateful over the analyzed frame: two consecutive
  observations and a 0.05 hysteresis margin are required.
- Runtime selection evaluates long and short candidates together, applies
  bottom-20% group filtering to residual shorts, gives systemic shorts
  priority in risk-off, and requires a 10-point advantage over the best long
  for a residual short in ordinary risk-on.
- Exact R thresholds need floating-point tolerance; the persisted MFE remains
  the unrounded analytical value while policy transitions use a `1e-9`
  comparison tolerance.
- Identical asset and benchmark returns are explicitly assigned zero residual
  before residual-volatility normalization, avoiding artificial momentum from
  near-zero floating variance.
- V4 attribution refuses to infer initial R from Freqtrade's final stop fields.
  R expectancy is available only with a sidecar ledger keyed by
  `PAIR|OPEN_DATE`; incomplete coverage blocks exit-policy selection and
  release.
- The exit selector retains all three declared policies. A missing policy fold
  is represented as ineligible/infinite drawdown rather than silently dropped.
- Release validation is anchored to 2018-2023 research and 2024-2025
  validation; the inspected 2026 interval is not accepted as an economic
  validation argument.
- Freqtrade 2026.6 validates `config.beta-v4.json` and reports V1, V2, V3, and
  V4 as `OK` from the isolated CloudCone validation directory.
- The validation container required a writable isolated user-data directory
  only for Freqtrade-created cache folders; no active deployment path was
  mounted or modified.

## V4 2026 Diagnostic Result
- The diagnostic uses the inspected 2026-04-22 02:00 through 2026-07-27 00:00
  OKX interval, 15m detail, protections, and 0.10% fee per side. It is tagged
  `V4_DIAGNOSTIC_ONLY_NO_TUNING`.
- V4 completed end to end with one AAPL `trend_long` trade, one win,
  `channel_exit`, +0.054941 USDT / +0.18%, and 48 rejected entry signals.
- The trade held 4 days 20 hours, reached 7.87% leveraged MFE, realized 3.37%,
  and captured 42.75% of MFE. Fee plus absolute funding cost was 0.011507
  USDT.
- There were no short trades and no other group trades. One observation cannot
  estimate PF, closed-trade Sharpe, payoff, side stability, or economic edge.
- The attribution tool reports `r_coverage=0`, `r_expectancy=null` without a
  sidecar initial-risk ledger. This correctly blocks exit selection and release
  instead of inferring R from the final stop.
- V4 is executable, but every economic research, validation, cost-stress,
  sample-size, side/model, Monte Carlo, and forward gate remains unpassed.

## V4 Final Engineering Verification
- Manual funding frames requested through Freqtrade's data provider are not
  reliably truncated with the analysis endpoint. Using them in candidate
  scores caused funding and all derived execution scores to fail the
  indicator-only lookahead check.
- V4 now separates the two contexts. Live/dry-run exposes an OKX funding event
  only after its complete 1h candle and for at most 9h. Research treats that
  candidate component as unavailable, renormalizes the remaining 95%, and must
  retain the incomplete-cost label; Freqtrade can still charge funding as a
  separate realized cost.
- CloudCone Freqtrade 2026.6 reports no recursive variance at
  1000/1125/1250 startup candles and no indicator-only lookahead after the
  correction.
- Final local evidence is 72/72 deployment tests, successful compilation,
  seven parsed runtime JSON files, valid V4 Compose configuration, and a clean
  whitespace diff check.
- Frozen V3 hashes remain
  `ED23DAB3BDD67D5E2FBDAE0E6408A2D6FDF78FE7DDC6583FFAA9A333A7D76015`
  for the strategy and
  `07F382B96902603B13BB060078706F4BBF3E6B5DEA2887960C561176B716DFEA`
  for the configuration.
- Active CloudCone V2 services stayed up throughout validation. No V4 service,
  `.env`, trade database, live approval, or dry-run approval was created.
- Engineering completion is not economic approval. The one-trade 2026
  diagnostic, absent 2018-2025 research datasets, incomplete R ledger, and
  missing 30-day forward sample keep V4 offline-only.

## Fresh Comparable V3/V4 Backtest
- Both strategies were rerun from frozen hashes on the same 2026-04-22 02:00
  through 2026-07-27 00:00 OKX data, 30 USDT wallet, one-position limit,
  15m execution detail, protections, and 0.10% fee per side.
- V3 reproduced 13 all-long trades and -0.497117 USDT / -1.6571%, PF 0.2448,
  closed-trade Sharpe -1.09, 61.54% wins, and 1.6571% maximum drawdown.
- V4 produced one all-long AAPL trade and +0.054941 USDT / +0.1831%, with no
  observed drawdown. PF, payoff, closed-trade Sharpe, and statistical
  significance are undefined with no losing trade and only one observation;
  Freqtrade's `-100` sentinel must not be treated as a real Sharpe.
- The V4 trade is identical to V3's AAPL trade opened 2026-07-15 16:00 UTC and
  closed by the channel on 2026-07-20 12:00 UTC. V4 did not discover a new
  winner; it retained one V3 winner and filtered the other 12 V3 trades.
- Those 12 omitted V3 trades comprise seven winners and five losers but net
  -0.552058 USDT. Therefore the observed +1.8402 percentage-point wallet
  improvement comes entirely from abstention/filtering.
- V3 BTC contributed -0.437320 USDT across two stop-loss trades, 87.97% of
  V3's total net loss. V4 avoided both, but it also generated no BTC, ETH,
  systemic-short, or residual-short trades.
- V3 incurred 0.150813 USDT of fee plus absolute funding magnitude versus
  V4's 0.011507 USDT, largely because V4 reduced trade count by 92.31%.
- Scaling recorded fee plus absolute funding magnitude by 1.5x and 2.0x gives
  V3 -0.572524 and -0.647931 USDT versus V4 +0.049188 and +0.043434 USDT.
  This is a conservative arithmetic cost stress, not an order-book/slippage
  replay, and remains statistically meaningless for V4 with one trade.
- SAMSUNG and SKHYNIX have no rows left after the shared 1250-candle warm-up.
  The comparison therefore cannot evaluate those two storage contracts.
- Descriptively V4 is safer and better on this 95-day interval. Economically,
  one retained winner is not evidence of materially higher expectancy and
  does not satisfy any sample, side/model, PF, Sharpe, or forward gate.
- Final verdict: V4 shows a promising reduction in false/expensive exposure,
  especially the two BTC stop-outs, but no demonstrated increase in per-trade
  edge. It should remain offline-only until the long historical and new
  forward samples distinguish robust selectivity from one-winner luck.

## V4 Profit Scale And Position-Limit Diagnosis
- The sole V4 winner used 1.63075 USDT margin, 2x leverage, and realized a
  +3.365684% leveraged net return. `1.63075 × 3.365684% = 0.054941 USDT`;
  the small absolute profit is mathematically consistent with the 30 USDT
  wallet and 0.50%-0.75% account-risk budgets.
- Applying the same net return to the full 5 USDT margin cap would produce only
  about 0.1683 USDT. Larger absolute profit cannot be manufactured from a
  30 USDT wallet without either more independent profitable trades, more
  capital, or materially greater risk.
- A three-position counterfactual, with every other input frozen, increased
  trade count from one to two. Both signals opened at 2026-07-15 16:00 UTC:
  AAPL earned +0.054941 USDT while AMZN stopped out for -0.138335 USDT.
- The multi-position result is -0.083394 USDT / -0.2780%, PF 0.3972,
  closed-trade Sharpe -0.17, and 0.4611% drawdown. Increasing the slot limit
  therefore made this sample worse, not more profitable.
- The base one-slot run reported 48 rejected signal rows, but the counterfactual
  proves they collapse to one additional distinct trade rather than 48 missed
  opportunities.
- The correct diagnosis is mixed: `max_open_trades=1` did suppress one
  simultaneous trade, but the dominant cause of low trade count remains V4's
  strict entry/regime/session/ranking filters and the short 95-day history.
- Simply increasing positions, stake, or leverage scales bad trades as well as
  good ones. The AMZN loss exceeded the AAPL gain by 2.52x, so boldness without
  independently validated edge reduces expected survival.

## 2026-07-28 V5 Optimization Design
- V4's one-position limit suppressed only one distinct AMZN trade in the
  95-day diagnostic, and that trade lost more than twice the AAPL winner.
  Increasing slots, stake, or leverage is therefore not the next optimization.
- V5 preserves V4's 60/90-bar breakout models and adds independent continuation
  entries after a completed 4h pullback touches EMA30 without breaking EMA90,
  followed by a close through the previous completed 4h high or low.
- Continuation entries cannot overlap the corresponding V4 channel breakout.
  They retain centered daily direction, regime, ADX, volume, cash-session,
  funding, spread, freshness, minimum-contract, and risk-plan vetoes.
- New long continuation risk is 0.375% in ordinary risk-on and 0.50% in strong
  risk-on, below V4's 0.50%/0.75% breakout risk. New systemic pullback shorts
  use 0.25% account risk. Total portfolio slots remain one.
- Candidate selection is cached by the completed 1h strategy candle. A
  selected pair that later fails execution remains fail-closed until the next
  candle instead of allowing pair-processing order to choose a fallback.
- V5 has its own strategy, config, Compose identities, port 8084, database,
  logs, state paths, and V4 hash manifest. All live/validation approvals remain
  false and no V5 service has been started.
- Focused V5 tests pass 8/8. The complete deployment suite passes 81/81;
  compileall, JSON parsing, Compose validation, and `git diff --check` pass.
- The first V5 diagnostic was identical to V4. Exported signals proved the only
  row with a positive long-pullback marker was the same AAPL channel breakout;
  rejected AMZN/GOOGL rows remained `trend_long`, with no continuation tag.
- Root cause was structural overlap: requiring a break of the previous 4h high
  after a pullback behaved like another breakout and did not add an independent
  entry path. V5 now defines resumption as a close beyond the previous completed
  4h close while retaining the channel-breakout exclusion and every trend,
  regime, session, execution, and lower-risk constraint.

## V5 Final Validation And Economic Assessment
- Final V5 files have SHA256
  `9c6ca720db9e24888c5d84c4280df114423ea8efedb76e1f61cc3c6209d4eb7a`
  for the strategy,
  `aa0da8161f93040c801db2aab7504662ab77d122f97f825a84ed7c8f08d2051b`
  for the config, and
  `dd13fe5297f4875343efea0c5dbd25232caa7dd484f6bfef9bcb2414dd7e3111`
  for Compose.
- Freqtrade 2026.6 discovers V1-V5 as `OK`. Recursive analysis at
  1000/1125/1250 startup candles reports no recursive variance and no
  indicator-only lookahead.
- The final frozen 2026-04-22 through 2026-07-27 diagnostic archive has SHA256
  `52176738af0cc3236ce98ce10a79f5c1550ebf362f90323d283e30c6b35762ab`.
  V5 remains identical to V4 at the portfolio level: one AAPL `trend_long`,
  +0.05494078 USDT / +0.1831%, no drawdown, and no short or crypto trades.
- That trade incurred 0.01150656 USDT of fee plus absolute funding cost and
  captured 42.75% of exported MFE. Profit factor is undefined without a loss;
  Freqtrade's one-trade Sharpe sentinel is not economic evidence. Removing the
  largest trade leaves zero profit.
- The corrected continuation model generated four AMZN signal rows from
  2026-07-17 00:00 through 03:00 UTC, all while AAPL occupied the only slot.
  A three-slot counterfactual exposed the same AMZN stop as V4 and reduced the
  account to approximately -0.084 USDT / -0.28%.
- V5 therefore improves model coverage and portfolio-selection determinism but
  does not improve realized profit on the available OKX interval. It remains
  offline-only until pre-2026 walk-forward research, 2024-2025 validation, cost
  stress, and a new untouched forward sample satisfy the existing gates.
- Final local verification passes 81/81 tests, compileall, JSON/Compose
  validation, and whitespace checks. Active CloudCone V2 remains healthy;
  the V5 comparison directory has no `.env` or database, and no V3/V4/V5
  service was started.

## 2026-07-28 V5 Long-History Deep Backtest
- The repository previously contained only a research manifest, not actual
  2018-2025 underlying history. A reproducible downloader acquired 33,508
  adjusted daily rows for 15 assets. Samsung and SK Hynix were converted from
  KRW to USD using separately downloaded KRW/USD history.
- SNDK begins only on 2025-02-13, ETH begins on 2017-11-09, and the Korean
  histories retain the limitations of adjusted Yahoo cash-market data. This is
  explicitly a `DAILY_UNDERLYING_ECONOMIC_PROXY_NOT_OKX_EXECUTION`: signals use
  completed daily closes and enter at the next session open. It does not replay
  OKX 4h order books, basis, real funding, or perpetual fills.
- The research backtester now has focused contracts for next-session
  execution, complete stop/R/cost accounting, one-slot rejection, exit-year
  attribution, no-lookahead invariance, cost monotonicity, and portfolio-risk
  scaling. The annual-attribution bug that bucketed each full timestamp as a
  separate year was corrected.

## V5 Long-History Results And Bottleneck
- The frozen one-slot V5 proxy over 2018-2023 closed 50 trades and returned
  +9.149835 USDT / +30.50%, PF 3.866, daily Sharpe 0.849, 5.89% maximum
  drawdown, and 1.194R expectancy. At 1.5x and 2.0x assumed costs it retained
  +28.21% and +27.14%.
- Those aggregate metrics hide severe concentration. Removing the largest five
  winners changes profit to -1.675906 USDT. Annual exit attribution is negative
  in 2019 and 2022, nearly flat in 2018 and 2020, and dominated by +7.996618
  USDT in 2021.
- Crypto supplied +7.764654 USDT of the 2018-2023 profit; excluding BTC reduced
  return to +6.79%. BTC is therefore not the long-history loss source, but the
  strategy is highly dependent on a few crypto trends and must cap
  concentration rather than delete BTC.
- Trend breakouts supplied +9.685083 USDT. Continuation longs lost -0.017217
  USDT and all short models together lost -0.518030 USDT. Memory lost
  -0.255417 USDT. The full V5 additions did not improve the long research
  baseline: breakout-only returned +31.72% and long-only +31.61%.
- Walk-forward test returns were +1.87% in 2021, +0.03% in 2022, and +3.42% in
  2023. Every fold becomes negative after removing its largest five winners.
  This is the primary robustness failure.
- Frozen 2024-2025 validation closed 23 trades for +0.745164 USDT / +2.48%,
  PF 1.580, Sharpe 0.405, 2.96% drawdown, and only 0.034R expectancy. Removing
  the largest five winners gives -1.012319 USDT, while QQQ buy-and-hold returned
  +53.04%.
- Independently reset annual runs show 2024 at +2.70%, PF 2.35 and Sharpe 0.89,
  but 2025 at -1.11%, PF 0.66 and Sharpe -0.34. Continuous multi-year
  exit-profit buckets are not interchangeable with these reset/forced-exit
  annual runs.
- In validation, trend longs earned +0.713136 USDT and continuation longs
  +0.589172 USDT. All eight shorts lost, totaling -0.557144 USDT. Crypto lost
  -0.113443 USDT and memory lost -0.171170 USDT; benchmark and Mag7 were the
  only positive groups.

## Equal-Risk Position Diagnosis
- A raw three-slot counterfactual returned +57.52% over 2018-2023 and +13.52%
  over 2024-2025, but it retained the full per-trade risk and therefore raised
  concurrent account risk. It cannot justify increasing the production slot
  count.
- With three slots and every trade risk scaled to one third, the full V5 proxy
  returned +18.32% over 2018-2023 on 146 trades, PF 2.54, Sharpe 0.91, 3.14%
  drawdown, and remained +0.171521 USDT after removing the largest five
  winners. This proves overlap suppression matters, but also shows the original
  three-slot result was substantially risk amplification.
- The strongest diagnostic was three slots, long-only, and one-third risk per
  trade. It returned +19.61% over 2018-2023 on 114 trades and remained
  +0.553257 USDT after removing the top five. Frozen 2024-2025 returned +5.73%
  on 44 trades, PF 3.18, Sharpe 1.14, 1.68% drawdown, 0.807R expectancy, and
  +0.063408 USDT after removing the top five.
- Reset annual diagnostics for that variant were +4.73% in 2024 and +1.30% in
  2025. Frozen-period cost stress remained +5.43% at 1.5x and +5.13% at 2.0x.
  Mag7 supplied +1.378556 of its +1.718271 USDT profit; benchmark and memory
  had only five and six trades, respectively.
- This does not authorize deployment. The variant still has only 44 frozen
  validation trades, fails the required per-group sample counts, intentionally
  removes the required short-side samples, and has no comparable long-history
  OKX execution data. The prior 95-day real-OKX three-slot diagnostic also
  exposed a losing AMZN overlap and ended near -0.28%.
- The smallest evidence-backed next experiment is therefore an independent
  long-only portfolio version with two or three slots, a fixed account-level
  risk ceiling divided across positions, group/correlation concentration
  limits, channel-only exits, and no default short sleeve. Memory remains
  quarantined until it has at least ten positive frozen-validation trades;
  BTC remains eligible under a concentration cap. V5 itself remains offline.

## 2026-07-29 V6 80 USDT Capital Assessment
- The V6 development configuration now uses `dry_run_wallet=80`. The strategy
  reads the raw 80 USDT account equity before Freqtrade's 0.50 tradable-ratio
  ceiling, subtracts the 15 USDT reserve only once, and still limits each
  position to 5 USDT margin. Three positions can therefore use at most
  15 USDT margin; the remaining balance is account collateral and drawdown
  capacity, not idle capital that should be forced into a trade.
- V6 keeps total planned trend risk approximately equal to the former
  one-position budget by dividing model risk among three slots. At 80 USDT,
  ordinary/strong trend risk is about 0.1333/0.2000 USDT per position before
  risk-quality reduction. At the 2.5% minimum stop and 2x stock leverage, this
  corresponds to approximately 2.67/4.00 USDT margin. Stock and ETF minimum
  contracts are therefore materially more feasible than they were at 30 USDT.
- The exact isolated OKX execution diagnostic used the real 2026-04-22 through
  2026-07-27 futures data, 80 USDT wallet, three slots, 15m execution detail,
  protections, and 0.10% fee per side. It closed two simultaneous Mag7 longs:
  AAPL used 1.63075 USDT margin and earned +0.05494078 USDT; AMZN used
  1.27955 USDT and lost -0.06918184 USDT. Net was -0.01424106 USDT
  (-0.0178%), PF 0.7942, Sharpe -0.0461, and 0.0865% maximum drawdown.
  There were no BTC or ETH trades.
- The daily underlying research proxy at 80 USDT produced the same percentages
  as the 30 USDT run because no margin cap bound it: 97 trades and +16.2838%
  (+13.0270 USDT) in 2018-2023, then 36 trades and +5.7418%
  (+4.5934 USDT) in frozen 2024-2025. This remains
  `DAILY_UNDERLYING_ECONOMIC_PROXY_NOT_OKX_EXECUTION`; it does not enforce
  historical OKX order books, real funding, basis, or minimum-contract fills.
- BTC alone contributed about +7.6920 of the +13.0270 USDT research profit.
  This creates a capital-feasibility mismatch: the OKX BTC contract minimum is
  approximately 0.0001 BTC, roughly 12 USDT notional or 4 USDT margin near the
  diagnostic price at 3x leverage. V6's one-third crypto risk budget at
  80 USDT permits only about 1.78 USDT margin at a 2.5% stop, and less margin
  at wider stops. Roughly 180 USDT equity is required even at the narrow 2.5%
  stop; an 8% stop raises that estimate to roughly 576 USDT.
- Therefore 80 USDT makes V6 mechanically usable for many stock/ETF contracts
  and likely ETH, but it does not faithfully fund the BTC sleeve that supplied
  most long-history proxy profit. The live candidate path must eventually
  exclude capital-infeasible pairs before ranking and expose each pair's
  minimum required equity, otherwise a high-ranked BTC candidate can be
  rejected at sizing without admitting the next feasible candidate.
- “Use all 80 USDT” should mean use all 80 as the equity and risk denominator,
  not place all 80 as margin. Deploying the full balance as margin would
  override the 15 USDT reserve, 5 USDT per-position cap, and account-risk
  budget without creating any evidence of higher expectancy.
- Release verdict: V6 is more sensible at 80 USDT than at 30 USDT, but is not
  economically ready for live trading. The exact OKX sample is negative,
  contains only two trades, has PF below one, contains no crypto execution,
  and remains far below the 100-trade, model/group, cost, and forward dry-run
  gates. Keep V6 offline/dry-run-only and do not increase leverage or risk to
  force the BTC minimum contract.
- Final engineering evidence is 98/98 tests, successful compileall, 11 parsed
  runtime JSON files, valid V6 Compose configuration, and a clean whitespace
  diff check. No V6 service was started and active CloudCone V2 was not changed.

## 2026-07-29 V7 Moderately Bolder Design
- V6 is frozen at strategy/config/Compose SHA256 values
  `cecaf1f0f435e9611016885daf7e58818cbe1d0fc8d15e1d8ff3358ea4c298cb`,
  `f522f4bb67773a17d46e6a18599e999785caedb26921e4c8e3eb02610561f736`,
  and `ae8651925f98d2a0b657d13f70656e19ba306555ff3ca3969ed93606ff8dc3eb`.
- V7 will keep three possible slots but raise the per-candidate scale from
  one-third to one-half of the V5 model budget. A hard 0.75% aggregate initial
  account-risk ceiling will include both open trades and unexpired pending
  entry plans, so the change redistributes unused risk instead of allowing
  three independently enlarged bets.
- The single-position cap remains 0.50% account risk. Leverage remains 2x for
  stocks/ETFs and 3x for crypto; margin remains capped at 5 USDT, the cash
  reserve remains 15 USDT, shorts remain disabled, and memory remains
  execution-quarantined.
- Before portfolio ranking, V7 will estimate minimum-contract feasibility from
  completed signal data and the frozen OKX contract quantities, then retain the
  exchange-provided minimum-stake check at final sizing. This lets a feasible
  runner-up replace an unaffordable BTC or equity candidate without silently
  raising risk to force the order.
- V7 implementation keeps three slots, long-only direction, memory quarantine,
  0.90 correlation veto, group caps, channel-only exits, 2x/3x leverage,
  5 USDT margin cap, and 15 USDT reserve. Ordinary/strong trend targets become
  0.25%/0.375% per position; the single-position hard cap is 0.50% and the
  open-plus-pending aggregate cap is 0.75%.
- On the unchanged daily underlying proxy, V7 and V6 both close 97 trades in
  2018-2023. V7 improves return from 16.2838% to 25.0879%, PF from 2.9982 to
  3.0647, Sharpe from 0.9938 to 1.0270, and top-five-removed profit from
  1.2764 to 2.1973 USDT. Maximum drawdown rises from 2.2048% to 3.1816%.
- In frozen 2024-2025, both close 36 trades. V7 improves return from 5.7418%
  to 8.5672%, Sharpe from 1.2060 to 1.2308, and profit from 4.5934 to
  6.8538 USDT. PF is nearly unchanged at 3.2218 versus 3.2374; maximum
  drawdown rises from 1.6001% to 2.3796%. Profit after removing the five
  largest winners remains positive at 0.1294 USDT.
- The higher result comes from risk redistribution, not a new signal edge:
  trade count, win rate, symbols, and model mix are unchanged. The daily proxy
  still does not enforce real historical OKX minimum-contract fills, order
  books, funding, or basis.
- A fresh remote V7 OKX diagnostic was not produced because two read-only SSH
  probes returned no output. No remote files or services were changed. The
  existing exact V6 80 USDT result—two trades and -0.014241 USDT—therefore
  remains the controlling execution warning.
- Final engineering evidence is 104/104 tests, successful compileall, JSON and
  V7 Compose validation, frozen V6 hashes, and a clean whitespace diff check.
  V7 remains offline and every approval switch remains false.

## 2026-07-29 V7 Adversarial Audit Findings
- Official Freqtrade 2026.3 documents different callback order in live/dry-run
  and backtest. Live/dry-run calculates leverage and `custom_stake_amount`
  before `confirm_trade_entry`; backtest confirms first and sizes later.
  Therefore the initial concern that every live entry lacks a risk plan was
  rejected after source verification: V2's backtest bypass and live ordering
  make that specific path internally consistent.
- The verified live ordering exposes a different high-severity flaw. V7 writes
  a pending risk plan during `custom_stake_amount`, before the cross-sectional
  winner is accepted in `confirm_trade_entry`. If that pair is not selected,
  the confirmation returns false but the pending plan is not immediately
  removed. Subsequent pairs count this rejected plan as reserved risk.
- Because the stable candidate set is first built during confirmation and then
  cached for one hour, the first signaled pair in whitelist processing order
  can create a phantom reservation before the ranking is frozen. A lower-ranked
  rejected pair can therefore reduce the stake of a higher-ranked accepted pair
  or prevent later minimum-contract feasibility. Existing tests cover pending
  plans as if all are legitimate, but do not cover rejection cleanup or
  permutation invariance across Freqtrade's real callback sequence.
- Official Freqtrade documentation states that an exception raised by
  `custom_stake_amount` causes fallback to `proposed_stake`. V7's open-trade
  lookup catches only `AttributeError` and `TypeError`; database/session/runtime
  errors can escape. With configured proposed stake 5 USDT, fallback could
  bypass the 0.75% aggregate risk ceiling rather than fail closed.
- A bounded callback-level reproduction showed one ordinary 80 USDT AAPL
  sizing call returns 4 USDT and immediately leaves a 0.25% pending risk
  reservation. There is no corresponding cleanup in V6's `selected is None`
  confirmation branch, so a rejected pair retains that reservation.
- A second reproduction replaced the open-trade query with a realistic
  `RuntimeError("database unavailable")`; V7 propagated the exception out of
  `custom_stake_amount`. Under documented Freqtrade behavior this would select
  the configured 5 USDT proposed stake, confirming that the exception path is
  fail-open rather than fail-closed.
- Pending plans are in-memory only and have a ten-minute TTL. A process restart
  between order creation and fill loses the planned stop/risk record; an open
  entry order is then valued with the 2.5% fallback distance until the fill
  callback reconstructs a fail-safe stop. This can undercount an originally
  wider pending risk and is not covered by restart/reconciliation tests.
- Candidate feasibility calls the account-equity and open-trade risk scan once
  per candidate inside the confirmation path. This creates repeated persistence
  queries in a timing-sensitive callback and does not use one atomic portfolio
  snapshot. The selected set is cached for one hour without invalidation on a
  rejected order, fill, cancellation, or exit.
- V7's hard-coded minimum underlying quantities happen to match the reviewed
  snapshot for the current BTC, ETH, and stock contracts, but OKX officially
  defines derivative `minSz` and `lotSz` as contract counts whose notional also
  depends on `ctVal`, `ctMult`, instrument state, and upcoming parameter
  changes. Static underlying amounts are not a durable exchange boundary.
- The 0.75% ledger models loss at the planned price stop. It excludes fees,
  slippage, funding, gaps, and stop-limit non-fill. Freqtrade documents that
  OKX futures supports stop-limit, not stop-market, and warns that a triggered
  limit can remain unfilled in a fast move. Therefore 0.75% is planned
  continuous-price risk, not a guaranteed maximum account loss.
- The portfolio correlation veto is 60-day Pearson correlation with an absolute
  0.90 threshold. It blocks strongly negative correlations even though they
  diversify a long-only book, while allowing several positively correlated
  tech-Beta assets below 0.90. It does not control common QQQ factor exposure,
  downside correlation, or stressed gap loss.
- V7's 25.09%/8.57% proxy returns are almost entirely risk scaling rather than
  new alpha: trade counts and signals are identical to V6. In 2018-2023,
  continuation contributes only 0.0073 USDT across 50 trades, BTC contributes
  11.6089 of 20.0704 USDT profit, and 2021 contributes 14.0223 USDT. AAPL,
  META, and ETH are negative over that research sample.
- Frozen 2024-2025 reverses the continuation result to +2.9356 USDT while
  crypto is slightly negative. This regime reversal argues for a walk-forward
  model-eligibility rule, not hard deletion based on one aggregate interval.
- V7 still has only 97 research trades and 36 frozen-validation trades, no
  exact V7 OKX execution result, no V7-specific 1.5x/2x cost stress, and no
  minimum-contract replay in the daily proxy. It therefore remains below its
  own evidence threshold despite attractive aggregate metrics.
- Risk Guard observability remains single-selection oriented. It does not
  publish the full selected candidate set, reserved/remaining account risk,
  pending reservations, capital-blocked pairs, contract-metadata timestamp,
  or rejection reason. A three-position strategy cannot currently be audited
  operationally from its state file.

## V8 Priority Order From The V7 Audit
1. Make sizing fail closed: catch all expected persistence/exchange/data errors
   within the sizing boundary and return zero; never allow Freqtrade's proposed
   stake fallback to bypass the risk ledger.
2. Make admission transactional and order-invariant: compute one per-loop
   portfolio snapshot, rank and reserve winners together, attach reservation
   IDs, release immediately on confirmation rejection/order cancellation, and
   invalidate the cache on every order/trade state transition.
3. Persist and reconcile reservations across restart. Count open entry orders
   conservatively at their planned stop or the 8% maximum until confirmed.
4. Replace static contract quantities with a validated OKX instrument snapshot
   using `minSz × ctVal × ctMult`, `lotSz`, state, effective changes, price,
   and actual leverage. Keep final exchange `min_stake` rejection.
5. Add execution-aware risk: fee/funding allowance, spread/slippage buffer,
   conservative gap stress, and explicit stop-limit non-fill monitoring with
   emergency-exit escalation.
6. Replace the single Pearson veto with factor and tail limits: total QQQ Beta,
   group notional/risk, downside correlation or stressed scenario loss. Do not
   remove negative correlation by absolute value unless justified.
7. Separate model risk budgets. Continuation should not automatically receive
   larger V7 risk while its 2018-2023 contribution is approximately zero; use
   predeclared walk-forward eligibility and shrinkage, not validation tuning.
8. Add full portfolio telemetry and callback-sequence tests, including pairlist
   permutation invariance, rejection cleanup, restart reconciliation, DB
   failure, partial fill, cancel/replace, metadata changes, and three
   simultaneous minimum contracts.
9. Only after items 1-8, rerun actual V7/V8 OKX diagnostics, V7-specific cost
   stress, minimum-contract replay, and a new untouched forward dry-run. Do not
   increase risk again before these correctness gaps are closed.

## 2026-07-31 V1-V7 archive inventory

- The current strategy implementation is an inheritance/import chain, not seven
  independent files: V7 -> V6 -> V5 -> V4 -> V3 -> V2 -> V1.
- V7 directly imports V6 and V3; V6 imports V5, V3, and V2; V5 imports V4,
  V3, and V2; V4 imports V3 and V2; V3 imports V2; V2 imports V1.
- Therefore, moving V1-V6 strategy source out of Python's import path would
  break V7. Historical runnable entrypoints may be archived, but the source
  modules must remain available until V7 is made standalone.
- The working tree already contains extensive user-owned V3-V7 edits and
  untracked V4-V7 artifacts. Preserve them and avoid unrelated cleanup.
- Freqtrade's default resolver scans only top-level strategy files unless
  `recursive_strategy_search` is enabled. Moving inherited modules into a
  nested archive would require import-path and test-loader changes and would
  turn an organization task into a high-risk strategy refactor.
- Historical Compose/config pairs are true runtime entrypoints and can be
  archived safely. V7's Compose and config remain the only current candidate
  entrypoint; V1-V6 source modules stay in place solely as V7 compatibility
  dependencies and V1-V6 tests remain as regression protection.
- V7's baseline manifest validates the frozen V6 strategy, config, and Compose
  hashes. Post-archive validation must point that test at the archived V6
  config/Compose paths while preserving the recorded content hashes.
- Existing project records say the active CloudCone deployment is still V2,
  while V7 is offline and has unresolved high-severity audit findings. Treating
  every V1-V6 runtime file as unused would remove the documented production
  entrypoint. Verify remote state read-only before selecting the final archive
  set.
- The 2026-07-31 read-only server check confirms `freqtrade` and
  `freqtrade-risk-guard` are healthy. The active container command explicitly
  selects `OkxAggressiveTrendV2` with `config.json`; no V3-V7 container is
  running.
- Final archive boundary: keep the active V2 Compose/config/specification; move
  the stopped V3-V6 Compose/config/specifications and V3-V6 freeze manifests
  into one `archive/v1-v6/` tree. Keep V1-V6 strategy modules, tests, and
  research tools because V7 imports them or they preserve regression and
  reproducibility coverage.
- Version constants verified from source: V1 uses 1h candles, supports both
  sides, and originally maps A/S/S+ to 8%/12%/20% planned risk and 5x/7x/10x;
  V2 reduces those budgets to 2%/3.5%/5% and replaces tunable window logic
  with discrete long 30/15 and short 80/40 defaults.
- V3 sets 0.75% account risk, 5 USDT margin cap, 15 USDT cash reserve, one slot,
  and weighted benchmark/Mag7/memory/crypto beta groups. V4 adds model-specific
  risk, regime hysteresis, residual models, funding alignment, and configurable
  exit policy.
- V5 adds pullback/continuation models; V6 becomes long-only with three slots,
  one-third risk scaling, group caps, and a 0.90 correlation ceiling; V7 keeps
  three slots but raises per-candidate scaling to one-half, caps aggregate
  initial risk at 0.75%, caps a single position at 0.50%, and adds minimum-
  contract feasibility plus reservation/reconciliation logic.
- Economic evidence must be presented with its data boundary: V2's strict
  2021-2025 result was +62.32%, PF 3.14, 5.89% drawdown, but later hardened
  reruns and 2025 holdout were materially weaker. V3's exact 2026 diagnostic
  was 13 trades, -1.66%, PF 0.24. V4/V5 each realized only one +0.1831% AAPL
  trade in the comparable 95-day OKX sample.
- V6's exact 80 USDT OKX diagnostic closed two trades for -0.0178%, PF 0.7942;
  its long-history daily-underlying proxy returned +16.28% research and +5.74%
  frozen validation. V7 scales the same signals to +25.09%/+8.57% in that proxy
  but has no exact OKX execution result and does not introduce new alpha.
- The V7 audit reproduced two high-severity release blockers: rejected entries
  can leave phantom pending-risk reservations, and persistence errors can
  escape sizing into Freqtrade's proposed-stake fallback. V7 must remain
  offline despite the attractive proxy figures.
- Final knowledge-closeout matrix: code paths are changed-and-verified; remote
  runtime is verified-current as active V2 and was not modified; deployment
  docs and scoped agent rules are changed-and-verified; generated agent memory
  is not applicable; workspace cleanup is intentionally pending because the
  pre-existing V3-V7 work and planning records contain unique uncommitted work.
- Five-axis review found no blocking correctness, readability, architecture,
  security, or performance issue in the archive change. No dependency was
  added and no tracked `.env`, database, or log file was found.
