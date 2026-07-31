# Progress

## 2026-07-23
- Initialized optimization review without changing strategy or remote state.
- Carried forward verified results from the 2026-07-21 remote audit and backtest.
- Confirmed current remote health, zero dry-run trades, recurring WebSocket errors, deployed hash, and available backtest lineage.
- Completed phase 1; began trade-level robustness analysis.
- Result ZIP download timed out; switched to server-side standard-library parsing.
- Parsed the full 35-trade result and quantified regime, side, exit, timing, and profit-concentration risk.
- Logged a PowerShell command-wrapping error before code inspection; no files or services changed.
- Reviewed base strategy risk sizing, leverage, entry checks, stop behavior, and test inventory; identified a sizing/stop persistence risk and major coverage gaps.
- Reviewed exact deployed V2 sizing/stop/live gate and Risk Guard promotion, recovery, pause, and equity logic.
- Confirmed fee stress, zero funding costs, leverage concentration, stake distribution, and 65-trial parameter grid availability.
- Remote grid aggregation timed out; switched to local parsing of the small CSV.
- Completed parameter-grid robustness review and finalized the recommendation: preserve defaults, fix evidence/execution gaps first.
- Identified that the 10-trade-in-14/30-days release protocol is incompatible with the strategy's roughly 6.5 trades/year frequency.
- Reformatted the completed plan so the skill completion checker can verify all four phases.
- Started implementation hardening from the review findings; confirmed V2 is the configured deployment strategy and dry-run remains enabled.
- Selected a scoped fix: persist the stake-sizing stop distance through the fill lifecycle and add focused runtime-gate tests without tuning strategy parameters.
- Implemented V2 entry risk plans, entry-rate validation, fill-time persistence, and a 2.5% fail-safe for missing plans.
- Added focused tests for risk persistence, exit-order isolation, leverage caps, live approval, funding veto, stale risk state, stale candles, spread rejection, and changed entry rates.
- Focused V2 test run passed all 13 tests in 0.241 seconds.
- Full deployment suite passed all 22 tests in 0.227 seconds; compileall and whitespace checks also passed.
- Ruff was unavailable in the active Python environment, so static review continued with direct source inspection and runtime import checks.
- Code review caught and fixed a later-entry-fill overwrite edge case; added a regression test and cache cleanup.
- Final deployment suite passed all 23 tests in 0.424 seconds; compileall, line-length, and whitespace checks passed.
- Freqtrade 2026.6 container imported V2 successfully and reported both V1 and V2 strategy status as `OK` after supplying temporary API validation values.
- Completed all implementation and validation phases without changing signal parameters or enabling live trading.

## 2026-07-26
- Started the requested commit-and-push workflow.
- Confirmed the repository is on `develop` tracking the official `freqtrade/freqtrade` origin and has 29 untracked files totaling about 4.1 MB.
- Confirmed populated `.env` paths are ignored; began a full content and sensitive-data audit before staging.
- Confirmed the authenticated GitHub account has read-only access to `freqtrade/freqtrade`; a personal fork is required for push.
- Excluded the generated `.trae-html-share-packages` ZIP cache; existing rules already exclude Python bytecode and populated `.env` files.
- Created branch `codex/okx-strategy-hardening`.
- Fresh deployment verification passed all 23 tests in 0.149 seconds; compileall and whitespace checks passed.
- Docker Desktop was not running for repeat container validation; the previously successful Freqtrade 2026.6 strategy discovery remains documented.
- Completed the full commit audit: no real credentials found, generated caches excluded, and all offline guide asset references resolve locally.
- Code review verdict: approved for commit; no correctness, security, architecture, or performance blockers remain.
- Created commit `6b960bd` for the OKX V1/V2 strategies and focused V2 tests.
- Created commit `604701a` for the risk guard, safe deployment configuration, and operational tools.
- Created commit `c4b93ed` for the OKX deployment documentation.
- Created commit `3bbe0b3` for the offline Freqtrade futures guide and generated-package ignore rule.
- Completed the atomic commit phase; preparing a personal fork because upstream origin is read-only.
- Created GitHub fork `Alexwhitee/freqtrade` and added it as the `fork` remote.
- SSH push timed out without creating the branch; prepared an authenticated HTTPS retry instead of repeating the stalled transport.
- HTTPS reached GitHub but was rejected because the shallow old base made existing workflows appear changed; selected a clean rebase onto the fork's current `develop` before the final push attempt.
- Fetched fork `develop` at `e5fd2fec`, rebased all five commits without conflicts, and confirmed the branch diff contains only the intended 21 files.
- Re-ran all 23 deployment tests after rebasing; all passed in 0.068 seconds.
- Successfully pushed `codex/okx-strategy-hardening` to `Alexwhitee/freqtrade` and configured the local branch to track it.

