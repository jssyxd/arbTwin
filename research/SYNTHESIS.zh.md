# 双交易所双腿价差收割：执行与再平衡方案（v1，2026-11-09）

> 仓库：`jssyxd/arbTwin`（OKX USDT 永续 ↔ Lighter USDC 永续）
> 日期：2026-11-09。本文只写可复现的事实与可执行的规则；未经证实的数字标 `[待验证]`。

## 0. 结论（先给答案）

1. **赚什么**：同一标的、两个独立撮合/独立 oracle 场所之间的短期报价错位（基差）。
   14 天实测：小时级 σ 4.1–7.4 bps，AR(1) 半衰期 0.8–1.7 h；p50 3–5 bps，p95 10–16 bps。
2. **成本决定形态**：OKX maker 2 bps / taker 5 bps，Lighter 当前 0/0。
   - 双腿 post-only：往返 ≈ 4 bps + 半价差；
   - 任一腿 taker：往返 ≈ 10–17 bps。
   因此：**常规路径以 post-only 为主；IOC 只用于 (a) 宽价差下的条件对冲、
   (b) deadline 兜底、(c) 风控**；市价只用于强平类风险。
3. **两腿不同步是常态，不是异常**：仓库 2026-11-08 机制回放 5 个标的只有 1 个完成双腿对冲，
   其余 4 个走 `ENTRY_PARTIAL → ABORT_UNWIND`。另一会话估计 `p_fail ≈ 0.13–0.2`，实测更高。
   → 方案必须把「单腿裸奔的成本」当作一等公民，而不是异常分支。
4. **核心修正**：补仓/再平衡的定价基准不是「A 的实时中间价 vs B 的实时中间价」，而是
   **「A 的边际成交价 vs B 的已成交均价」**，并要求**合并后的配对基差 ≥ 目标 b\***。
   公式见 §2。这一条同时解决「补仓后整体变差」和「来回振荡」。
5. **分批**：建仓与平仓都按 2–4 片、价格阶梯 + 时间窗推进；每片独立可撤、可对账；
   任何时刻未配对名义 ≤ `max_unhedged_notional_usd`（当前 150 USD）。
6. **NautilusTrader 1.219.0**：本机已安装；官方适配 OKX（post-only/reduce-only/IOC/FOK 均有），
   无 Lighter 适配；`OmsType.HEDGING`、`OrderEmulator`（OCO/OTO/OUO）、`SyntheticInstrument`
   可用。上游 **v2.0.0rc8 已有官方 Lighter 适配器**（post-only/reduce-only/IOC/GTD/原生 OCO），
   落地路径见 §8。
7. **「双向」的正确含义**：本方案做的是**符号对称的均值回归**——Lighter 贵则空 Lighter/多 OKX，
   Lighter 便宜则反过来；峰→谷、谷→峰都做（`wave.py` 的 `_direction()` 已按基差符号切换，
   v0.2.5 修过负基差被净 edge 门槛误杀的问题）。**不做**「价差突破后追趋势」的动量模式：
   现有证据只支持收敛（半衰期 0.8–1.7 h），动量模式需要另一套信号与成本模型，
   不在本方案范围内。

### 0.1 一页答案（针对「限价还是市价、怎么分批、怎么补」）

1. **建仓先选模式**：`basis − 主动腿成本 ≥ min_edge` → **hedge_now**（领先腿 post-only，
   成交后对冲腿 `LIMIT IOC`）；否则才用 **dual_maker**（双腿 post-only）。两者都不成立就不做。
   实践上没人用「双腿同时贴 touch 等成交」当默认路径，但纯 dual_maker 在 OKX 5 bps taker
   成本下仍是唯一能覆盖成本的形态——所以是**条件切换**，不是二选一教条。
2. **首腿成交后**：按 §2 用「已成交腿均价」算缺口腿的 `p_limit`。
   - 能挂在 `p_limit`（距 touch ≤5 bps）→ post-only 挂；
   - 挂不到但 `b_comb(p_exec) ≥ b*_cross` → `LIMIT IOC`，限价 `p_limit`（带 collar）；
   - 两者都不行 → 按 `b*_abort` 决定「主动完成」还是「只平未配对部分」。
3. **平仓**：`reduce-only` post-only 分片（0/2/5 bps 阶梯）；单片 deadline 到才允许一次 IOC；
   只有风控强平才用更激进的价格。
4. **补仓**：先补不足腿，补不了才减过量腿（先补后减）；补仓价**永远**由已成交腿均价与
   目标合并基差决定，不由两所同时刻 mid 决定。
