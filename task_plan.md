# Task Plan: OKX Strategy Runtime Hardening

## Goal
Harden, validate, version, and safely deploy the OKX strategy in dry-run mode on the CloudCone host using environment-only credentials.

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

## Decision Rules
- Do not tune signal parameters or alter entry/exit rules.
- Keep live and dry-run runtime checks fail closed.
- Preserve the exact stake-sizing stop distance across candle transitions.
- Remote deployment is authorized only with `dry_run=true`; do not enable live trading or validation approvals.
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
| Session catch-up failed via `python` and then used the wrong `.codex` skill path | Re-ran successfully with `py -3` and the actual `.agents` skill path. |
| Approval-flag patch expected unquoted values | Read the exact UTF-8 lines and reissued the patch with the existing quotes. |
| Network-disabled backtest could not load OKX public market metadata | Re-run the read-only backtest with network access and temporary non-secret credentials; no private API or order command is used. |
| One full local pytest run hit a transient Windows `WinError 5` during `os.replace` in a temp state file | The targeted test and immediate full rerun both passed; record as an environment-only flake and retain the passing rerun as the final result. |