## 2026-07-26 Deployment
- Started the authorized CloudCone dry-run deployment using supplied OKX credentials.
- Confirmed SSH access and found the existing `freqtrade` and `freqtrade-risk-guard` containers healthy after five days under `/root/freqtrade`.
- Selected a preflight-first replacement flow that validates credentials before stopping the old deployment and keeps all live approval gates disabled.
- Remote preflight passed: `dry_run=true`, zero total/open trades, protected `.env` mode `600`, all live gates false, and 21 GB free disk.
- Copied the protected remote `.env` into the ignored local deployment path; supplied API key/secret matched the existing values, and only the differing OKX passphrase was replaced.
- Uploaded the reviewed candidate, set candidate `.env` mode to `600`, and passed `docker compose config --quiet` plus hash verification.
- Correctly separated the supplied subaccount login password from the OKX API passphrase after the full-width value failed header encoding; selected the existing validated passphrase for the matching key/secret.
- Existing remote passphrase now fails OKX private authentication, consistent with the runtime API-failure state; old containers remain untouched while one normalized passphrase candidate is tested.
- Confirmed server egress IP is exactly the whitelisted `66.154.127.2` and NTP is synchronized.
- OKX rejected the key with code `50111` in both live and sandbox modes, proving the key itself is invalid or revoked rather than an IP/time/passphrase formatting issue.
- Aborted before stopping containers; the existing healthy dry-run deployment remains running while valid replacement API credentials are required.
- Removed the candidate `.env` and temporary validator from the server; active `/root/freqtrade/.env` remains mode `600` and untouched.
- Restricted the ignored local `.env` ACL to the current Windows account, SYSTEM, and Administrators only.
- Verified active V2 hash remains the old `e3ae0e5b...`, candidate V2 is the new `d6180d80...`, and the active Freqtrade container is still healthy.

## 2026-07-27 Validation And Backtest Request
- Validated the populated local `.env` without exposing secrets: syntax, required values, API credential pairing, Git ignore, and ACL checks passed.
- Confirmed OKX private authentication from CloudCone with a read-only balance call.
- Confirmed the local Freqtrade API credentials authenticate against the active remote service.
- Confirmed Compose validation passes and the strategy configuration remains `dry_run=true`.
- Found all three Risk Guard approval flags set true locally; deployment will force them false.
- Recovered the existing file-based plan after correcting the Python launcher and skill path.
- Began a fresh backtest of the current hardened V2 candidate because prior return metrics belong to a different deployed hash.
- First isolated backtest attempt stopped before strategy execution because Freqtrade requires OKX public market metadata and the container had no DNS/network; no private credentials or orders were used.
- Re-ran the full hardened V2 backtest with public market metadata access and temporary dummy credentials; it completed with 35 trades, +32.78%, PF 2.08, Sharpe 0.09, and 9.57% drawdown.
- Ran the 2025 holdout; it produced 9 trades, -0.73%, PF 0.83, and Sharpe -0.04.
- Rejected live deployment on evidence quality; next permitted path is dry-run operational soak with all approvals false.
- Fixed `install_remote.py` so candidate `.env` values take precedence over stale active credentials; added a regression test.
- Deployment suite passed all 24 tests after the installer fix; compileall and diff checks passed.
- Uploaded the candidate and valid credentials, confirmed zero open trades, installed the new candidate in dry-run, and preserved a timestamped backup.
- Operational verification passed: both services started, Freqtrade healthy, internal API HTTP 200, Risk Guard fresh with zero API failures, zero trades, all live gates false.
- Removed the temporary candidate `.env` secret copy; active remote `.env` remains mode `600`.

## 2026-07-27 Cross-Asset Beta V3
- Accepted the detailed V3 implementation plan while preserving the active V2 dry-run.
- Recovered the file-based plan and confirmed a clean working tree on
  `codex/okx-strategy-hardening`.
- Confirmed no existing exchange-calendar dependency is available locally;
  selected a standard-library session gate with explicit holiday data.
- Began Phase 13: V3 architecture, interfaces, independent runtime, and
  backward-compatible Risk Guard design.
- Confirmed the exact V2 extension points and the Risk Guard hard-coded BTC
  lock; implementation will preserve V2 behavior and add backward-compatible
  cross-asset state fields.
- Added the first V3 implementation, reviewed market-calendar data, independent
  dry-run config/Compose stack, and backward-compatible Risk Guard beta fields.
- The initial test command found no `python` on PATH; loaded the bundled Codex
  Python runtime for deterministic local validation.
- Implemented V3 completed-daily Beta scoring, regime boundaries, 4h signals,
  cross-sectional single-winner selection, US/KR entry sessions, asset-specific
  stops/leverage, fixed Stage-1 stake sizing, and beta-state publication.
- Added independent V3 Compose/config resources and made permanent Risk Guard
  locking cover every configured and open market.
- Added research data contracts, KRW-to-USD panel preparation, frozen release
  gate evaluation, and Chinese operating/validation documentation.
- Local validation currently passes 40 tests, Python compilation, JSON parsing,
  Compose rendering, and `git diff --check`.
- Isolated Freqtrade 2026.6 validation confirmed the V3 config schema, then
  exposed a resolver/dataclass incompatibility with delayed annotations; the
  annotation mode was removed before the required discovery rerun.
- Public OKX downloads completed for all 15 execution/benchmark pairs at
  15m/1h/4h/1d plus mark/funding data. The first backtest preflight found the
  1500-candle exchange limit; V3 now uses the sufficient 1250-candle warm-up.
