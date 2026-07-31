# OKX Deployment Agent Notes

## Scope

This directory contains the CloudCone OKX Freqtrade deployment, historical
cross-asset research versions, and their regression tooling.

## Current State

- CloudCone runs `OkxAggressiveTrendV2` in `/root/freqtrade` and an isolated
  `OkxCrossAssetBetaV7` candidate in `/root/freqtrade-v7`; both are dry-run.
- V7's identified engineering blockers are fixed, but it lacks a new untouched
  forward sample. Keep all live approval gates closed unless the account owner
  explicitly performs the manual activation runbook.
- V3-V6 runnable stacks are historical and live under `archive/v1-v6/`.

## Important Boundaries

- Read files containing Chinese explicitly as UTF-8. In PowerShell, set
  `[Console]::OutputEncoding` and use `Get-Content -Encoding UTF8`.
- Do not delete or move V1-V6 strategy modules from `runtime/strategies/`.
  V7 imports the full V7 -> V1 inheritance chain.
- Do not enable validation, live, or S+ approval flags by default.
- Never commit `.env`, credentials, databases, logs, caches, or bulk backtest
  data.
- Treat archived Compose files as audit evidence, not runnable entrypoints.
- Do not change remote services during local organization or documentation
  work. Remote state checks should be read-only unless deployment is explicit.

## Verification

Run the deployment regression suite:

```powershell
py -3 -m pytest deployments/okx-aggressive/tests -q
```

Also parse all runtime/archive JSON, compile deployment Python, validate active
Compose files, and run `git diff --check` after structural changes.

See `STRATEGIES_V1_TO_V7_CN.md` for the authoritative version map, strategy
behavior, evidence, archive policy, and release status.