5. **节奏**：`Δ > 0.5×上限` 才动，3 次确认、60 s 冷却、每个 episode 单向（COMPLETE 或 REDUCE），
   防止来回振荡。

## 1. 现状（代码事实）

- `arbtwin/wave.py`：单标的单槽状态机 `FLAT → ENTRY_QUOTING → ENTRY_PARTIAL → HEDGED →
  EXIT_QUOTING → EXIT_PARTIAL → FLAT`，另有 `ABORT_UNWIND`。
- 入场：`_entry_orders()` 两腿同时 post-only 贴各自盘口（卖挂 ask、买挂 bid）。
- 部分成交：只有 `ENTRY_PARTIAL` 持有裸腿；上限 `max_unhedged_notional_usd=150`，
  逆向止损 `max(60 bps, 2·σ_1h·sqrt(dt))`；超限或基差回到 p50/超时 → 被动 unwind。
- 出场：触发时冻结两腿价格（`freeze_px_on_trigger`），post-only 重挂；
  60 min 未平完 → **一次**带标记 IOC；风控强平才允许更激进。
- 成交判定：live 用**交易所持仓增量**（`reconcile.py`），不是盘口穿越；启动 seed 防重放。
- 已知缺口（仓库评审 + 本次设计审读）：
  - `abort` 轮 PnL 不进统计，失败成本不可见；
  - `p_fail` 先验 0.15 明显偏低（机制回放实测约 0.8）；
  - `rounds.jsonl` 未用于断点恢复；`client_order_index` 无幂等重发；
  - `wave.on_fill` 把 `filled_px` 设为**最后一笔**成交价，分片后必须改成名义额加权
    （`C += q·p`、`P̄ = C/Q`），否则补仓定价全错；
  - `round_pnl_bps` 直接用 Lighter 原始价与 OKX 价比较，未除 `unit_ratio`，比值偏离 1 时有偏；
  - `ENTRY_PARTIAL` 的补腿价来自「两边同时刻盘口」（`_requote` 贴 touch），
    正是要替换成 §2 的「已成交腿均价」口径。

## 2. 配对基差与补仓定价（核心数学）

记 OKX 腿价格 `pO`，Lighter 腿价格 `pL`（已按 `unit_ratio` 归一），
基差 `b = (pL − pO) / pO × 1e4`（bps）。正基差 = Lighter 贵 → 多 OKX / 空 Lighter。

### 2.1 已成交腿的「当前基差」

若 B 腿已成交数量 `q1`、均价 `p̄1`，则它对**当前** A 侧报价的基差是：

```
b1 = (pA_current − p̄1) / p̄1 × 1e4      # A=OKX、B=Lighter 时；反向取符号
```

注意：这里用 `pA_current`（要补的那条腿现在能拿到的价），不是 A 的 mid，更不是两边 mid 相减。
另注意符号口径：仓库原始基差固定为 `(pL − pO)/pO`；补仓数学里的 `b_comb` 按**交易方向**取正
（分母取多头腿），两者在负基差方向相差一个符号。实现时只保留一种口径并在入口处转换，
不能在同一函数里混用（v0.2.5 修过同类符号 bug）。

### 2.2 补 `q2` 后仍要满足目标

合并均价 `p̄ = (q1·p̄1 + q2·p2) / (q1 + q2)`。要求合并基差 `b̄ ≥ b*`（`b*` = 成本 + 最小边际），
解出边际腿最多能接受的基差：

```
b2* = b* + (q1 / q2) · (b* − b1)
```

- `b1 = b*`（首腿刚好达标）→ `b2* = b*`：补仓腿也要达标；
- `b1 < b*`（首腿偏差）→ `b2* > b*`：补仓腿必须更好才能把整体拉回目标；
- `b1 > b*`（首腿很好）→ `b2* < b*`：允许补在更差的价位，仍能保住整体目标。

对应的限价（以 OKX 买入为例，`b*` 为 bps）：

```
p2_max = pA_current / (1 + b2*/1e4)
```

**禁止补仓条件**：当前盘口可成交价 `p2_best > p2_max`（拿不到 b2*），且
`q2` 已无法再拆小到「补一点就有意义」的规模 → 不补，转 §5 的减仓或等待。
禁止用「A 的 mid 与 B 的 mid 同时刻对比」作为补仓依据：它忽略了两腿成交量的不对称，
会在 B 已偏离时继续加仓，把整体基差拖坏。

### 2.3 数值例（与 `unit_ratio=1` 近似）

`pL=100`，首腿 Lighter 空单成交在 `p̄1=99.9001`（b1≈10 bps），目标 `b*=12 bps`：