- The next real backtest reached indicator calculation and exposed Pandas 3
  timestamp-unit strictness; daily/base timestamps are now normalized to
  nanosecond UTC before `merge_asof`.
- The corrected real OKX execution backtest completed over 2026-04-22 through
  2026-07-27 with zero trades, so all performance/sample gates remain unmet.
- Recursive analysis passed at 1000/1125/1250 candles with no recursive
  variance and no indicator-only lookahead bias.
- Full Lookahead remains blocked by Freqtrade 2026.6 forcing a 10,000-unit
  stake that exceeds META's OKX tier limit; production risk caps were retained.
- Completed the five-axis code/security review and fixed all required findings.
- Final local suite passes 42 tests, compilation, JSON, Compose, and diff
  checks; isolated Freqtrade discovers V3 as OK and active V2 remains healthy.
- Split the implementation into Risk Guard, V3 runtime, research tooling, and
  documentation commits for reviewable history.

## 2026-07-27 V3 Deep Optimization
- Reproduced the zero-trade result at the signal, regime, session, and sizing
  layers using the real downloaded OKX dataset.
- Proved that entry signals exist and isolated the double-reserve interaction
  between Freqtrade's `tradable_balance_ratio` and V3's fixed cash reserve.
- Ran a temporary ratio-only A/B backtest: 13 trades appeared, but the result
  remained negative and failed PF/Sharpe/sample gates.
- Confirmed a shared exit-tag overwrite on three AAPL long exits and a complete
  lack of short-regime overlap.
- Began Phase 20 implementation planning; active V2 and all live approvals
  remain untouched.
- Session recovery initially failed due to unavailable/wrong Python paths, then
  succeeded with the bundled Python and the installed `.agents` skill path.
- Implemented raw-equity Stage-1 risk sizing while retaining Freqtrade's
  ratio-adjusted `max_stake` as the execution-capacity ceiling.
- A 30 USDT wallet at `tradable_balance_ratio=0.50` now produces a 2.8125 USDT
  QQQ margin stake for the tested 4% stop and 2x leverage, while a 15 USDT raw
  wallet remains blocked by the cash reserve.
- Entry signals now consume the categorical beta regime, eliminating the score
  40 neutral/short mismatch between signal generation and stake validation.
- Simultaneous long/short exit masks retain both exit flags and receive the
  truthful `dual_channel_exit` tag instead of being mislabeled as a short exit.
- The focused V3 suite passes 16 tests after the first optimization patch.
- Added fallback coverage for ratio-adjusted wallet objects and protected the
  reviewed 0.50 capacity ratio in the independent V3 configuration test.
- The complete deployment suite passes 45 tests; runtime/tools/tests compile,
  all runtime JSON files parse, and `git diff --check` passes.
- Created `/root/freqtrade-v3-optimized-20260727` as a new isolated validation
  directory, copied only credential-free config/calendar/base strategies,
  uploaded the optimized V3, and linked the existing public OKX data read-only
  by convention. The active V2 deployment was not touched.
- Logged two PowerShell-to-SSH quoting failures during read-only inspection;
  neither command changed strategy or service state.
- The first isolated discovery exposed a Docker bind-mount boundary for the
  shared data symlink; replaced it with an explicit nested data bind mount.
- The next discovery reached schema validation and rejected only the short
  dummy JWT. No real credentials were used or required.
- Discovery then loaded the same default strategy directory twice because an
  unnecessary explicit strategy path was supplied; repeat without that option
  to obtain the real resolver status.
- Corrected isolated discovery reports V1, V2, and V3 as `OK`.
- Re-ran the exact comparable real-OKX execution backtest. The production
  configuration now executes 13 trades without an environment override, but
  still returns -1.66%, PF 0.24, Sharpe -1.09, and 1.66% drawdown.
- Confirmed the three previously mislabeled exits now report
  `dual_channel_exit`; all trades remain long and four full stops still account
  for the loss.
- Freqtrade's trade-indicator analyzer requires a signals pickle, which the
  comparable trades-only export does not contain; scheduled one identical
  signals-export run for structural trade diagnosis.
- Exported and analyzed entry/exit indicators for all 13 trades. The two BTC
  stops occurred on consecutive days, and NVDA's stop occurred after the fresh
  Beta regime had deteriorated to neutral.
- Added a one-loss, 48-hour, per-pair StoplossGuard while retaining the existing
  global two-loss guard.
- Added fresh-regime exits for longs leaving risk-on and shorts leaving
  risk-off; incomplete/blocked data does not itself force an exit.
- Replaced direction-sensitive shared exit tags with side-agnostic channel,
  beta-regime, and combined reason tags.
- Focused V3 tests pass 19/19 and the complete deployment suite passes 47/47;
  compilation and whitespace checks pass.
- Real-OKX revalidation of the structural-exit experiment produced 16 trades,
  -2.15%, PF 0.29, Sharpe -1.35, and 2.49% drawdown.
- The regime exit increased portfolio turnover and exposed the account to
  additional losing selections; it will be reverted rather than retained from
  a single unfavorable experiment.
