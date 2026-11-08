# 机制验证跑：每个标的跑完一轮（2026-11-08）

## 方法

`scripts/mechanics_run.py`：对每个标的读入真实小时线（OKX 收盘 / Lighter mark），
按 **5 分钟子步**线性插值，合成双侧盘口（半价差 = 实测价差），用**出厂阈值**
（`entry_bps_p95` / `exit_bps_p50`）驱动 `wave` 状态机，成交规则与 dry-run 一致
（价格穿过挂单价才成交），假时钟推进 `time.time()` 让裸腿停留时间真实。

## 结果（5/5 都回到 FLAT）

| 标的 | 入场 \|basis\| | 结局 | 净额 | 关键路径 |
|---|---:|---|---:|---|
| SOXL | 12.6 | abort | — | entry→partial→(基差回落)被动 unwind |
| COIN | 10.6 | abort | — | entry→partial→(基差回落)→unwind 超时→单次 IOC |
| LIT | 11.6 | abort | — | entry→partial→(90min 到期)→unwind 超时→单次 IOC |
| VVV | 12.2 | **hedged→exit** | **−30.66 bps** | 入场→对冲完成→4h 超时触发→出场 60min 未成交→IOC |
| GRAM | 19.3 | abort | — | entry→partial→(90min 到期)→unwind→IOC |

结论：状态机在 5 个标的上都能"进入→持有→退出→回到 FLAT"，但**只有 1/5 走到双腿对冲**。

## 本轮发现并已修的问题

1. **裸腿止损没有波动率标定**（严重）：固定 60bps，而 SOXL 的 `sigma_1h = 244bps`、
   LIT 155bps —— 任何正常小时波动都会触发 abort。现在
   `stop = max(60, k·sigma·sqrt(dt))`（`k=2`，`dt` 为裸腿已存在的时长），并把 `sigma_1h`
   从 symbols 注入 `WaveParams.leg_sigma_1h_bps`。
2. 监控脚本里 python 引号被 heredoc 吃掉 → 每 3 小时的 tick 只记健康、不记矩阵（已修）。

## 本轮记录但未修的问题

3. **abort 轮的 PnL 不进统计**：`round_pnl_bps()` 需要双腿 exit，abort 只平一条腿，
   于是报告里 pnl=0 —— 失败成本不可见。应单独记 `abort_cost_bps`。
4. **双腿被动成交率偏低**：5 次入场里 4 次只成交一条腿，`entry_deadline_s=90min` 到期后
   被动 unwind（再等 60min 才 IOC）。这印证了另一会话的 `p_fail ≈ 0.13–0.2`，但我们的
   双挂模型没有为"另一腿不成交"预留成本，`net = capture − b* − p_fail·b*` 里的
   `p_fail` 可能低估（实测 0.8）。
5. **出场 deadline 是真正的瓶颈**：VVV 唯一一次走完的轮，出场 60 分钟没成交，最后 IOC
   亏 30.66bps。`exit_bps_p50` 目标是"回到中位数"，但被动单回到中位数的概率远低于直觉。
6. 仿真以小时线插值，仍比 tick 级乐观（真实成交更需要价格穿过）。

## 市场现实（决定了"每个跑一轮"能不能做）

* 2026-11-08 是**周日**：SOXL / COIN 是 `us_equity`，会话门禁整周末关闭 —— 实盘这周末
  不可能跑这两只。
* LIT / VVV / GRAM 是 24/7，但 b\* 口径下净额为负（+0.8 / −1.2 / −8.3 bps），
  **不应该跑**。
* 因此"5 个标的各跑一轮"在真实市场上无法在周末完成；本轮用确定性回放完成了机制验证，
  实盘只能从周一开始、且只做 SOXL / COIN。

## 监控

`arbtwin-monitor3h.timer`（`OnCalendar=*:0/3`）每 3 小时写一条 tick 到
`/opt/arbtwin/state/monitor_3h.log`：服务状态、内存/swap/磁盘、snapshot 新鲜度
（>30min 告警）、每标的三列（basis / band / action）、critical 计数与最近三条。