- 补同等数量（q2=q1）：`b2* = 12 + 1×(12−10) = 14 bps` → OKX 买价上限
  `100/(1+14/1e4)=99.8602`；
- 只补 1/4 数量：`b2* = 12 + 4×2 = 20 bps`（或按精确均价公式等号成立）；
- 若首腿在 20 bps 成交：`b2* = 12 + (12−20) = 4 bps`，允许补在较差价位。

精确均价公式与线性近似在上述量级误差 < 0.01 bps（已用脚本核对）。

### 2.4 三区决策（可直接实现）

把「已成交腿均价」作为约束，逐次判断缺口腿怎么下：

```
p_limit = 使合并基差恰好等于 b* 的缺口腿限价（§2.2 的精确公式）
p_touch = 缺口腿被动侧（买=bid，卖=ask）
p_exec  = 缺口腿可成交侧（买=ask，卖=bid）

Zone 1 CROSS：b_comb(p_exec) ≥ b*_cross → LIMIT-IOC，限价 p_limit（带 collar）
Zone 2 REST ：|p_touch − p_limit| ≤ 5 bps → post-only，价格取「安全方向」：
               买腿 min(p_touch, p_limit)，卖腿 max(p_touch, p_limit)
Zone 3 NO   ：以上都不成立 → 按 §2.5 选择「按 b*_abort 完成」或「被动 unwind」
```

注意方向：买腿取 `min(p_touch, p_limit)`，卖腿必须取 `max(p_touch, p_limit)`；
`research/execution/design.md` 的伪代码统一写了 `min`，实现时按方向修正，否则卖腿会挂在
低于下限的价位，合并基差反而不达标。

`b*` 分三档（同一标的分开标定）：`b*_pass`（被动补腿，≈ exit + maker 费 + 半价差 + funding）、
`b*_cross`（主动补腿，再加 taker−maker 与延迟成本）、`b*_abort`（完成比 unwind 更差的底线）。
Lighter 主动免费但慢 300 ms，OKX 主动快但贵 3 bps：按标的 σ 与 `b*_cross` 比较选主动腿。

`b*` 的计算必须用**总量/总名义**，不是边际一档：`b_comb = (P̄_S − P̄_L)/P̄_L × 1e4`，
其中 `P̄_L = Σq·p/Σq`。仓库现状 `on_fill` 只存最后一笔价（`filled_px = px`），
分片后必须改成名义额累加，否则均价错误。

### 2.5 不补仓的判定与两种收尾

同时满足以下三条才放弃补仓：被动侧拿不到（`b_comb(p_touch) < b*_pass`）、
`p_limit` 距 touch 超过阶梯上限（5 bps）、主动侧也拿不到（`b_comb(p_exec) < b*_cross`）。
此时二选一：
- `b_comb(p_exec) ≥ b*_abort` → 按 `b*_abort` 主动完成（仍用限价，绝不穿价）；
- 否则被动 unwind **只平未配对部分**，保留已配对仓位（比例 `p_fail` 的成本最小化）。

## 3. 订单类型决策矩阵

### 3.1 先选模式，再选订单类型

实盘只有两种入场形态，按**可执行净 edge**二选一（不是按偏好）：

| 条件 | 模式 | 订单 | 成本 |
|---|---|---|---|
| `basis − taker 成本(主动腿) − 滑点 ≥ min_edge` | **hedge_now**（先被动、后主动） | 领先腿 post-only；成交确认后，对冲腿 `LIMIT IOC`（collar） | ≈5–10 bps + 半价差 |
| 上述不成立，但 `basis ≥ b*_pass` | **dual_maker**（双腿被动） | 双腿 post-only + §2 补仓定价 | ≈4 bps |
| 两者都不成立 | 不做 | — | — |

实践证据：CBB 的 `takerRatioBuy/Sell`（0.05/0.10，买卖不对称）、TierZero「HL maker 先、
CEX taker 后」、XEMM「maker 报价由 taker 对冲价反推」都是同一逻辑——**价差足够大时才主动**。
arbTwin 目前只有 dual_maker；`engine._execute_plan` 的 ENTER 路径是 hedge_now 的雏形。
把 `hedge_now` 接进 wave 的 `ENTRY_QUOTING`，并加 `taker_entry_ratio_bps`，
是本方案与仓库现状的第一条行为差异。

**OKX↔Lighter 的特殊性**：OKX taker 5 bps + 跨价差 2–6 bps，主动对冲往返 10–17 bps，
而主流标的 p95 只有 10–16 bps。因此 hedge_now 只适用于宽价差标的（p95 ≥18–20 bps）
或瞬时错峰；**默认仍是 dual_maker**，否则会把 README §1.4 的负期望结论重新引入。