- Reverted the unhelpful regime-exit and pair-quarantine experiment. The final
  candidate retains only the proven capital, entry-regime, and truthful
  side-agnostic exit-tag corrections.
- The first final full-suite run hit the previously observed transient Windows
  temp-file `WinError 5`; production code was not changed and the prescribed
  isolated-then-full rerun is pending.
- The isolated Risk Guard test and immediate full rerun passed, confirming the
  temporary ACL race was environmental.
- Final real-OKX backtest returned the stable comparable result: 13 trades,
  -1.66%, PF 0.24, Sharpe -1.09, and 1.66% drawdown. Exit reporting now uses the
  truthful side-agnostic `channel_exit`.
- Updated the Chinese V3 guide with the corrected capital semantics, final
  execution result, failed structural experiment, and unchanged release block.
- Final verification passes 17 focused V3 tests, 45 complete deployment tests,
  compilation, JSON/config checks, and whitespace checks.
- Verified active V2 remains healthy in dry-run with Risk Guard running and no
  V3 container present. Completed Phases 22 and 23 without deployment or live
  approval changes.

## 2026-07-27 V3 Negative-Return Research
- Started a deep-research quick brief on the final 13-trade result, with local
  trade attribution first and verified academic/official evidence second.
- Added Phases 24-27 for trade attribution, mechanics diagnosis, mature-method
  evidence review, and a non-overfit optimization roadmap.
- Completed pair, side, exit-reason, MFE/MAE, funding, fee, leverage, regime,
  and candidate-score attribution for the stable final 13-trade archive.
- Confirmed all trades were long because no technically complete short aligned
  with the narrow global risk-off state; this was not an order-rejection bug.
- Confirmed BTC caused 87.97% of net loss, while removing BTC still leaves a
  negative result and PF 0.729.
- Identified payoff asymmetry as the primary economic failure: four full stops
  lost 0.652913 USDT while five trailing winners earned only 0.026262 USDT.
- Reviewed primary research on time-series and cross-sectional momentum,
  residual momentum, volatility management, carry, trend diversification, and
  momentum crash control.
- Completed a prioritized research roadmap that separates direction from risk,
  restores trend payoff convexity, isolates crypto risk, makes short design
  explicit, and freezes a new forward interval.
- No strategy, deployment, credential, live-approval, or cloud runtime state
  was changed during this diagnostic phase. Completed Phases 24-27.

## 2026-07-27 V4 Implementation
- Started Phases 28-32 from the approved V4 plan.
- Captured the V3 strategy/config hashes and the stable 13-trade economic
  baseline before creating V4 artifacts.
- Confirmed the current branch and preserved all pre-existing V3, guide, test,
  and planning changes.
- V4 implementation is local/offline only; no cloud container or live approval
  change is authorized.
- Added the independent `OkxCrossAssetBetaV4` strategy module with centered
  direction, volatility-only risk quality, residual momentum, regime
  hysteresis, dual short models, unified candidate selection, model-specific
  risk budgets, and three monotonic exit policies.
- The new V4 module passes Python compilation. Configuration, research tooling,
  behavior tests, and runtime resolver validation remain pending.
- Added independent V4 dry-run configuration, Compose service identities,
  port 8083, database/log/state paths, and a machine-readable V3 baseline
  manifest. All three approval flags are explicitly false.
- Completed Phase 28. No V4 service was started; Phase 29 behavior validation
  is in progress.
- First focused V4 run passed 11/14 tests. Three deterministic numerical/test
  fixture failures were recorded for targeted correction; no production
  parameter was relaxed.
- Corrected the numerical boundaries and re-ran V4 plus frozen V3 tests:
  31/31 pass.
- Completed Phase 29. Phase 30 now validates persisted risk/excursion state and
  exit-policy behavior beyond the focused unit cases.
- Added V4 trade attribution, exit-policy selection, and release-gate tools.
  The first research-tool run passed 6/7 tests and exposed one missing-fold
  fail-closed edge, which is being corrected explicitly.
- The first complete deployment run passed 67/68 tests; the remaining failure
  is the same empty-policy branch reaching a second empty median calculation.
- Corrected the missing-fold median condition. Research tools pass 7/7 and the
  complete deployment suite passes 68/68.
- Completed Phase 30. Phase 31 now adds operator documentation and validates
  configuration/Compose contracts without starting V4.
- Local JSON, Compose, compile, frozen-hash, and whitespace checks pass.
- Uploaded only credential-free validation artifacts to a new CloudCone
  directory. The first resolver attempt stopped before strategy loading
  because Freqtrade requires a writable user-data cache directory.
- Re-ran against the writable isolated directory. Freqtrade 2026.6 config
  validation passed and strategy discovery reports V4 `OK`.
- Confirmed the active V2 Freqtrade and Risk Guard containers remain running;
  no V3 or V4 service was started.
- The first diagnostic backtest stopped before strategy loading because the
  selected read-only host data root did not contain Freqtrade's expected
  `okx` subdirectory. An explicit existing datadir will be located instead.
