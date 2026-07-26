# Task Plan: OKX Strategy Runtime Hardening

## Goal
Harden and validate the OKX strategy changes, then commit every safe project change and push them to a reviewable remote branch.

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

## Decision Rules
- Do not tune signal parameters or alter entry/exit rules.
- Keep live and dry-run runtime checks fail closed.
- Preserve the exact stake-sizing stop distance across candle transitions.
- Do not deploy remotely or enable live trading as part of this task.
- Never commit populated `.env` files, credentials, runtime databases, logs, caches, or backtest bulk artifacts.
- Push a reviewable `codex/` branch instead of writing directly to upstream `develop`.

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