| 状态/目的 | OKX 腿 | Lighter 腿 | 理由 |
|---|---|---|---|
| 正常入场 | LIMIT post-only（贴 bid/ask） | LIMIT post-only | 往返成本 4 bps 是唯一正期望路径 |
| 首腿成交后的补腿 | LIMIT post-only；买腿 ≤ `p_limit`，卖腿 ≥ `p_limit`（§2.2） | 同（按 `unit_ratio` 换算） | 保持合并基差 ≥ b* |
| 补腿超时（如 90 min） | 撤单，转被动 unwind | 同 | 不再追价 |
| 正常出场 | LIMIT post-only（reduce-only） | LIMIT post-only（reduce-only） | 冻结触发价，阶梯 0/2/5 bps |
| 出场 deadline（60 min） | 一次 LIMIT IOC（带 collar） | 同 | 只付一次 taker，且限价保护 |
| 裸腿逆向 stop / 强平距离 | LIMIT IOC（reduce-only, collar） | 同 | 绝不 naked market；市价只留给系统性风险 |
| 风险强平（drift/liq） | MARKET 或激进 IOC | 同 | 已在风控白名单，事后审计 |

Collar：IOC 限价 = 对手价 ± `max_slippage_bps`（当前 6 bps，可随轮次放宽到 18 bps，
但必须有上限）；超过上限宁可留下仓位并 critical 告警，也不无限滑价。

**「等还是跨」的可执行判据**（OneChronos 2026 实测，8 个 pair）：
`cost_ratio = (滞后腿滑点 + |价差漂移|) / 领先腿成交时刻的滞后腿半价差`，
`cost_ratio > 1` 就该跨价差；实测约一半异步名义落在 >1 一侧，而任何 pair 的
「两腿 1 ms 内同步成交」占比都 < 10%（滞后腿滑点 1.5–14 bp）。
→ 不要追求「同步成交」，要追求「成交后的残差收敛成本 < 继续等待的期望成本」。

**被动挂单的取舍阈值**（Maglaras–Moallemi–Wang）：挂单优于吃单当且仅当
`P(T ≤ h) > Θ`，`Θ = (E[cleanup | T>h] − S/2) / (E[cleanup | T>h] + S/2)`。
在 SOL/ANT 类 4 tick 价差、未成交后常跳 1–2 tick 的盘口，`Θ ≈ 0.3–0.5`；
即「挂一档等成交」只有在 1 分钟内成交概率 > 30–50% 时才划算。
本仓库的 `exit_bps_p50` 假设了「回到 p50 就能成交」，这正是需要 tick 数据检验的地方。

## 4. 分批建仓（entry slicing）

- 目标名义 `N` 由 `_target_notional()`：`min(两所权益×杠杆×0.35, 1% 深度×10%, max_notional)`。
- 片数 `n=2..4`，第 i 片规模 `w_i`：默认等权；波动大/盘口薄时向首片倾斜（0.5/0.3/0.2）。
- **分批的真实价值是「中止期权」，不是「降冲击」**：Almgren 2005 拟合的临时冲击
  `K/σ = 0.142·r^0.6`；本场景 1 片 ≈ 分钟成交量的 1–2%，冲击 < 0.2 bps，而同侧价差
  2–8 bps —— 分批省不下冲击。它的价值是每片之后都能按当时的实际价差决定停手，
  把最坏情况从「全量在最差价差成交」降为「仅已成交片在最差价差成交」。
- 片数下限由最小下单额决定：`N = clamp(ceil(Q / ε_min), 1, 4)`，
  `ε_min = max(1 lot, min_quote/mid)`（Lighter `min_quote_amount` 今天为 $10）。
- 贴合连续解的条件：`N ≥ 3κT`（Almgren–Chriss 的 `κ = sqrt(λσ²/η)`，`1/κ` 为半衰期）；
  在本场景 κT 很小，`N=1–4` 已足够，更多切片只增加撤单/费率摩擦。
- 每片两条腿仍同时 post-only；**同一时刻只允许一片处于未配对状态**（其余片等前一片完成或撤销）。
- 片间价格阶梯：按 `b*` 递增 2–4 bps（越往后要求越好的价），或按时间窗 30–120 s。
- 任一片触发 §2 的「禁止补仓」→ 该片撤销，不降级为 taker。
- 全部片完成 → `HEDGED`；部分完成且超时 → 对已成交部分执行 §5 的被动减仓。

## 5. 分批平仓（exit slicing）