- The explicit active OKX datadir reached leverage initialization but remained
  intentionally read-only; Freqtrade then attempted to write its leverage-tier
  cache. A writable isolated public-data copy is required for the diagnostic.
- The writable diagnostic reached V4 indicators and exposed an empty-crypto
  residual access before fail-closed handling. A targeted production guard and
  regression test were added; complete cross-asset validation data is still
  required for an economic-shaped diagnostic.
- The empty-frame fix passes 16 focused V4 tests. Replaced the incomplete
  public-data copy with the existing complete isolated V3 validation dataset.
- V4 completed the 2026 diagnostic: one AAPL long, +0.054941 USDT / +0.18%,
  channel exit, no shorts. The interval remains diagnostic-only.
- Ran the V4 attribution tool against the real result. It reports 42.75% MFE
  capture and correctly leaves R coverage at zero without a sidecar ledger.
- Completed Phase 31. Phase 32 final regression, static checks, and active
  service verification are in progress.
- Added V4-only funding availability semantics: live/dry-run waits one complete
  1h funding candle and expires it after 9h; research excludes Freqtrade's
  endpoint-unsliced funding frame from candidate scoring and reweights the
  remaining 95%. Historical funding costs remain available to the engine.
- Added regression coverage proving a future funding event cannot change
  earlier rows. The focused V4 suite passes 17/17.
- CloudCone Freqtrade 2026.6 recursive analysis passes at 1000/1125/1250
  startup candles with no recursive variance and no indicator-only lookahead.
- Final deployment suite passes 72/72. Compileall, seven JSON files, V4 Compose,
  and `git diff --check` all pass.
- Reconfirmed the frozen V3 strategy/config hashes are unchanged. The active V2
  Freqtrade and Risk Guard containers remain running, and the isolated V4
  validation directory contains neither `.env` nor a trade database.
- Completed Phase 32. V4 implementation and engineering validation are done;
  economic release gates remain intentionally unpassed, so no V4 service was
  started.
- Started Phase 33. The comparison will rerun V3 and V4 from current frozen
  files on the same isolated CloudCone public dataset and execution settings;
  no strategy service or private credential will be used.
- Confirmed the uploaded V3/V4 strategy and configuration SHA256 values match
  the frozen local inputs. The first data-inventory command used an empty
  config and was rejected before reading data; it will be rerun with the
  credential-free V4 config.
- Confirmed 88 OKX pair/timeframe/type combinations and fixed the comparable
  interval to 2026-04-22 through 2026-07-27 with 15m detail, protections, and
  0.10% fee per side. Freqtrade 2026.6 does not support `--backtest-notes`, so
  comparison identity will be preserved in filenames and this log.
- Fresh comparable runs completed. V3 reproduced 13 long trades,
  -0.497117 USDT / -1.6571%, PF 0.2448, closed-trade Sharpe -1.09, and 1.6571%
  drawdown. V4 produced one AAPL trend-long trade, +0.054941 USDT / +0.1831%,
  and no observed drawdown.
- Added a reusable comparison parser after nested PowerShell quoting broke the
  first inline parser. It proves the single V4 trade is exactly the same
  AAPL 2026-07-15 through 2026-07-20 trade already present in V3.
- The comparison parser now reports shared/omitted trades and conservative
  fee-plus-absolute-funding stress. Its focused research-tool suite passes 8/8.
- Under a mechanical 1.5x/2.0x cost-magnitude stress, V3 falls to
  -0.572524/-0.647931 USDT while V4 remains +0.049188/+0.043434 USDT. This is
  not a slippage or order-book simulation and does not repair the one-trade
  sample problem.
- Final regression passes 73/73; the comparison parser compiles and the
  whitespace diff check passes.
- Comparison archives were preserved in the isolated CloudCone directory with
  SHA256 `f3bb3ad83f3b1db269faa634c6f931fe8d56a4200009b565245989996526af7a`
  for V3 and
  `7cf82df2569746be89417431eeed2b154f544997ca362df06528353dac5071ea`
  for V4.
- Reconfirmed active V2 Freqtrade/Risk Guard health, no V3/V4 container, and no
  `.env` or trade database in the comparison directory. Completed Phase 33.
- Started Phase 34. The small-profit arithmetic and a three-position
  counterfactual will be evaluated without changing V4 defaults or starting a
  service.
- Exact V4 winner sizing is 1.63075 USDT margin at 2x leverage; its exported
  +3.365684% leveraged net return therefore produces +0.054941 USDT.
- The V4-only `max_open_trades=3` diagnostic completed with two simultaneous
  Mag7 longs: the original AAPL winner and a new AMZN stop-out. Net result
  deteriorated to -0.083394 USDT / -0.2780%, PF 0.3972, closed-trade Sharpe
  -0.17, and 0.4611% drawdown.
- The base run's 48 rejected entry signals represent repeated blocked signal
  rows, not 48 independent trades. Removing the slot constraint exposed only
  one additional closed trade in this interval.
