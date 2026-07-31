# OKX 跨资产 Beta 轮动策略 V4

> 归档说明（2026-07-31）：V4 已停止作为独立候选运行。本文中的路径与命令
> 保留为历史验证记录，不能从当前归档目录直接执行。当前版本关系与运行状态见
> `../../../STRATEGIES_V1_TO_V7_CN.md`。

## 当前状态

V4 是独立研究版本，不是实盘候选。它不会替换 V2 或 V3，也不得在经济
门槛通过前启动 `docker-compose.beta-v4.yml`。

- 策略：`runtime/strategies/OkxCrossAssetBetaV4.py`
- 配置：`runtime/config.beta-v4.json`
- Compose：`docker-compose.beta-v4.yml`
- 交易数据库：`runtime/trades-beta-v4.sqlite`
- 策略状态：`runtime/risk_guard/beta-v4-regime.json`
- Risk Guard：`runtime/risk_guard/beta-v4-state.json`
- API端口：`127.0.0.1:8083`

V4 配置固定为 `dry_run=true`、30 USDT钱包、一个持仓、15 USDT现金储备和
5 USDT保证金上限。三个审批开关必须保持 `false`。

## 与 V3 的主要区别

- 方向分改为 `-1～1`，横盘资产不再因低波动获得多头分。
- 使用20、60、120日波动率标准化趋势。
- QQQ相对SPY、股票相对QQQ计算滚动残差动量。
- 波动率和组内离散度只降低风险，不参与方向预测。
- 做空分为 `systemic_short` 和 `residual_short`。
- 多空候选在同一个选择器中竞争，始终最多选择一个品种。
- 提供 `channel_only`、`delayed_break_even`、`slow_atr` 三套固定退出候选。

## 安全约束

- 缺少QQQ、BTC、ETH，Mag7少于5个，或存储组少于2个时禁止开仓。
- 日线特征只在下一日可用，超过36小时视为陈旧。
- 股票类新增仓位继续受美国/韩国现金市场日历限制。
- live/dry-run缺少资金费率、订单簿或关键K线时禁止开仓。
- live/dry-run中的资金费率事件必须等对应1小时周期完整结束后才可用，
  并在9小时后失效。
- Freqtrade手工请求的历史funding数据不会随分析端点可靠截断；因此研究
  模式不把funding用于候选评分，按剩余95%权重重算并强制标记为
  “非完整成本回测”。回测引擎仍独立计入可获得的历史资金费用。
- 低于OKX最小合约数量时放弃交易，不提高风险。
- later fill和exit fill不得覆盖初始止损距离。
- V4不包含已经证伪的Beta转弱立即退出和48小时品种隔离。

## 本地检查

```bash
python -m pytest -q deployments/okx-aggressive/tests
python -m compileall -q \
  deployments/okx-aggressive/runtime/strategies/OkxCrossAssetBetaV4.py \
  deployments/okx-aggressive/tools
docker compose -f deployments/okx-aggressive/docker-compose.beta-v4.yml \
  config --quiet
```

配置解析不代表授权启动服务。不得运行 `up`，除非研究与发布门槛已经全部
通过并得到单独授权。

## 研究结果归因

Freqtrade结果本身不可靠地导出自定义初始R，因此分析工具不会用最终追踪
止损反推初始风险。必须提供独立风险账本：

```json
{
  "trades": {
    "QQQ/USDT:USDT|2025-01-01 00:00:00+00:00": 0.15
  }
}
```

生成归因：

```bash
python deployments/okx-aggressive/tools/analyze_beta_v4.py \
  backtest-result.zip \
  --risk-ledger initial-risk.json \
  --output-json attribution.json \
  --output-csv trades.csv
```

报告包含品种、组别、方向、信号模型、退出原因、收益、MFE、MAE、
MFE捕获率、R倍数、手续费和资金费率。R覆盖率不足100%时，退出策略选择和
发布验证都会失败。

研究结果即使包含Freqtrade计算的资金费用，也不代表funding已参与历史
候选排名；所有V4历史结果必须保留“非完整成本回测”标签。live/dry-run则
继续对缺失或陈旧funding执行fail-closed。

## 退出策略选择

2018～2023采用三个3年训练/1年测试折。每个退出策略的每个测试折都要生成
归因JSON：

```bash
python deployments/okx-aggressive/tools/select_beta_v4_exit_policy.py \
  --fold channel_only:2021=channel-2021.json \
  --fold channel_only:2022=channel-2022.json \
  --fold channel_only:2023=channel-2023.json \
  --fold delayed_break_even:2021=delayed-2021.json \
  --fold delayed_break_even:2022=delayed-2022.json \
  --fold delayed_break_even:2023=delayed-2023.json \
  --fold slow_atr:2021=slow-2021.json \
  --fold slow_atr:2022=slow-2022.json \
  --fold slow_atr:2023=slow-2023.json \
  --output exit-selection.json
```

没有策略满足正收益折、PF、回撤、去除最大5笔盈利和完整R覆盖门槛时，工具
返回非零状态并停止发布，不允许临时增加第四套参数。

## 发布验证

2024～2025必须保持未参与退出策略选择。2026-04-22至2026-07-27只能用于
工程诊断，不能作为经济验证。

```bash
python deployments/okx-aggressive/tools/validate_beta_v4.py \
  --research research-2018-2023.zip \
  --research-attribution research-attribution.json \
  --validation validation-2024-2025.zip \
  --validation-attribution validation-attribution.json \
  --stress 1.5x=validation-cost-1.5x.zip \
  --stress 2x=validation-cost-2x.zip \
  --monte-carlo-p95 0.14
```

只有工具返回 `backtest_release_candidate=true`，并完成2026-07-28之后的独立
OKX前向验证，才可以单独申请启动V4 dry-run。实盘仍需新的人工审批，不能
从回测或dry-run自动晋级。