- 触发：`|b| ≤ exit_bps`（p50）、`hold_h ≥ max_hold_h`、收盘前 `force_flat`、逆向 stop。
- 片数 2–4，按 `reduce-only` post-only 挂在 0/2/5 bps 阶梯；每片独立 deadline。
- 已实现 PnL 按片记账：`(entry_basis − exit_basis_slice) × 片名义`；剩余仓位保留原入场基差。
- 单片 deadline → 该片一次 IOC；**不是整轮一次 IOC**，避免一次性把剩余仓位全部打穿。
- 风控强平：一次性、只针对风险片，不走阶梯。

工业界可直接抄的完成模式（Instinet WORK/MAKE/PART）：
- `Smart Complete`：在最大参与率内尽量完成（默认更保守）；
- `Must Complete`：允许突破参与率上限以完成，**但必须同时给硬价格约束**
  （主动档距最新价 ≤ 4 tick 或 2%，取较不保守者）；
- `Strict Min Part Rate`：实际进度落后于下限就立即跨价差；
- 若完成所需速率 > 40% → 暂停并转人工复核（硬护栏）；
- 连续追赶子单 ≤ 3× 常规子单，滑点上限 ≤ 3%。
本方案映射：`Must Complete` = 「到 deadline 后允许一次 IOC」，`4 tick / 2%` = collar，
`>40% 暂停` = 「连续 3 片未成交 → critical 告警 + 停止该标的」。

## 6. 动态再平衡（持仓期间）

监控量：`imbalance = qA·pA − qB·pB`（按名义，带方向），`b_now`（§2.1 口径）。

规则（按优先级）：
1. `|imbalance| ≤ δ`（δ = max(10% 名义, 一个最小下单额)）→ 不动，避免手续费磨损。
2. 超出 δ 且 `b_now` 仍满足 §2 的 `b2*` → 在**较小腿**补 `min(|imbalance|, 剩余片额)`，
   价格按 §2 计算；补完重算 `b̄`。
3. 超出 δ 但拿不到 `b2*` → **减较大腿**（reduce-only post-only），把 imbalance 拉回 δ 内；
   减仓不改剩余仓位的入场均价。
4. `b_now` 逆向超过 `adverse_basis_stop_bps`（25 bps）→ 两腿同时进入被动退出队列，
   按 §5 分批平；不补仓。
5. 每次补/减后设置冷却（如 5 s）与最小变动阈值，防止在同一价位反复触发。

**残差年龄闸门**：持有残差 `e` 的 1σ 成本为 `|e|·σ·√h`，立即消掉的成本为 `|e|·半价差`；
两者相等给出 `h* = (k·半价差/σ)²`（k = 几倍 σ）。以 σ_1h≈5 bps、半价差 1–2 bps 计，
`h*` 落在分钟级；超过 `h*` 仍不补/不减，就是在用风险换不存在的价差。
**过补禁止令**：补仓量只能由**已实现成交**（fill-to-fill）决定，不能用「计划量 − 当前持仓」
决定；后者在持仓读数异常时会退化成「把空头越补越大」（这正是另一条 HL 腿事故的机理：
HL 6/6 未平 → 回补逻辑去补 OKX，净空放大到 3.7× 权益）。

## 7. 风控与对账（不可省）

- 未配对名义 ≤ 150 USD；未配对时长 ≤ 90 min；单腿逆向 stop 按 σ 标定（§1）。
- **三态仓位语义（fail-closed）**：只有「显式开仓报告」和「显式 flat 报告」是权威；
  missing report / null quantity / 场所不公布持仓都 **不是** flat 证据（NautilusTrader
  reconciliation 规范）。解析失败必须进入 `UNKNOWN` 并阻止新开仓，不能折叠成 `q=0`。
- 敞口统计必须包含**在途挂单**（Cboe `agg_gcdt`/`abs_nntnl`；Knight Capital 案的违规事实
  之一就是 position limits 未计在途单）。
- 每轮 `round_id`、每片 `slice_id`、每次补仓的 `b1/b2*/q1/q2` 全部落盘（`state/*.jsonl`）。
- 幂等：`client_order_index` 必须在**重发时复用**（三层 key：venue+symbol+client_order_index），
  否则断线重试会变成双倍下单。
- live 成交只认持仓增量；启动对账；orphan 阻断新开仓；`client_order_index` 幂等重发（待实现）。
- abort 轮单独记 `abort_cost_bps`，否则失败成本不可见（仓库已知缺口）。

## 8. NautilusTrader 落地

已在本机与上游核实（2026-11-09）：