- Preserved the three-slot diagnostic archive with SHA256
  `5d1c81dce9290dc91f2037cd71ba807c931312d7918662c7be012f81f25aa924`.
  Reconfirmed the committed V4 config remains `max_open_trades=1`,
  `dry_run=true`, every approval flag remains false, and active V2 services are
  healthy. Completed Phase 34.
- Started the findings-driven optimization request and reread the UTF-8
  findings, plan, recent progress, V4 strategy, tests, and dirty-worktree
  inventory.
- Chose a new isolated V5 rather than mutating V4. The experiment will preserve
  the one-position and account-risk limits while adding independent
  continuation/re-entry opportunities and stable per-candle candidate
  selection.
- Session catch-up first failed because `python` is unavailable; `py -3`
  completed successfully. An initial root-level strategy search also used the
  wrong layout before the deployment-specific path was identified.
- Added the isolated V5 strategy, config, Compose identity, port 8084, database,
  Risk Guard paths, and V4 baseline manifest. V5 compiles and no service was
  started.
- The first focused V5 run passed 6/8 tests. Corrected one informative-column
  suffix and one stake-test pair fixture while preserving all production
  thresholds and fail-closed behavior.
- Focused V5 tests now pass 8/8 and the complete V2/V3/V4/V5 deployment suite
  passes 81/81. Compileall, V5 JSON parsing, Compose validation, and whitespace
  checks pass.
- Completed Phases 35-37. Phase 38 now moves to isolated Freqtrade discovery
  and a same-data V4/V5 diagnostic comparison; economic thresholds remain
  frozen and no service will be started.
- Created a new credential-free CloudCone V4/V5 comparison directory. Strategy
  discovery reports V1-V5 `OK`; active V2 remains untouched.
- The first backtest lacked the external public-data mount. After copying the
  existing isolated 20 MB dataset, V5 completed the frozen 95-day diagnostic
  with the same single AAPL trade and +0.054941 USDT as V4.
- Exported V5 signals. Freqtrade's rejected-signal analyzer fails on its own
  missing `close_date` column, so a read-only compatible-pickle inspector was
  added to diagnose model coverage without changing parameters.
- Signal attribution proved the previous-high continuation trigger overlapped
  V4 breakouts. Replaced it structurally with previous-close resumption while
  retaining trend, regime, channel-exclusion, session, execution, and risk
  gates; focused V4/V5 tests pass 25/25.
- The revised model generated four AMZN continuation rows during the existing
  AAPL position but no additional one-slot trade. A three-slot counterfactual
  reproduced the losing AMZN exposure and approximately -0.084 USDT / -0.28%.
- Freqtrade recursive analysis passes at 1000/1125/1250 candles with no
  recursive variance or indicator-only lookahead. Final attribution reports
  one AAPL trend-long, +0.05494078 USDT, 0.01150656 USDT fee-plus-funding cost,
  and 42.75% MFE capture.
- Final deployment tests pass 81/81, compilation and whitespace checks pass,
  V4 hashes remain frozen, active V2 is healthy, and the isolated V5 directory
  contains no `.env` or database. Completed Phases 38-39 without starting V5.
- Started the requested deep-backtest phase. Repository inspection confirmed
  the 2018-2025 research manifest exists but no actual underlying history or
  long-horizon V5 backtester is present.
- Added Phases 40-44 for reproducible data acquisition, a clearly labeled daily
  economic proxy, rolling/frozen validation, ablations, stress tests, and a
  profit-bottleneck verdict. The 95-day OKX interval will not be used to tune
  these research rules.
- Downloaded 33,508 adjusted daily rows across 15 underlying assets, converted
  Samsung and SK Hynix to USD with KRW/USD, and documented the short SNDK and
  ETH histories. Raw and prepared data remain under ignored `.uploads`.
- Added the research-only V5 daily economic proxy backtester. It uses completed
  daily signals, next-session opens, account-risk sizing, explicit fees,
  slippage and assumed carry, mark-to-market drawdown, attribution, Monte
  Carlo, cost stress, and parameter perturbation. It is explicitly not an OKX
  execution backtest.
- Corrected annual attribution to bucket by exit year and replaced repeated
  feature-column insertion with a single `pd.concat`; the full rerun emitted
  no pandas fragmentation warning.
- Added seven focused backtester contract tests for next-session execution,
  one-slot rejection, stop/R/cost accounting, annual attribution,
  no-lookahead invariance, cost monotonicity, and portfolio-risk scaling.
- The one-slot 2018-2023 proxy returned +30.50% on 50 trades, but removing the
  largest five winners produced -1.675906 USDT. Frozen 2024-2025 returned only
  +2.48%, Sharpe 0.405, 0.034R expectancy, and -1.012319 USDT without the top
  five; independently reset 2025 lost -1.11%.
- Attribution confirmed trend longs and a few crypto trends supplied nearly
  all long-history profit. Continuation was flat in research, all validation
  shorts lost, and the memory sleeve was negative in both main periods.
- A raw three-slot result was rejected as a risk-amplified comparison. The
  equal-total-risk, long-only three-slot diagnostic produced 114 research
  trades and +19.61%, then 44 frozen-validation trades and +5.73%, PF 3.18,
  Sharpe 1.14, 1.68% drawdown, and positive top-five-removed profit.
