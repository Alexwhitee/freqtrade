# Task Plan: OKX Strategy Runtime Hardening and Cross-Asset Beta V5

## Goal
Preserve the validated V2 dry-run and frozen V3/V4 baselines while implementing,
testing, and evaluating an independent OKX cross-asset beta rotation V5 for
offline research validation only.

## Phases

### Phase 1: Scope and Baseline
**Status:** complete
Confirm the deployed strategy entry point, preserve parameter defaults, and identify the smallest safe implementation surface.

### Phase 2: Initial Risk Persistence
**Status:** complete
Persist the exact stop distance selected during stake sizing onto the filled trade and fail closed when an entry lacks a valid risk plan.

### Phase 3: Focused Coverage
**Status:** complete
Test sizing/stop consistency, leverage boundaries, live approval, funding veto, stale state, stale candle, and spread rejection.

### Phase 4: Validation
**Status:** complete
Run focused tests first, then the deployment test suite and relevant static checks.

### Phase 5: Commit Audit
**Status:** complete
Inventory every changed and untracked file, reject secrets/caches, and review the complete commit surface.

### Phase 6: Atomic Commits
**Status:** complete
Create a `codex/` branch and commit all safe project changes in coherent units.

### Phase 7: Push
**Status:** complete
Push the branch to the configured remote and verify its upstream state.

### Phase 8: Remote Preflight
**Status:** complete
Confirm zero open trades, current dry-run state, backup paths, container health, and the exact deployed file layout without exposing secrets.

### Phase 9: Secret Configuration
**Status:** complete
Create the ignored local `.env` from the existing protected API-service secrets, replace only the supplied OKX credentials, and validate them before stopping the old deployment.

### Phase 10: Current-Candidate Backtest
**Status:** complete
Run the current hardened V2 strategy against the available historical dataset and assess it against the documented profitability, drawdown, bias, robustness, and cost limitations.

### Phase 11: Dry-Run Deployment
**Status:** complete
Upload the reviewed candidate, back up the old deployment, install it with all live approval gates disabled, and start both services.

### Phase 12: Operational Verification
**Status:** complete
Verify container health, deployed hashes, API authentication, risk-guard freshness, dry-run state, and absence of startup errors.

### Phase 13: V3 Architecture and Contracts
**Status:** complete
Define the asset universe, beta-regime state, market-session policy, ranking
contract, independent configuration, and V2-compatible Risk Guard changes.

### Phase 14: Cross-Asset Strategy
**Status:** complete
Implement `OkxCrossAssetBetaV3` with beta scoring, pair classification,
cross-sectional selection, session gates, pair-specific risk, and inherited
fail-closed execution controls.

### Phase 15: Independent Runtime
**Status:** complete
Add a V3-only config and compose profile with a separate container, port,
database, logs, Risk Guard state, and dry-run wallet.

### Phase 16: Dynamic Risk Guard
**Status:** complete
Remove hard-coded BTC locks/exits, operate on the configured whitelist and open
trades, and publish V3 beta/selection fields without breaking V2.

### Phase 17: V3 Tests and Research Tooling
**Status:** complete
Add focused unit tests, data-quality checks, OKX specification snapshots, and
research/validation tooling without treating underlying-stock history as
synthetic OKX execution history.

### Phase 18: Validation and Review
**Status:** complete
Run the complete deployment suite, strategy discovery, config validation,
static checks, code/security review, and document all unmet backtest/release
gates. Do not deploy V3 or enable live trading.

### Phase 19: Zero-Trade Root-Cause Analysis
**Status:** complete
Reproduce the real OKX signal pipeline, isolate signal, session, regime, ranking,
and sizing gates, and prove the cause of the zero-trade execution result with a
single-variable backtest.

### Phase 20: V3 Capital and Signal Optimization
**Status:** complete
Remove the double-reserve defect, separate account risk equity from available
stake capacity, correct side-specific exit metadata, and improve the short-side
regime contract without weakening data freshness, session, liquidity, or risk
controls.

