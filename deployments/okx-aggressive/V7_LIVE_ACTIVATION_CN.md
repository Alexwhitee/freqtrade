# V7 手动实盘启用与回滚手册

本文只描述发布操作，不构成收益或风险保证。V7 的工程阻塞已修复，但新的未触碰
前向 dry-run 样本仍不足。真实交易会产生实际亏损、滑点、资金费率和强平风险；
是否启用只能由 OKX 账户所有者决定并亲自执行。

## 1. 启用前硬条件

以下任一项不满足就停止，不要修改批准门：

- 当前 V7 dry-run 容器持续健康，日志无账本、数据库、行情快照或 Risk Guard 错误。
- 风险账本尚未创建，或 `runtime/risk_guard/beta-v7-risk-ledger.json` 可读取且
  `valid` 为 `true`；只要文件存在，预检就必须显示 `"risk_ledger": "valid"`。
- V7 本地数据库没有未平仓交易和挂单。
- OKX 账户没有任何未平仓永续仓位或挂单，并且 API Key 仅具备读取和交易权限，
  不具备提币权限。
- OKX API Key 绑定服务器 IP，账户已设置账户级止损/告警，并准备好手机端人工平仓。
- 市场快照不超过七天，当前 OKX 合约的 `amount_min` 和 `contractSize` 与快照一致。

若部署文件由 root 解压，首次启动前执行 `chown -R 1000:1000 runtime`，否则容器
用户无法写日志、SQLite 和风险状态文件。

在服务器的 V7 目录执行 dry-run 预检：

```bash
cd /root/freqtrade-v7
docker run --rm --user 0:0 --entrypoint python \
  -v "$PWD:/deployment:ro" \
  freqtradeorg/freqtrade:stable \
  /deployment/tools/validate_beta_v7_live.py \
  --root /deployment --env-file /deployment/.env
```

输出必须包含 `"ok": true`、`"effective_dry_run": true`、
`"database_open_trades": 0` 和 `"database_open_orders": 0`。

## 2. 账户所有者手动打开实盘门

先停止 V7 dry-run，保留数据库和风险账本：

```bash
cd /root/freqtrade-v7
docker compose -p freqtrade-v7 -f docker-compose.beta-v7.yml down
cp .env ".env.before-live.$(date -u +%Y%m%dT%H%M%SZ)"
chmod 600 .env .env.before-live.*
```

编辑 `.env`，只改变以下五项。三个 `RISK_GUARD_*` 是原有批准门，
`BETA_V7_LIVE_APPROVED` 是 V7 的额外独立批准门：

```dotenv
FREQTRADE__DRY_RUN=false
RISK_GUARD_VALIDATION_APPROVED=true
RISK_GUARD_LIVE_APPROVED=true
RISK_GUARD_SPLUS_APPROVED=true
BETA_V7_LIVE_APPROVED=true
```

不要把 `.env` 内容打印到终端、聊天或 Git。然后执行会访问 OKX 私有只读接口的
最终预检；该命令不下单、不撤单，也不修改账户：

```bash
docker run --rm --user 0:0 --entrypoint python \
  -v "$PWD:/deployment:ro" \
  freqtradeorg/freqtrade:stable \
  /deployment/tools/validate_beta_v7_live.py \
  --root /deployment --env-file /deployment/.env \
  --require-live --check-okx
```

只有输出为 `"ok": true` 时，账户所有者才可停止旧 V2 并启动 V7。停止 V2 只删除
旧容器和网络，不删除 `/root/freqtrade/user_data`、数据库或已有备份：

```bash
cd /root/freqtrade
docker compose down

cd /root/freqtrade-v7
docker compose -p freqtrade-v7 -f docker-compose.beta-v7.yml up -d
docker compose -p freqtrade-v7 -f docker-compose.beta-v7.yml ps
docker compose -p freqtrade-v7 -f docker-compose.beta-v7.yml logs --tail 200
```

启动后立即通过 SSH 隧道检查 FreqUI，并核对 `dry_run` 为 `false`、余额、交易对、
最大三仓和逐仓模式。不要同时运行会使用同一 OKX API Key 或同一策略资金的旧实例。

## 3. 紧急停止

先阻止策略继续操作：

```bash
cd /root/freqtrade-v7
docker compose -p freqtrade-v7 -f docker-compose.beta-v7.yml stop
```

停止容器不会平掉交易所仓位，也不会撤销交易所止损。随后必须在 OKX 手机端或网页端
人工核对所有仓位和挂单；需要退出时使用交易所的只减仓平仓，并确认仓位和挂单都为零。

## 4. 回滚到 dry-run

确认 OKX 仓位和挂单为零后，将 `.env` 中 `FREQTRADE__DRY_RUN` 改回 `true`，并把
四个批准门全部改回 `false`。不要沿用实盘数据库启动新的 dry-run；完整归档 SQLite
主文件及可能存在的 WAL/SHM：

```bash
cd /root/freqtrade-v7
archive="runtime/backups/v7-live-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 -p "$archive"
for file in runtime/trades-beta-v7.sqlite*; do
  [ ! -e "$file" ] || mv -- "$file" "$archive/"
done
docker compose -p freqtrade-v7 -f docker-compose.beta-v7.yml up -d
```

数据库主文件不要直接删除；若需要全新 dry-run 数据库，应先把主文件移动到带时间戳的
备份目录，再启动。风险账本损坏或状态不明时不要手工伪造，保持服务停止并检查日志。
需要改为恢复旧 V2 dry-run 时，先停止 V7 dry-run，再启动 V2：

```bash
cd /root/freqtrade-v7
docker compose -p freqtrade-v7 -f docker-compose.beta-v7.yml down

cd /root/freqtrade
docker compose up -d
```
