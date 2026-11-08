# Hermes 交接书（监控 / 研究），2026-11-08

> 交易引擎是 `arbtwin`（systemd `arbtwin.service`）。**Hermes 不交易**，它负责监控、统计、
> 研究和汇报。任何下单、改配置、动 `.env` 的动作都必须先经过人确认。

## 1. 环境

| 项 | 值 |
|---|---|
| Hermes | `/root/.hermes`（源码安装），CLI `/root/.local/bin/hermes` |
| Provider | `alibaba-token-plan-cn`（阿里云 Model Studio Token Plan，CN 端点） |
| Base URL | `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` |
| Model | `deepseek-v4.1-flash-1017`（实测 200；同族可选 `deepseek-v4.1-flash`、`deepseek-v4-pro`） |
| 凭据 | `/root/.hermes/.env`（600）：`ALIBABA_TOKEN_PLAN_CN_API_KEY`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_ALLOWED_USERS`、`TELEGRAM_HOME_CHANNEL` |
| Telegram | bot `@po1yBotbot`；授权用户 `974319640`（@opclown_openclaw）；home channel 同 |
| 交付命令 | `hermes send --to telegram --file <body>`（bot-token 平台无需常驻 gateway） |
| 一次性问答 | `hermes -z "<prompt>"`（纯文本输出，适合脚本） |

## 2. 交易系统的可观测面（只读）

| 文件 | 内容 |
|---|---|
| `state/snapshot.jsonl` | 每 1.5s 一条：每标的 action / basis / band / reasons |
| `state/rounds.jsonl` | 每一轮的状态迁移（entry_quoting → … → flat / abort） |
| `state/alerts.jsonl` | warn / critical（单腿裸奔、撤单失败、权益读取失败…） |
| `state/positions.json` | 持仓账本（对账用） |
| `state/monitor_3h.log` | 3 小时健康 tick |
| `state/reports/hourly.log` | 每小时报告正文（与 Telegram 同步） |
| `state/deviations.jsonl` | **所有 bug / 不符合预期的记录** |

## 3. 已交付的监控程序

| 程序 | 频率 | 作用 |
|---|---|---|
| `scripts/hourly_report.py --hours 1 --send` | 每小时 | 汇总过去 1h：轮次数、完成/abort/IOC、实现 bps、胜率、逐标的 vs 期望、服务与快照健康；检出偏差写入 `deviations.jsonl` 并推送 Telegram |
| `arbtwin-monitor3h.timer` | 每 3h | 服务/内存/swap/磁盘、snapshot 新鲜度（>30min 告警）、矩阵、critical 计数 |
| `arbtwin-hourly-report.timer` | 每小时 | 运行上面的报告并推送 |

## 4. 交给 Hermes 的 autoresearch 程序（按优先级）

所有程序都**只读**交易文件，产出写到 `state/research/<name>.json` + 摘要进 `state/reports/`，
发现异常时用 `hermes send` 推送。

### A. 成交质量（p_fail / markout）
* 输入：`rounds.jsonl`（entry_partial 的时长、是否 abort）、`snapshot.jsonl`（同期基差）
* 指标：`p_fail`（一腿成交而另一腿在 deadline 内未成交的比例）、`markout_5m/30m`（成交后 5/30 分钟基差相对成交价的位移）
* 阈值：`p_fail > 0.5` 或 `markout_30m < -5bps` → 报警，建议改"一腿被动 + 一腿 IOC"

### B. 出场 deadline 研究
* 输入：`rounds.jsonl` 的 `exit_quoting`→`flat` 时长、是否触发 `escalation_used`
* 指标：被动出场完成时间的分布（p50/p90）、IOC 升级率、升级成本（bps）
* 阈值：升级率 > 20% 或中位出场 > 45min → 建议放宽 `exit_deadline_s` 或改目标（p50 → p25）

### C. 成本漂移（b\* 复核）
* 输入：每小时 `snapshot.jsonl` 的 `spread_cost_bps`；`config/symbols*.yaml` 的 `b_star_bps`
* 指标：实际 `fees + spreads`（滚动 24h 中位）vs 选票时用的 `b_star_bps`
* 阈值：实际成本 > 选票 b\* 的 1.3× → 该标的应重新筛选

### D. 逐标的期望跟踪（CUSUM）
* 输入：`rounds.jsonl` 的 `pnl_bps`，对照 `config/symbols*.yaml` 的 `net_per_round_bps`
* 指标：滚动 20 轮均值、胜率、累计偏差（CUSUM）
* 阈值：CUSUM 越界或滚动均值 < 期望的 50% → 降级/下线该标的建议

### E. 门禁否决普查
* 输入：`snapshot.jsonl` 的 `action=BLOCK` + `reasons`
* 指标：每个 gate 每标的每小时否决次数（session / mark 偏离 / 深度 / 薄盘口 / OI / min_edge）
* 阈值：单一 gate 连续 6h 否决 → 检查是否配置过紧（例如 mark 偏离阈值对小票过严）

## 5. 记录 bug / 预期偏离的统一格式

`state/deviations.jsonl`，每行：

```json
{"ts": 1794150000.0, "kind": "bug|expectation|ops|risk", "key": "abort_rate|under_expectation:SOXL|snapshot_stale|...",
 "detail": "人可读的一句话", "window_hours": 1}
```

已经内置的检测项（`scripts/hourly_report.py`）：服务不在 active、snapshot 停滞 >30min、
critical 告警、abort 率 >30%、IOC 升级率 >20%、某标的实现 bps < 期望 50%、24h 无任何轮次。

## 6. 硬性约束

1. 不碰 `arbtwin` 进程、不重启服务、不改 `.env`（除非用户明确要求）。
2. 不发任何交易指令；Hermes 没有交易所凭据，也不应该有。
3. 所有推送控制在 3500 字符内，数字优先，结论一行。
4. 报告必须同时落盘（`state/reports/`），Telegram 只是投递通道。