### Phase 21: Regression and Integration Coverage
**Status:** complete
Add tests using Freqtrade's ratio-adjusted wallet semantics, verify non-zero
Stage-1 stake with a 30 USDT wallet, prove reserve/caps remain enforced, and
cover exit-tag and regime-boundary behavior.

### Phase 22: Real OKX Execution Revalidation
**Status:** complete
Run the complete local suite and isolated CloudCone Freqtrade validation, then
repeat the 2026 execution backtest and compare trades, direction, payoff,
drawdown, PF, and Sharpe against the diagnosed baseline.

### Phase 23: Release Assessment
**Status:** complete
Document remaining evidence gaps and decide whether the optimized V3 is eligible
only for independent dry-run or must remain research-only. Do not replace V2 or
enable live trading.

### Phase 24: Final-Result Trade Attribution
**Status:** complete
Decompose the 13 final trades by pair, side, exit reason, gross profit/loss,
R-multiple shape, duration, and contribution to the net loss. Quantify BTC's
role and prove why no short trades executed.

### Phase 25: Strategy-Mechanics Diagnosis
**Status:** complete
Audit Beta Score calibration, entry/ranking filters, stop sizing, winner
management, funding/spread evidence, and the one-position portfolio sequence
for structural causes of negative expectancy.

### Phase 26: Mature-Method Evidence Review
**Status:** complete
Compare V3 against peer-reviewed trend following, cross-sectional momentum,
volatility management, residual momentum, and futures carry/funding methods
using verified primary sources.

### Phase 27: Optimization Research Roadmap
**Status:** complete
Prioritize engineering fixes, research hypotheses, ablation tests, and frozen
walk-forward gates without tuning against the 2026 execution holdout or
authorizing deployment.

### Phase 28: V4 Baseline And Isolation
**Status:** complete
Freeze the V3 code/config/result evidence, create independent V4 strategy,
configuration, Compose, tests, and research identities, and preserve every V2
and V3 runtime artifact.

### Phase 29: V4 Direction And Candidate Engine
**Status:** complete
Implement centered multi-horizon direction, risk-quality separation, regime
hysteresis, residual momentum, dual short models, and side-aware
cross-sectional selection without future data.

### Phase 30: V4 Risk And Exit Engine
**Status:** complete
Implement signal-model risk budgets, crypto caps, persisted R/MFE/MAE state,
and the channel-only, delayed-break-even, and slow-ATR monotonic exit policies.

### Phase 31: V4 Research And Release Tooling
**Status:** complete
Add independent dry-run configuration, Compose isolation, trade attribution,
walk-forward exit-policy selection, cost-stress reporting, and release gates.

### Phase 32: V4 Verification
**Status:** complete
Run V2/V3/V4 regression tests, compilation, JSON/Compose validation,
lookahead-safe feature tests, strategy discovery where available, and confirm
that no cloud or live approval state changed.

### Phase 33: Comparable V3/V4 Backtest
**Status:** complete
Run fresh V3 and V4 backtests against the same isolated OKX interval, public
data, fee, detail timeframe, protections, wallet, and execution assumptions.
Compare return, trade count, long/short mix, PF, Sharpe, drawdown, payoff,
costs, concentration, and rejected signals without tuning either strategy.

### Phase 34: Profit-Scale And Position-Limit Diagnosis
**Status:** complete
Explain the small absolute profit from wallet, stake, leverage, and realized
move mathematics. Run a V4-only `max_open_trades=3` counterfactual with every
other input frozen to determine whether the one-position limit, rather than
entry selectivity, caused the one-trade result. This run is diagnostic only and
must not change the Stage-1 default.

### Phase 35: V4 Freeze And V5 Design
**Status:** complete
Freeze the current V4 strategy/config/result identity and define an independent
V5 experiment that adds continuation opportunities without loosening V4
breakout thresholds, increasing portfolio slots, or changing total risk.

### Phase 36: Independent Continuation Signals
**Status:** complete
Implement completed-4h trend pullback/re-entry longs and symmetric risk-off
pullback/re-entry shorts with separate tags, candidate components, attribution,
and lower model-specific risk budgets.