- That diagnostic remained +5.43%/+5.13% under 1.5x/2.0x frozen-period costs
  and positive in separately reset 2024 and 2025 runs. It remains ineligible
  for deployment because execution fidelity, total validation sample,
  short-side, and per-group gates are not met.
- Completed Phases 40-43. Phase 44 final regression and release verdict are in
  progress; active V2 and every approval flag remain unchanged.
- Final regression passes 87/87 tests. Compileall, nine runtime JSON parses,
  and `git diff --check` pass.
- Added a narrow ignore rule for `.uploads/beta-v5-research/` after the final
  isolation audit showed parquet, raw history, and bulk JSON were otherwise
  untracked. No research files were deleted.
- Completed Phase 44. The release verdict remains blocked: V5 stays offline,
  active V2 is unchanged, and the equal-total-risk long-only portfolio is a
  separately testable next experiment rather than a production parameter
  change.

## 2026-07-28 V6 Portfolio Optimization
- Added Phases 45-50 for an isolated V6 implementation derived from the
  equal-total-risk long-only result.
- V6 will not be a config-only slot increase. V5 currently caches one selected
  pair per hour, so V6 requires a stable candidate set plus portfolio-aware
  admission controls.
- The frozen design keeps leverage, stop bounds, 5 USDT margin cap, 15 USDT
  reserve, execution vetoes, and channel-only exits unchanged. It divides
  model risk by three, disables short entries, quarantines memory execution,
  and retains memory data only for regime quality.
- Added the isolated V6 strategy, configuration, Compose services, V5 baseline
  manifest, and focused tests. No service was started.
- The first focused run passed 7/9 tests. Corrected a shared mutable test frame
  and rounded the calculated stake to execution precision before comparing it
  with the exchange minimum; risk budgets and minimum-stake policy were not
  relaxed.
- V6 strategy tests now pass 9/9. It maintains one stable candidate set per
  completed hour, reserves existing slots and group capacity, rejects missing
  correlation evidence, and never backfills memory or short candidates.
- Extended the daily economic proxy with a V6 portfolio profile using the same
  memory quarantine, group caps, 90% completed-daily correlation limit, open
  position accounting, and one-third risk scale.
- Combined V6 runtime and research-contract tests pass 17/17; focused
  compilation and whitespace checks pass. Completed Phases 45-47.
- V6 proxy results are +16.28% on 97 research trades and +5.74% on 36 frozen
  validation trades. Both periods remain positive after removing the largest
  five winners in the continuous runs; 2x frozen-period costs retain +5.16%.
- Breakout-only improves return and concentration but cuts samples to 71/29.
  Two slots increase return but lose frozen top-five robustness. Correlation
  sensitivity from 0.85 to 0.95 stays profitable; 0.90 remains frozen to avoid
  selecting the best validation threshold.
- Local Docker Desktop is not running, so daemon-backed resolver/backtest
  validation will use the existing isolated CloudCone runtime if available.
- Isolated Freqtrade 2026.6 discovery reports V1-V6 `OK`. The first exact V6
  OKX execution diagnostic exported zero entry signals, not merely zero fills.
- A V5-code/V6-config A/B produced the known AAPL winner and AMZN stop, proving
  the reduced whitelist and memory informative inputs are not the zero-signal
  cause. The next single-variable test retains the inherited futures
  capability flag while preserving V6's four independent long-only gates.
- The futures-capability A/B still exported zero accepted signals, so
  `can_short=false` was restored. V6's dry-run wallet is now aligned with the
  user's stated 80 USDT solely for isolated validation.
- At 80 USDT, ordinary/strong stock trend margins are approximately
  2.67/4.00 USDT at the minimum stop, and continuation margins are
  approximately 2.00/2.67 USDT. BTC can still fall below its roughly 4 USDT
  minimum-contract margin under the one-third crypto risk cap.
- The exact 80 USDT OKX diagnostic executed the known simultaneous AAPL and
  AMZN longs. AAPL used 1.63075 USDT margin and earned +0.054941 USDT; AMZN
  used 1.27955 USDT margin and lost -0.069182 USDT. Net was -0.014241 USDT
  with 0.0865% maximum account drawdown.
- Completed the 80 USDT capital-feasibility assessment. Stocks and ETFs are
  materially more executable, but V6's one-third crypto risk budget still
  usually cannot fund OKX's roughly 4 USDT minimum BTC margin. This matters
  because BTC supplied about 59% of the 2018-2023 V6 proxy profit.
- Re-ran the complete V2-V6 deployment suite: 98/98 tests pass. Compileall,
  all 11 runtime JSON parses, V6 Compose parsing, and `git diff --check` pass.
- Completed Phases 48-50. V6 remains offline: its exact 95-day OKX diagnostic
  is two trades and -0.014241 USDT, no BTC/ETH trade was executed, and the
  economic sample/release gates remain unpassed. No V6 service was started and
  active V2 was not changed.