| 事实 | 证据 |
|---|---|
| 本机安装 1.219.0；上游最新为 **v2.0.0rc8**（2026-10-29，prerelease） | `import nautilus_trader`；GitHub releases API |
| v2 起官方提供 **Lighter 适配器**（Rust 核心 + Python 扩展；REST/WS、L2 签名、账户流、对账） | `docs/integrations/lighter.md` @ v2.0.0rc8 |
| Lighter 适配器支持：`post_only`、`reduce_only`、GTC/GTD/**IOC**（无 FOK）、MARKET/STOP_MARKET/STOP_LIMIT/MARKET_IF_TOUCHED/LIMIT_IF_TOUCHED、原生 OTO/OCO/OTOCO | 同上「Order capabilities」 |
| OKX 适配器支持：`post_only`、`reduce_only`、FOK/IOC；**执行客户端不翻译 OCO/bracket 列表** | `docs/integrations/okx.md` @ v2.0.0rc8 |
| 本机 1.219.0 可用：`OmsType.HEDGING`、`OrderEmulator`、`SyntheticInstrument`、`ContingencyType.OCO/OTO/OUO`、官方 OKX 适配器；**无 Lighter** | 本机枚举 |

**推荐路径（两条，按风险选择）**：

- **路径 A（推荐，改造成本最低）**：保留 arbTwin 自研引擎与 `wave.py`，先按 §2–§7 改造执行层；
  Nautilus 只用于**回测/仿真**（`ParquetDataCatalog` + `OrderEmulator`）与**未来迁移**。
  理由：现引擎已有 Lighter zk 签名、单位校准、机制闸门、持仓增量对账；直接重写风险高。
- **路径 B（中期）**：升级到 NT v2.0.0rc8，直接用官方 **OKX + Lighter 双适配器** +
  `OmsType.HEDGING` 重写 `TwoLegBasisStrategy`。需要补：
  1. 双腿状态机（`on_order_event` / `on_position_event` 驱动，不靠轮询）；
  2. §2 的补仓定价与 `DEGRADED` 再平衡（NT 没有现成 spread 策略）；
  3. 跨场所原子性：NT 不提供跨 venue 事务，仍要自己做残差状态机与 fail-closed 对账；
  4. OKX 的 OCO/bracket 不翻译 → 用 `OrderEmulator` 或两腿各自 reduce-only 条件单；
  5. Lighter IOC/GTD 与 300 ms speed bump 的延迟预算。

**不建议**：在本机 1.219.0 + 自写 Lighter adapter 上直接实盘。1.219.0 无 Lighter，
自写适配器要覆盖 zk 签名、nonce 流、订单状态对账，工作量与风险都高于路径 A 的执行层改造。

[详细模块映射与 P0/P1/P2 计划见 `research/nautilus/blueprint.md`。]

蓝图要点（已核对）：
- **双适配器同一 `LiveNode`**：OKX + Lighter 各自 `ExecutionClient`、独立 `account_id`
  （USDT / USDC 不共享保证金），策略内统一折算 USD 名义。
- **OMS**：策略与 OKX 用 `HEDGING`，Lighter 场所是净持仓；NT 虚拟持仓可按腿跟踪，
  `position_id` 用 `{symbol}-okx-leg` / `{symbol}-lighter-leg` 便于状态机映射。
- **表达方式**：post-only 挂单 + 定时 `modify_order` 重挂 + 超时撤单后发 `IOC`，
  这是策略层模式，不是单一订单类型；Lighter 条件单触发前不接受 IOC，OKX 无原生 GTD。
- **能力差异**：Lighter 原生 OTO/OCO/OTOCO；OKX 适配器**不翻译** OCO/bracket 列表，
  需策略自己撤兄弟单或用 `OrderEmulator`；两边都不支持 iceberg。
- **多 key**：arbTwin 的「maker key / taker key 分开」在 NT 里需多个 `LighterExecutionClient`
  （每个 `api_key_index` 一个），同一账户多 client 的 nonce 协调是**未验证项**。
- **回测**：`ParquetDataCatalog` + `BacktestNode`；maker 成交用「价格穿过」的保守模型，
  不模拟队列位置；300 ms speed bump 需自定义 `LatencyModel`。
- **明确不建议**：在 NT 1.219.0 上自写 Lighter 适配器实盘；先用 v2.0.0rc8 的官方适配器。

## 9. 开源参考

调研结论（`research/github/findings.md`，全部经 GitHub API/源码核验）：

1. **pranay123-stack/perp-funding-basis-arb**（MIT，paper-only）：最完整的双腿状态机。
   - `HedgeCycle` 用 `base_exposure()` / `hedge_ratio()` 而非原始数量做对冲比，兼容不同合约乘数；
   - 首腿选**更难成交的腿**（薄盘口/慢场所）先挂，第二腿按首腿**实际成交**缩量；
   - 部分成交进入显式 `DEGRADED → REBALANCE`：优先补不足腿，补不了才削减过量腿；
   - 退出：只让 lead leg 被动挂，hedge leg 在 lead 成交后立即 cross；超时才双腿 cross；
   - 盘口重挂注释值得抄：「A limit order left where it was placed is not a quote, it is a landmine.」
2. **counterfactual5/funding-arb**（MIT，11★，2026-11-09 仍在更新）：有 **Lighter + OKX 的
   真实 venue adapter**（`scripts/venues/lighter.py`，live-tested），
   `pure_futures_executor.py` 做 perp-perp 双腿、`_floor_qty()` 处理 lot size、
   `settle_mismatch_planner.py` 处理资金费结算周期错配；`cross_venue_executor.py` 用
   rollback + `naked` 告警。缺点：market order 为主，不适合 arbTwin 的成本结构。
3. **nautilus_trader**：OMS/OrderEmulator/多场所适配器齐全，但没有现成的双腿价差示例；
   v2.0.0rc8 起官方有 **Lighter 适配器**（见 §8）。
4. **hummingbot**：`ArbitrageExecutor` 是双腿 MARKET 同时发、只检查是否都成交，
   对限价基差交易过于粗糙。
5. freqtrade / jesse / passivbot：单场所，不适用。

对本仓库最重要的三条修正：
- **显式 `DEGRADED` 状态 + 先补后减**：把 `ENTRY_PARTIAL` 的「等另一腿」改成有记账的再平衡；
- **以 base exposure 计算对冲比**，不要假设 1:1 数量；
- **退出只让领先腿被动、对冲腿在领先腿成交后立即收**——但这条与 OKX 5 bps taker 冲突，
  因此在本仓库只能用于「净 edge 仍 ≥ 2 bps」的情形，否则维持双腿 post-only + 残差闸门。

**注意**：没有任何开源仓库实现「按已成交腿均价给补仓定价」；这正是本方案 §2 的增量。

## 10. 平台证据

### Kaggle（`research/kaggle/findings.md`）
- **没有**任何 notebook 处理「异步双腿成交 + 按已成交腿均价补仓」；整个 stat-arb 语料停在
  信号生成，执行语料是单市场。→ 本问题的答案只能自己建（合成 LOB 测试床）。
- 可复用：`powakadata/crypto-perpetual-funding-rates-and-open-interest`（资金费+mark+OI 长历史）；
  `jorijnsmit/binance-full-history`（1m K 线，用于离线重跑筛选漏斗）；
  `martinsn/high-frequency-crypto-limit-order-book-data`（1s、15 档，用于成交概率/深度闸门原型）。
- 可移植方法：OU 回归估计半衰期 `Δspread = a + b·spread_lag`，`half_life = −ln2/b`；
  比固定 p95 多一个时间轴，可用于「裸腿 deadline 前是否可能回归」的准入。
- 反模式：全样本 z-score（lookahead）、无多重检验校正的 cointegration 扫描
  （ETC 与所有资产"协整"是伪发现）、所有 pairs kernel 都不计成本。
- 结论：`backtest/replay.py` 的 fill-at-next-bar 对信号研究诚实，但对腿平衡问题不诚实；
  两者必须分开，不能拿它证明双腿执行可行。

### GitHub（`research/github/findings.md`）
见 §9。

### x.com / Medium / Reddit（`research/x-medium/findings.md`）
- **共识**：没有成熟玩家用「双腿同时贴 touch 的 post-only」作为生产入场。
  主流是「难成交/费率占优的腿先挂 maker，确认成交后再对流动腿主动对冲」；
  第二代补仓价由首腿实际成交价与目标配对价反推（FMZ `leg2Price = SUM_TARGET − leg1Price`；
  XEMM `maker_px = hedge_px·(1−taker_fee)/(1+maker_fee+margin)`）。
- **数字**：TierZero taker/taker 盈亏平衡 ≈4.5 bps/8h，目标价差 ≥10 bps；
  NeuralArb maker 往返 ≈7 bps，单边 taker ≈13–20 bps；Perpys 在 $10k/7d、4 次 taker 后
  只有 3/8 个双边 OI≥$10M 的标的覆盖费用——**taker/taker 在散户规模基本不可行**。
- **失败案例**：CBB 的 $1.1M 亏损来自**过期的对冲场所行情**，把幻影 delta「修正」成
  $120M 裸空；Polymarket 被动双腿案例把 38% 毛利亏在残差上。
- **可抄参数**：CBB `maxDelta=800`（按数量而非名义）、`cancelDelta=0.02`、
  `takerRatio` 买卖不对称；XEMM 60 s 撤旧单、利润偏离 ±3 bps 撤单、对冲事件队列满即停挂；
  funding-arb-bot 缺腿重试 3 次/60 s 后平掉已成交腿（复平成本 ≈0.15%）。
- **队列证据**：Daru（SSRN 7461901）实测 Bybit/Binance/HL 的 touch 挂单 10 s markout
  为 −0.39 ~ −0.84 bp，**半价差覆盖不了逆向选择**；第 5 档亏损更小但成交率只有 12%。
  → dry-run 的「价格穿过即成交」对 touch 挂单偏乐观，必须用真实 small-size 标定。

### arXiv（`research/arxiv/findings.md`，15 篇）
- **OU 半衰期** `τ½ = ln2/θ`；用 `Δb_t = α + β·b_{t−1}` 估 `θ = −β/Δt`。论文经验区间
  τ½≈2–30 min 才"可交易"；仓库小时线测出 0.8–1.7 h（48–102 min），两者不矛盾但**说明
  小时线把盘中回归平滑掉了**——必须用 tick 数据重估，不能直接套论文阈值。
- **入场/出场带**（Leung–Li 2014）：OU + 比例成本下存在对称 exercise band；
  `entry_bps` 对应上界、`exit_bps` 对应下界，**止损与止盈耦合**（止损越宽 → 最优止盈越低）。
  仓库现在把 `exit_bps`、`adverse_basis_stop_bps` 独立配置，应联动标定。
- **成本自由边界**（Xing 2019）：比例成本产生 no-trade 区，宽度随 `√c` 增长；
  当带宽小于自由边界时，即使均值回归完美也是负期望——解释了为什么 BTC/ETH 的 2–5 bps
  基差不可做。
- **A–S 库存偏斜**（Guéant–Lehalle–Tapia 2011；Feys 2026）：`δ± = 1/k ∓ γσ²q`；
  Feys 证明 A–S 的 γ 与 Cartea–Jaimungal 的 φ 满足 `φ = γσ²/2`，只需标定一个参数。
  这直接支持 §6 的 inventory skew。
- **限价 vs 市价**（Cont–Kukanov 2012；Figueroa-López 2017）：最优拆解依赖队列、费率、
  成交概率；负漂移且期限超过临界 `t0` 时，**挂深一点优于贴一档**——与 Daru 的实证一致。
- **永续/股票永续**：Gupta–Polson 2026 给出 funding 贴现的定价表示；Seo et al. 2026 证明
  premium-index funding 无法复制股息，**分红股永续在除息日存在可预期漏损**，RWA 标的
  （AAPL/NVDA 等）的期望 edge 必须扣掉这一项。
- **空白**：没有论文处理双场所异步成交、300 ms speed bump、USDT/USDC 基差、1h vs 8h 资金费、
  tick/lot 约束；这些只能靠本仓库实测。

## 11. 验证计划

1. 用 tick 级录制（`monitor.jsonl` 已有）估：双腿同时成交概率、单腿成交后 N 秒内补腿成功率、
   各价位 `p_fill`、markout（成交后 1/5/30 min）。
2. 用 §2 公式回放 2026-11-08 的 5 个 abort 轮，计算「若按 b2* 补仓/减仓」的净额。
3. 纸面/dry-run 跑 1 周，验收：完成率、`abort_cost_bps`、单次实现净额、`p_fail` 实测值。
4. 达标线（建议）：完成率 ≥ 40%，单次净额 ≥ 2 bps，abort 成本 ≤ 0.5×单次毛利。

## 12. 运行记录（本次多模型调研）

| 作业 | 模型 | 平台 | 产物 |
|---|---|---|---|
| `glm-x-medium` | glm-5.3 | x.com / Medium / Reddit | `research/x-medium/findings.md` |
| `qwen-arxiv` | qwen3.8-max | arXiv / SSRN / Semantic Scholar | `research/arxiv/findings.md` |
| `deepseek-github` | deepseek-v4-pro | GitHub | `research/github/findings.md` |
| `glm-kaggle` | glm-5.3 | Kaggle | `research/kaggle/findings.md` |
| `qwen-executor` | qwen3.8-max | 本仓库 + 工业/学术执行文献 | `research/execution/design.md` |
| `deepseek-nautilus` | deepseek-v4-pro | NautilusTrader 文档/源码 | `research/nautilus/blueprint.md` |

调度：Herdr（`herd-dispatch`），每作业独立 pane；工作完成后停止子 agent，仅保留 captain pane。
每个作业的原始简报与 pane 转储在 `.herd-dispatch/jobs/<name>/`（gitignored）。