### Phase 37: Stable Portfolio Candidate Selection
**Status:** complete
Select one long/short candidate across the entire pool and cache that decision
for the current base candle so sequential entry confirmations cannot change the
winner because of pair-processing order.

### Phase 38: V5 Tests And Isolated Validation
**Status:** complete
Add focused continuation, exclusivity, cache, sizing, configuration, and frozen
baseline tests, then run the complete deployment suite, compilation, strategy
discovery, and lookahead/recursive checks where the available runtime permits.

### Phase 39: Economic Assessment
**Status:** complete
Run a frozen V4/V5 comparison as an engineering diagnostic, report model and
direction attribution, and keep V5 offline unless pre-2026 walk-forward,
2024-2025 validation, cost stress, and new forward evidence pass the existing
economic gates.

### Phase 40: Long-History Data And Integrity
**Status:** complete
Acquire reproducible 2017-2025 daily underlying histories for the benchmark,
Mag7, memory, and crypto groups, preserve source/currency metadata, and report
missing dates, short histories, corporate-action limitations, and proxy use.

### Phase 41: V5 Daily Economic Proxy
**Status:** complete
Implement a research-only daily proxy for V5 direction, risk quality,
breakout/continuation/systemic/residual signals, single-position ranking,
risk-sized entries, channel/ATR exits, and per-trade R attribution. Never label
the results as OKX perpetual execution.

### Phase 42: Walk-Forward And Frozen Validation
**Status:** complete
Run expanding and rolling 2018-2023 research folds, freeze all rules, then
evaluate 2024-2025 separately by year, side, model, asset group, payoff,
drawdown, concentration, and benchmark-relative return.

### Phase 43: Ablation And Stress Testing
**Status:** complete
Compare breakout-only versus continuation, long-only versus dual-side,
one versus three slots, exit variants, and BTC inclusion; apply 1.0x/1.5x/2.0x
cost assumptions, top-five removal, parameter perturbation, and Monte Carlo.

### Phase 44: Profit Bottleneck Verdict
**Status:** complete
Identify whether low profit comes from signal frequency, portfolio overlap,
selection quality, payoff asymmetry, risk caps, costs, or missing short edge,
then define the smallest evidence-backed next strategy change without starting
V5 or altering active V2.

### Phase 45: V5 Freeze And V6 Portfolio Contract
**Status:** complete
Capture V5 strategy/config/Compose hashes and define an isolated V6 long-only
portfolio contract with three slots, one-third per-trade risk, group caps,
correlation vetoes, memory execution quarantine, and unchanged execution
fail-closed controls.

### Phase 46: V6 Candidate And Risk Engine
**Status:** complete
Implement a stable ranked candidate set per completed base candle, enforce
long-only entries, exclude memory from execution, cap crypto and correlated
Mag7 exposure, and divide the existing model risk budgets by three without
raising leverage, margin, or account-level risk.

### Phase 47: V6 Isolated Runtime
**Status:** complete
Add independent V6 configuration, Compose identity, port, database, logs,
Risk Guard state, and frozen V5 manifest. Keep dry-run and every approval flag
false and do not start any V6 service.

### Phase 48: V6 Focused And Regression Tests
**Status:** complete
Cover multi-candidate stability, long-only behavior, memory quarantine, group
and correlation limits, risk scaling, minimum stake rejection, isolation, and
frozen V5 hashes, then rerun the complete V2-V6 deployment suite.

### Phase 49: V6 Economic Revalidation
**Status:** complete
Compare the V6 research contract against V5 on 2018-2023 and frozen 2024-2025,
including annual stability, top-five concentration, group attribution, cost
stress, and the existing 95-day OKX execution diagnostic where feasible.

### Phase 50: V6 Release Verdict
**Status:** complete
Document whether the portfolio redesign improves robustness without increasing
total risk. Keep V6 offline unless all existing economic and execution gates
are met; do not change active V2.

### Phase 51: Freeze V6 And Define V7 Risk Contract
**Status:** complete
Capture V6 strategy/config/Compose hashes and create an isolated V7 contract
for the 80 USDT account. Make capital use moderately more assertive through a
dynamic account-risk budget and minimum-contract feasibility, not through
unbounded leverage, full-margin deployment, or changes to V2-V6.