## 2026-07-29 V7 Moderately Bolder Allocation
- Added Phases 51-55. V6 will be frozen and V7 will be isolated.
- The intended change is capital-aware risk redistribution for the 80 USDT
  wallet: modestly increase the risk available to a feasible high-ranked
  contract, enforce a hard aggregate risk ceiling, and select the next feasible
  candidate when an OKX minimum contract cannot fit. Leverage, the 15 USDT
  reserve, the 5 USDT per-position margin cap, long-only direction, memory
  quarantine, execution vetoes, and channel-only exits remain unchanged.
- Added V7 strategy, isolated 80 USDT configuration, port 8086 Compose stack,
  V6 baseline manifest, capital-feasibility fallback, and aggregate
  open-plus-pending risk accounting. No service was started.
- Focused V7 and research-contract tests pass 14/14 after isolating the
  open-trade dependency and correcting the exact minimum-stop fixture.
- Frozen daily-proxy comparison: V7 returns 25.09% versus V6 16.28% in
  2018-2023, and 8.57% versus 5.74% in 2024-2025. Drawdown increases to
  3.18% and 2.38%, respectively; both top-five-removed results remain positive.
- Two read-only SSH checks produced no output, so no new exact V7 OKX result
  was claimed and no remote state was changed.
- Final full deployment regression passes 104/104. Compileall, runtime JSON,
  V7 Compose, frozen-hash, and whitespace checks pass. Completed Phases 51-55;
  V7 remains offline and active V2 remains unchanged.

## 2026-07-29 V7 Deep Audit
- Added Phases 56-59 for a read-only adversarial audit using local code,
  frozen research evidence, and current official Freqtrade/OKX documentation.
- The audit will not treat higher V7 proxy return as a new signal edge and will
  not modify or start V7.
- Verified the official callback ordering and corrected an initial hypothesis:
  live/dry-run sizes before confirmation, while backtest confirms before
  sizing. The inherited risk-plan requirement is therefore not an automatic
  live-entry blocker.
- Reproduced two material failures: a rejected candidate can leave a 0.25%
  phantom reservation, and a persistence `RuntimeError` escapes V7 sizing into
  Freqtrade's documented proposed-stake fallback.
- Completed the five-axis review. Additional gaps include non-persistent
  reservations, one-hour stale candidate cache, static contract conversions,
  stop-limit gap/non-fill exposure, repeated DB scans, tail-factor
  concentration, incomplete telemetry, and proxy/execution mismatch.
- Completed Phases 56-59 with a nine-step V8 priority order. V7 was not
  modified or started, and active V2 remains unchanged.

## 2026-07-31 Archive and documentation pass

- Started Phase 60 and recovered the existing file-based plan using the
  bundled Python runtime plus the actual `.agents` skill path.
- Recorded one harmless planning edit failure caused by truncated terminal
  output; no project source or deployment artifact was changed.
- Confirmed the complete V7-to-V1 inheritance chain before deciding archive
  scope.
- Confirmed Freqtrade's non-recursive default resolver behavior and selected a
  low-risk archive boundary: historical runnable artifacts move; V7's imported
  source modules and their regression tests remain discoverable.
- Paused the tentative V2 archive decision because existing records identify
  V2 as the active CloudCone strategy and V7 as an offline audited candidate.
- Read-only CloudCone inspection confirmed the active healthy strategy is V2.
  One optional remote `grep` failed from quoting and was not retried because
  `docker inspect` already provided authoritative command-line evidence.
- Completed Phase 60. Started Phase 61 with V2 retained as production and only
  stopped V3-V6 runnable stacks selected for archive.
- Moved V3-V6 Compose/config pairs, V3/V4 legacy specifications, and V3-V6
  baseline manifests into `deployments/okx-aggressive/archive/v1-v6/` without
  changing their bytes.
- Updated historical isolation tests and baseline manifest paths to the new
  archive locations. Kept active V2 and offline V7 entrypoints at deployment
  root.
- Added `STRATEGIES_V1_TO_V7_CN.md` with per-version signals, risk, evidence,
  status, dependency, and operational boundaries; added an archive index and
  refreshed the deployment README. Completed Phases 61-62.
- Started Phase 63 validation.
- Initial Phase 63 checks pass: 110 deployment tests, Python compilation, all
  runtime/archive JSON parsing, current and archived Compose parsing, frozen
  baseline hashes, and whitespace validation. The only nonzero static-search
  status represented zero stale-path matches.
- Added explicit archive banners to the moved V3/V4 specifications so their
  original commands cannot be mistaken for current runnable instructions.
- The knowledge-governance inventory found no on-disk project rule file. Added
  a scoped deployment `AGENTS.md` with the verified V2/V7 status, UTF-8 rule,
  archive boundary, safety constraints, and regression command.
- Final validation passes: 110/110 deployment tests, compileall, active and
  archived JSON/Compose parsing, baseline hash checks, deployment Markdown link
  checks, stale active-reference search, secret-track check, and
  `git diff --check`.
- One pre-existing pytest warning remains: `pytest-asyncio` has no explicit
  `asyncio_default_fixture_loop_scope`. It is unrelated to this archive task.
- Completed Phase 63 and the five-axis review. Remote services were never
  changed; existing uncommitted V3-V7 work and planning files remain intact.
