# V1-V6 历史文件归档

> 整理日期：2026-07-31
> 归档性质：只读历史资料，不是可直接启动的部署目录。

## 为什么建立这个目录

本目录集中保存已经停止作为独立候选运行的 V3-V6 启动栈及其冻结证据，
减少部署目录顶层的歧义。V1 没有独立 Compose/配置，V2 仍是 CloudCone
服务器当前实际运行的 dry-run 策略，因此 V2 的启动文件没有移入归档。

## 目录内容

| 路径 | 内容 |
|---|---|
| `docker-compose.beta.yml` | V3 历史 Compose |
| `docker-compose.beta-v4.yml` | V4 历史 Compose |
| `docker-compose.beta-v5.yml` | V5 历史 Compose |
| `docker-compose.beta-v6.yml` | V6 历史 Compose |
| `runtime/config.beta.json` | V3 历史 dry-run 配置 |
| `runtime/config.beta-v4.json` | V4 历史 dry-run 配置 |
| `runtime/config.beta-v5.json` | V5 历史 dry-run 配置 |
| `runtime/config.beta-v6.json` | V6 历史 dry-run 配置 |
| `docs/` | V3、V4 原始专题说明 |
| `baselines/` | V3-V6 冻结策略、配置、Compose 的哈希与结果摘要 |

这些 Compose 文件保留原始内容用于审计，移动后不再具备原目录下的相对挂载
结构，不能从本目录直接执行 `docker compose up`。

## 为什么 V1-V6 策略源码没有搬入归档

当前 V7 不是独立实现，源码继承链如下：

```text
V7 -> V6 -> V5 -> V4 -> V3 -> V2 -> V1
```

因此 `runtime/strategies/` 中的 V1-V6 文件仍是 V7 的运行依赖，不属于可删除
文件。将它们直接移走会导致 `OkxCrossAssetBetaV7` 无法导入。对应测试和研究
工具也保留在原位置，用于回归验证与重现历史结论。

## 当前保留在顶层的运行入口

- `docker-compose.yml` + `runtime/config.json`：服务器当前运行的 V2 dry-run。
- `docker-compose.beta-v7.yml` + `runtime/config.beta-v7.json`：V7 独立候选入口，
  仅供离线验证，当前禁止启动和实盘。

完整的版本关系、交易逻辑、风险参数、研究结果和发布状态见
`../../STRATEGIES_V1_TO_V7_CN.md`。