### Phase 52: Capital-Aware Candidate And Stake Engine
**Status:** complete
Exclude candidates whose minimum OKX contract cannot fit the remaining risk
budget before final ranking, allow a feasible runner-up, and allocate moderately
larger per-position risk while enforcing a hard aggregate open-plus-pending risk
ceiling, group caps, correlation vetoes, cash reserve, and margin limits.

### Phase 53: V7 Isolated Runtime And Tests
**Status:** complete
Add independent V7 strategy/config/Compose/database/log/state identities and
tests for risk-ledger accounting, capital-feasibility fallback, BTC minimum
contracts, concurrent-risk ceilings, exact boundary behavior, and frozen V6
hashes. Keep all approval flags false and start no service.

### Phase 54: V7 Economic Revalidation
**Status:** complete
Compare V7 with V6 on the unchanged 2018-2023 research and frozen 2024-2025
daily proxy, then run the isolated 80 USDT real-OKX diagnostic when feasible.
Report return, drawdown, concentration, costs, capital utilization, and BTC
executability without using the 2026 interval to tune parameters.

### Phase 55: V7 Release Verdict
**Status:** complete
Complete regression, configuration, Compose, compilation, and diff checks.
Document whether the modestly bolder allocation improves executable opportunity
without violating the account-risk contract. Keep V7 offline unless all
economic and forward gates pass; do not change active V2.

### Phase 56: V7 Audit Scope And Evidence Map
**Status:** complete
Audit V7 without changing it. Trace candidate generation, pre-ranking capital
checks, callback ordering, pending/open risk accounting, exchange minimums,
portfolio admission, exits, and research-proxy assumptions against current
official Freqtrade and OKX behavior.

### Phase 57: Adversarial Code And Simulation Review
**Status:** complete
Search for race conditions, stale caches, false fail-open/fail-closed behavior,
contract-size errors, risk-budget double counting, selection/fill divergence,
portfolio correlation gaps, and backtest/live mismatches. Reproduce material
issues with bounded read-only tests or calculations where possible.

### Phase 58: Economic Robustness Gap Analysis
**Status:** complete
Separate signal edge from risk scaling; examine BTC dependence, continuation
quality, regime concentration, minimum-contract feasibility, cost/funding
coverage, annual stability, capacity, and the absence of a real V7 OKX
execution backtest.

### Phase 59: Prioritized V8 Recommendations
**Status:** complete
Rank overlooked improvements by severity, expected benefit, implementation
risk, and evidence needed. Do not tune against the 2026 diagnostic, modify V7,
start a service, or enable any approval flag.

## Decision Rules
- Do not tune signal parameters or alter entry/exit rules.
- Keep live and dry-run runtime checks fail closed.
- Preserve the exact stake-sizing stop distance across candle transitions.
- Remote deployment is authorized only with `dry_run=true`; do not enable live trading or validation approvals.
- V3 must run independently from V2 and must not reuse V2 databases, ports,
  logs, Risk Guard state, or release evidence.
- V3 Stage 1 uses one open trade, 0.75% planned risk, a 5 USDT margin cap,
  2x stock/ETF leverage, and 3x crypto leverage.
- OKX contract data drives execution validation; underlying equity history is
  research-only and must never be presented as OKX fill history.
- Missing beta inputs, stale data, closed cash sessions, or incomplete
  cross-sectional ranking block new entries without blocking exits.
- Do not use the 2026 execution interval to tune profitability thresholds.
  Engineering corrections may be validated on it, but economic changes require
  pre-2026 underlying research and a newly frozen forward interval.
- Preserve the 15 USDT cash reserve based on raw account equity exactly once;
  `tradable_balance_ratio` must not silently apply a second reserve.
- V3 remains research-only after this optimization because the executable 2026
  result fails return, PF, Sharpe, direction-diversity, and sample-size gates.
- Freeze V3 while implementing V4. Do not modify its strategy, configuration,
  database, Compose service identity, or recorded result after the V4 baseline
  hash is captured.
