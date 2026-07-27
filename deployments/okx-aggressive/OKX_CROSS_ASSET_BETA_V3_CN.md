# OKX 跨资产 Beta 轮动策略 V3

## 当前状态

V3 已作为独立候选实现，V2 的策略文件、配置、数据库、端口和云端
dry-run 均不被替换。V3 仍处于“代码完成、研究与执行验证待完成”阶段，
不得据此开启实盘。

独立运行资源如下：

- 策略：`OkxCrossAssetBetaV3`
- 配置：`runtime/config.beta.json`
- Compose：`docker-compose.beta.yml`
- 容器：`freqtrade-beta-v3`、`freqtrade-risk-guard-beta-v3`
- 本地端口：`127.0.0.1:8082`
- 数据库：`runtime/trades-beta-v3.sqlite`
- 日志：`runtime/logs/freqtrade-beta-v3.log`
- Risk Guard：`runtime/risk_guard/beta-v3-state.json`
- Beta 状态：`runtime/risk_guard/beta-v3-regime.json`

Compose 文件把所有实盘审批开关强制设为 `false`。即使共享 `.env` 中误设了
审批值，V3 服务也不会继承这些值。

## 已实现交易逻辑

正式交易池包括 QQQ、七巨头、MU、SNDK、SAMSUNG、SKHYNIX、BTC 和
ETH 的 OKX USDT 永续。SPY 只作为状态输入；DRAM、LITE、SKHY 不进入
Stage 1 白名单。

每根已完成 1 小时 K 线使用已完成日线计算 Beta Score。日线特征向后偏移
一天后再做 `merge_asof`，防止使用尚未收盘的日线。QQQ、BTC、ETH 缺一，
七巨头有效数少于 5，存储组少于 2，或者日线状态超过 36 小时，状态均为
`blocked`。

入场保持 4 小时 30/80 通道、EMA30/EMA60、ADX 与成交量确认。多个品种
同时触发时，`confirm_trade_entry` 对全部已分析白名单品种计算同一时点的
候选分，只允许唯一优胜者下单。5 分内并列依次偏好 QQQ、BTC、较低波动率。

股票合约只在相应现金市场开盘 15 分钟后至收盘 15 分钟前新增仓位。美国
时段通过 `America/New_York` 自动处理夏令时；韩国时段通过 `Asia/Seoul`
处理。节假日和提前收市来自可审计的
`runtime/market_calendars.beta.json`。韩国日历超出已审核年份时 fail-closed。
平仓、止损、Risk Guard 强退不受现金市场时段限制。

Stage 1 在策略层硬限制：

- 最多一个持仓；
- 单笔账户风险 0.75%；
- 普通 risk-on 风险乘数 0.65，risk-off 空头为 0.50；
- 股票/ETF 最高 2 倍，BTC/ETH 最高 3 倍；
- 单笔保证金最高 5 USDT；
- 至少保留 15 USDT；
- 股票止损距离 2.5%～6%，加密资产 2.5%～8%；
- 低于 Freqtrade/交易所传入的最小 stake 时返回 0，不凑单。

V2 已验证的初始风险持久化、later fill/exit fill 保护、资金费率、价差、
K 线新鲜度、Risk Guard 和人工实盘审批继续生效。

## 数据与研究边界

`runtime/research_data_manifest.beta.json` 冻结了研究品种、币种和时间划分。
`tools/prepare_beta_research.py` 验证现金市场 CSV，并把三星、海力士价格用
逐日 `KRW/USD` 汇率转换成美元口径。生成的面板只能验证信号，不得当作
OKX 永续成交历史。

示例：

```bash
python tools/prepare_beta_research.py \
  research/raw runtime/research/beta-underlying-usd.parquet
```

真实执行验证只能使用 OKX futures、mark、funding_rate 和订单簿数据。
目前股票永续历史较短，因此“2018～2025 长期研究”和“2026 OKX 执行验证”
必须分别报告。

## OKX 数据与回测

先验证配置，不启动服务：

```bash
docker compose -f docker-compose.beta.yml config --quiet
```

下载 OKX 数据：

```bash
docker compose -f docker-compose.beta.yml run --rm freqtrade-beta-v3 \
  download-data --config /freqtrade/user_data/config.beta.json \
  --trading-mode futures --timeframes 15m 1h 4h 1d \
  --candle-types futures --timerange 20260301- --prepend

docker compose -f docker-compose.beta.yml run --rm freqtrade-beta-v3 \
  download-data --config /freqtrade/user_data/config.beta.json \
  --trading-mode futures --timeframes 1h \
  --candle-types mark funding_rate --timerange 20260301- --prepend
```

执行独立回测、Lookahead 与 Recursive：

```bash
docker compose -f docker-compose.beta.yml run --rm freqtrade-beta-v3 \
  backtesting --config /freqtrade/user_data/config.beta.json \
  --strategy OkxCrossAssetBetaV3 --timeframe 1h --timeframe-detail 15m \
  --timerange 20260301- --enable-protections --fee 0.001 --export trades

docker compose -f docker-compose.beta.yml run --rm freqtrade-beta-v3 \
  lookahead-analysis --config /freqtrade/user_data/config.beta.json \
  --strategy OkxCrossAssetBetaV3 --timeframe 1h --timerange 20260301- \
  --stake-amount 5 --dry-run-wallet 30

docker compose -f docker-compose.beta.yml run --rm freqtrade-beta-v3 \
  recursive-analysis --config /freqtrade/user_data/config.beta.json \
  --strategy OkxCrossAssetBetaV3 --timeframe 1h --timerange 20260401- \
  --startup-candle 1000 1125 1250
```

基准、1.5 倍和 2 倍成本需要分别产生结果文件。验收工具不会把“未提供压力
结果”当作通过：

```bash
python tools/validate_beta_v3.py \
  --research research.zip \
  --validation-2026 validation-2026.zip \
  --stress 1.5x=stress-1.5x.zip \
  --stress 2x=stress-2x.zip
```

工具检查研究样本数、2026 收益/PF/Sharpe、15% 最大回撤、514 天回撤时长
对比、去掉最大 5 笔盈利后的结果、分组贡献和成本压力。Monte Carlo 继续
使用 `tools/validate_backtest.py`。

## 发布门槛

当前尚没有足够的多年真实研究数据、2026 独立结果、成本压力结果，也没有
V3 连续 30 天且至少 10 笔闭合交易的独立 dry-run。因此不能启动实盘，
也不能声称策略已经达到计划中的收益和回撤标准。

只有全部回测门槛通过后，才允许人工启动独立 V3 dry-run：

```bash
docker compose -f docker-compose.beta.yml up -d
```

30 天/10 笔验收通过仍只会形成“实盘候选”。`dry_run=false`、
`RISK_GUARD_VALIDATION_APPROVED=true` 和
`RISK_GUARD_LIVE_APPROVED=true` 必须在另一次人工审核中分别批准。