- V4 remains offline-only until the economic gates pass. Creating isolated
  configuration and Compose files does not authorize starting either V4
  service.
- A V3/V4 comparison is descriptive only unless both strategies have enough
  closed trades. Do not interpret a one- or low-double-digit sample as proof of
  durable improvement.
- Do not increase leverage, risk budget, or simultaneous positions merely to
  enlarge backtest profit. First distinguish capital scaling from signal edge
  and measure portfolio overlap/correlation.
- Never commit populated `.env` files, credentials, runtime databases, logs, caches, or backtest bulk artifacts.
- Push a reviewable `codex/` branch instead of writing directly to upstream `develop`.
- Never print, log, commit, or persist supplied credentials outside ignored/protected `.env` files.

## Errors Encountered
| Error | Resolution |
|---|---|
| None in this review yet | N/A |
| SCP of the 2026-07-20 result timed out | Parse the ZIP on the server and return only aggregate JSON. |
| PowerShell continuation characters broke the wrapped code-inspection command | Reissue as simple single-line PowerShell statements. |
| Range helper failed on PowerShell array typing for the second code view | Use direct indexed loops without a generic range helper. |
| Remote parameter-grid aggregation exceeded the SSH wrapper timeout | Transfer the small CSV and analyze locally instead of repeating remote parsing. |
| Completion checker reported 4/0 because the plan omitted recognized phase headings | Reformatted phase entries to the checker's supported structure. |
| Session catch-up via `python` failed because the command is unavailable | Re-ran it successfully with the Windows `py` launcher. |
| `py -m ruff` failed because Ruff is not installed in the active Python 3.11 environment | Keep syntax/diff checks, perform manual line-length review, and validate against the available real runtime or container. |
| Freqtrade config validation initially failed without API credentials | Re-ran with non-secret temporary test values matching `.env.example`; schema validation and strategy discovery passed. |
| Strategy discovery initially reported duplicate names | Removed the redundant explicit strategy path; both strategies then reported `OK`. |
| `commit-hygiene` referenced a `base.md` that is absent from the installed skill tree | Used the complete available `SKILL.md` instructions and continued with conservative commit checks. |
| Final container revalidation could not connect because Docker Desktop is not running | Retained the successful Freqtrade 2026.6 container validation from 2026-07-23 and relied on the fresh 23-test/compile pass for this commit. |
| The first HTML asset-reference regex was mangled by PowerShell quoting | Switched to literal UTF-8 `Select-String` checks for the known local asset paths. |
| Deployment `runtime/config.json` was skipped by the repository-wide `config*.json` ignore rule | Verified it contains no credentials and remains `dry_run=true`, then force-added only that reviewed file. |
| Deployment documentation used Markdown trailing spaces for hard line breaks | Replaced them with explicit `<br>` tags so rendering is preserved and `git diff --check` passes. |
| Vendored Mermaid bundle contained upstream trailing whitespace | Removed only line-ending whitespace and re-ran the staged diff check successfully. |
| Installed `gh` rejected `--remote` when an explicit repository argument was supplied | Re-ran fork creation in current-repository inference mode; `Alexwhitee/freqtrade` was created successfully. |
| Initial SSH push timed out after 124 seconds and the GitHub API confirmed no branch was created | Switched the fork remote to HTTPS and configured Git to use the existing authenticated `gh` credential flow. |
| HTTPS push was rejected for missing OAuth `workflow` scope | Identified the real cause as the depth-1 local base (`d8fe5cf`) lagging the fork base (`e5fd2fec`); rebase the five local commits onto the fork's actual `develop` so existing workflows are not part of the update. |
| PowerShell expanded the remote backup timestamp expression locally | The old candidate was still copied safely as `candidate-`; rename that verified directory to a fixed timestamped backup before deployment.
| Inline OKX credential validator failed from nested PowerShell/SSH quoting before Python ran | Upload a temporary credential-free validator script, run it in the Freqtrade image with `--env-file`, then remove it. |
| Supplied full-width-password value caused `UnicodeEncodeError` as an OKX API passphrase, and the old passphrase was not the ASCII punctuation variant | Treat the supplied password as the subaccount login password, preserve the previously validated API passphrase for the matching API key/secret, and re-run private authentication. |
| Matching key/secret with the existing remote API passphrase returned OKX `AuthenticationError` | Test the normalized ASCII punctuation variant once; if it fails, stop before deployment and request the dedicated API passphrase. |
| Attempted ASCII passphrase rewrite made no change | Confirmed the existing remote passphrase was already the normalized ASCII value; diagnose OKX error code and outbound IP instead of retrying credentials. |
| OKX returned `50111 Invalid OK-ACCESS-KEY` in both live and sandbox validation | Keep the existing deployment running, remove candidate credential copies, and require a newly created valid API key/secret/API passphrase before deployment can continue. |
| First candidate-secret cleanup command was expanded by local PowerShell before reaching SSH | Reissued cleanup with two explicit verified absolute file paths and no variables; both candidate secret files were removed. |
| Latest optimization-session catch-up first used unavailable `python` | Re-ran the skill recovery script successfully with the installed Windows `py -3` launcher. |
| Initial V4 symbol search targeted the repository-root `user_data/strategies` path | Located the actual deployment layout under `deployments/okx-aggressive/runtime/strategies` and continued there. |
| The first full V5 research rerun used a 1-second shell timeout and was terminated before producing a result | Re-ran with a sufficient timeout, then used a hidden background process and bounded polling for subsequent long runs. |
| The first equal-risk ablation patch matched the wrong insertion context | Read the exact current sections and applied the same scoped change against stable surrounding lines. |
| A V6 source search used an unclosed grouped regular expression | Re-ran the read-only search with separate `rg -e` expressions. |
| The first V6 focused run passed 7/9 tests; a shared DataFrame fixture mutated the prior assertion and floating stake arithmetic fell just below an exact 1.0 minimum | Return a fresh frame per mocked call and round stake to execution precision before enforcing the minimum. |
| The second V6 focused run expected a 0.75 USDT continuation stake while passing a 1.0 USDT exchange minimum | Use a 0.1 minimum only in the risk-ratio test and retain a separate 1.01 minimum rejection test. |
| The first combined V6 verification hid successful parallel results when local Docker Desktop was unavailable | Record Docker as unavailable and rerun tests, compilation, JSON, and Compose parsing independently of daemon-backed validation. |
| The first PowerShell parser for V6 robustness tables ended a `foreach` expression directly into a pipe | Assign the generated rows to a variable before formatting the read-only report. |
| The first isolated V6 strategy discovery scanned the default strategy directory twice and reported duplicate names | Remove the redundant `--strategy-path`; the uploaded V6 module itself loaded successfully. |
| The first real-OKX V6 diagnostic exported zero entry signals while V5 with the same V6 whitelist exported AAPL and AMZN longs | Isolate Freqtrade's inherited futures capability flag with a single-variable A/B; keep long-only enforced at signal, selection, confirmation, and sizing layers. |
| Enabling inherited short capability did not change V6's zero accepted-signal export | Restore `can_short=false`; test the user's new 80 USDT wallet because one-third risk sizing at 30 USDT falls below equity-contract minimum amounts. |
| Adding symbol attribution exposed two synthetic annual-summary trades without a `symbol` field | Add the realistic QQQ/BTC identities to the test fixture; production trades already persist symbols. |
| First focused V5 run had two fixture/interface failures: missing informative suffix and missing pair identity in the stake candle | Read the inherited V3 interfaces, use the merged `_4h` column names, and include the pair so existing asset-specific stop validation can run. |
| First isolated V5 backtest saw no history because the copied comparison directory did not contain the externally mounted public dataset | Copied the existing credential-free 20 MB V4 validation data into the new isolated directory and reran successfully. |
| Freqtrade 2026.6 `backtesting-analysis --rejected-signals` raised `KeyError: close_date` on its own rejected-signal frame | Add a read-only pickle inspector that summarizes signal frames without applying closed-trade assumptions. |
| The first custom signal-inspector invocation used the image's Freqtrade entrypoint and then assumed a raw pickle stream | Override the entrypoint with Python and reuse Freqtrade's actual `joblib.load(BytesIO(...))` decoding path. |
| Session catch-up failed via `python` and then used the wrong `.codex` skill path | Re-ran successfully with `py -3` and the actual `.agents` skill path. |
| Approval-flag patch expected unquoted values | Read the exact UTF-8 lines and reissued the patch with the existing quotes. |
| Network-disabled backtest could not load OKX public market metadata | Re-run the read-only backtest with network access and temporary non-secret credentials; no private API or order command is used. |
| One full local pytest run hit a transient Windows `WinError 5` during `os.replace` in a temp state file | The targeted test and immediate full rerun both passed; record as an environment-only flake and retain the passing rerun as the final result. |
| Final remote Python one-liner was expanded by PowerShell quoting | Replaced it with fixed read-only `grep`/`test` checks; the dry-run flags and cleanup state were confirmed. |
| Remote dependency probe was expanded by nested PowerShell/SSH quoting | Keep the confirmed local dependency result and use credential-free container validation after implementation. |
| The first V3 compile/test command used unavailable `python` | Load the Codex workspace dependencies and use its explicit bundled Python executable. |
| Bundled workspace Python lacked `requests`, so Risk Guard tests could not import | Use the repository's established `py -3` runtime, which has the deployment test dependencies. |
| Legacy permanent-lock test had no whitelist after removing the hard-coded BTC pair | Make the test API expose its configured whitelist and add coverage for whitelist plus pre-force-exit open pairs. |
| Docker Desktop is not running, so local image-based strategy discovery is unavailable | Keep local Compose validation and use an isolated validation directory on the existing CloudCone Docker host without changing active V2 services. |
| First isolated Freqtrade discovery passed config schema but V3 dataclasses failed under the resolver's delayed-annotation module loading | Remove the unnecessary `from __future__ import annotations` from V3 and repeat discovery with a single strategy lookup path. |
| First execution backtest was rejected because `startup_candle_count=1500` exceeds Freqtrade's OKX limit of 1499 | Use the already sufficient V2-proven 1250-candle warm-up and include 1000/1125/1250 in recursive analysis. |
| Pandas 3/Freqtrade backtest rejected `merge_asof` between millisecond and microsecond UTC timestamps | Normalize both base and daily availability timestamps to explicit `datetime64[ns, UTC]` before backward alignment. |
| Freqtrade 2026.6 full Lookahead forces `stake_amount=10000`, above META's OKX leverage-tier maximum | Record the upstream-tool incompatibility; rely on the completed indicator-only lookahead/recursive analysis and do not weaken V3 risk limits to accommodate the analyzer. |
| `py -3 -m pyflakes` was unavailable in the established local Python | Retain compileall, 42 behavior tests, Freqtrade strategy discovery/backtest, diff checks, and manual five-axis review without adding a project dependency. |
| Optimization-session catch-up first used unavailable `python`, then the wrong `.codex` skill path | Loaded the bundled workspace Python and ran the script from the actual `.agents` skill path. |
| Initial remote inspection commands lost quoted `find`/Docker-template/`grep -E` arguments through PowerShell-to-SSH parsing | Use simple fixed remote commands or PowerShell outer double quotes with inner POSIX single quotes; no service or strategy state was changed. |
| First optimized validation mount used a host symlink whose target was outside the container mount; the next discovery used a dummy JWT below schema minimum length | Replaced the symlink with a dedicated nested data bind mount and use a credential-free 32+ character validation-only JWT value. |
| Trade-indicator analysis could not read signals because the comparable run exported trades only | Repeat the same isolated backtest once with `--export signals`, then analyze entry/exit indicators without changing strategy parameters. |
| Final full suite hit the known transient Windows `WinError 5` while atomically replacing a temporary Risk Guard state file | Run the affected test alone, then repeat the full suite; do not change production persistence logic for an environment-only ACL race. |
| First V4 focused run had 3 failures: identical-return residual drift, exact-3R floating comparison, and an ATR-multiplier fixture mismatch | Stabilize identical-return residuals, use numerical tolerance at R boundaries, and make the test candle represent the intended 2.5% post-multiplier stop. |
| V4 exit-policy selector called `max()` on a policy with no fold reports | Represent missing folds as infinite drawdown and keep the policy explicitly ineligible. |
| Isolated Freqtrade discovery failed before strategy loading because a read-only user-data mount cannot create `backtest_results` | Re-run with a writable dedicated validation directory; do not mount or change the active deployment. |
| First V4 diagnostic backtest mounted an empty/incorrect read-only `data` root and Freqtrade tried to create `data/okx` | Locate the existing public OKX data directory and pass it explicitly with `--datadir`; do not copy or mutate active data. |
| Explicit read-only OKX datadir reached exchange initialization but Freqtrade writes `leverage_tiers_USDT.json` into that directory | Copy public data into the dedicated V4 validation directory and let only the isolated copy receive cache writes. |
| First indicator-level V4 backtest found an empty crypto frame accessed `momentum20` before the missing-data guard | Return an explicit empty residual column before model-specific residual logic and add regression coverage; keep the asset fail-closed. |
| V4 indicator-only analysis flagged funding and all funding-derived candidate scores as lookahead | Delay live/dry-run funding availability by one complete hour; in research, exclude the manually fetched unsliced funding frame from candidate scoring, retain engine-level funding costs, and label results as incomplete-cost. |
| The first delayed-funding container run hit a Pandas timestamp precision `MergeError` | Normalize both base and funding timestamps to explicit nanosecond UTC before the live/dry-run merge. |
| A quoted remote `grep -E` expression was split by the Windows SSH wrapper and left the local SSH client waiting | Stop only the verified matching local SSH process and rerun the analyzer directly without a remote output pipeline. |
| Comparison data inventory rejected `/dev/null` as an invalid config | Re-run `list-data` with the uploaded credential-free V4 config and validation-only API-server environment values. |
| Freqtrade 2026.6 rejected the comparison-only `--backtest-notes` option | Remove the unsupported metadata flag, keep the frozen command parameters in `progress.md`, and encode the comparison identity in deterministic output filenames. |
| PowerShell removed embedded Python string quotes from the inline ZIP comparison command | Add a small reusable `compare_beta_backtests.py` parser with normal CLI arguments instead of relying on nested shell quoting. |
| Both direct pickle and pandas rejected Freqtrade's exported signal payload after a valid pickle header | Stop this nonessential branch after two distinct readers; completed trade archives, rejected-signal totals, and exact trade overlap already answer the comparison without altering runtime dependencies. |
| The first V7 focused run passed 2/6 tests because the test process restored an incomplete local Freqtrade package before the runtime method imported persistence | Extract open-trade retrieval behind an injectable method; production still reads `Trade.get_open_trades()` while focused tests supply deterministic positions. |
| Two read-only SSH probes of the prior isolated V6 validation directory returned no output | Do not mutate or restart any remote service; complete the V7 economic comparison with the frozen daily proxy and retain the existing exact V6 OKX result as the execution warning. |
| Initial archive-task plan append used a truncated line from terminal output | Read the exact UTF-8 file tail and append against a stable complete line. |
| Read-only remote config `grep` had a trailing backslash after nested PowerShell/SSH quoting | Use the successful container command inspection as the authoritative strategy evidence; do not repeat the unnecessary failing probe. |

### Phase 60: V1-V7 Archive Inventory
**Status:** complete
Map every V1-V7 strategy, configuration, Compose file, test, document, tool,
manifest, and import/reference before moving anything.

### Phase 61: Archive Historical Runtime Entrypoints
**Status:** complete
Move obsolete V1-V6 runnable artifacts into one clearly documented archive
tree while preserving every source module still required by V7.

### Phase 62: V1-V7 Strategy Guide
**Status:** complete
Write a detailed Chinese Markdown guide covering architecture, signals, risk,
runtime status, version deltas, dependencies, and safe operational usage.

### Phase 63: Post-Archive Validation
**Status:** complete
Validate references, Python/JSON/YAML syntax, focused V7 loading/tests, and the
final Git diff without modifying the active remote service.
