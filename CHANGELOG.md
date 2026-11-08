# CHANGELOG

## v0.3.0 — 实盘开启：持仓增量对账（2026-11-08）

- `arbtwin/reconcile.py`：live 的唯一成交来源是**交易所持仓增量**（不是盘口穿越）。
  账本持久化到 `state/positions.json`；启动 `seed()` 采纳现有仓位（避免把历史仓位当成交）；
  delta 的方向即买卖方向，价格取交易所 `avg_entry`。
- `_assert_live_is_safe()`：`ARBTWIN_FILL_SOURCE=positions` + 两所私钥齐全才允许 live，
  否则仍拒绝启动（并 `exit 2`，不产生崩溃循环）。
- 启动清理：两所 `cancel_all`，杜绝重启后昨日的 GTC 单继续可成交。
- **sizing 修正**：之前用交易所最大杠杆（OKX 50–100x）算名义，$59 账户会被算成 $3000；
  现在用 `limits.target_leverage`（`ARBTWIN_TARGET_LEVERAGE`，默认 3x），并可用
  `ARBTWIN_MAX_NOTIONAL` 给单腿封顶（首轮 $15）。
- 平坦账户的启动检查从 warn 降为 info。

## v0.2.6 — 数据契约透镜：OKX 档位单位、会话标签、收盘窗口（2026-11-08）

- **OKX `/market/books` 的 size 是"张"，不是币**（严重）：`Book` 之前直接当基础币用，
  深度被放大 `1/ctVal`。实测：XAU `ctVal=0.001` → **1000×**（$23.3B vs 真实 $23.3M），
  VVV 0.1 → 10×，BTC 0.01 → 100×。已在 `OkxVenue.book()` 统一乘 `ctVal*ctMult`。
- **陈旧的 session 标签**：`config/symbols.yaml` 里 LIT/VVV/GRAM 仍是 `us_equity`（分类器修过，
  文件没重生成）→ 每天 16.5 小时只减仓。已按分类器重贴标签（三者 → crypto）。
- `screening` 的 entry 现在同时写 `basis_p95_rth_bps` / `basis_p50_rth_bps`（此前只在行对象里）。
- **收盘窗口策略**：新增 `no_new_entry_min_before_close=60` / `force_flat_min_before_close=20`
  —— 开盘跳空实测 43.8bps（典型步长的 65 倍），不允许在收盘前 1 小时新开一轮，
  也不允许持仓跨过收盘（20 分钟内强制走向 EXIT）。crypto 与已闭市返回 `inf`（没有"收盘"可争）。

## v0.2.5 — 策略透镜：基差方向对称性 + 文档命令与宇宙一致（2026-11-08）

- **只有一半行情会被交易（真 bug）**：`wave.decide` 的触发用 `abs(basis)`，但传给它的
  `maker_net` 用的是**带符号**的 basis（`spread.py`），于是所有负基差（Lighter 便宜 →
  多 Lighter / 空 OKX）都被 `min_edge_bps` 拒掉。改为 `maker_net = |basis| − 成本`，
  并用一个"正负基差对称"的单测锁定。
- `screening` 的 `to_symbol_entry` 补 `max_fee_bps_for_breakeven`（此前只在行对象里），
  并加回归测试：一次 `arbtwin screen` 必须产出 wave 需要的全部字段，否则例行重扫会把
  引擎悄悄降级成只观察。
- 部署文档 3.1/3.3 的命令从不存在的 `SOXL,GRAM,VVV` 宇宙改为当前的 `COIN,SOXL`，
  名义从 $400 改为 $12（Lighter 最小下单 $10 是硬地板）。

## v0.2.4 — 统计透镜 + 运维透镜的第一轮修正（2026-11-08）

### 收益口径（推翻旧结论）

- 每轮捕获是 `E[basis | basis≥p95] − 出场阈值`，**不是**入场基差。`event_study()` 现在
  在 screening 里做这件事（按 session 取样本、4 小时窗口、超时按市值计），输出
  `capture_bps / completion / events / trades_per_day / net_per_round_bps`。
- 净额公式：`capture − b* − p_fail·b*`（`p_fail` 先验 0.15）。旧的 "单次毛利 8~11bps、
  净 3~6bps" 是错的（把 p95 当毛利、半价差只算一次）。
- 阈值改为**分 session** 计算（`basis_p95_rth_bps` / `basis_p50_rth_bps`，需要 ≥120 个
  交易时段样本），因为引擎只在参考市场开市时挂单；混合 24/7 样本的尾部会被闭市时段污染。
- 运行时 gate 也补上价差：`maker_net = basis − maker_fee − (spread_okx + spread_lt) − funding`
  （旧版少扣 2–6 bps，会在 screener 已判定亏损的价位入场）。

### 运维与静默失败

- **Lighter 权益字段读错**：`assets[].balance` 对已入金账户读作 `0.000000`（实测
  `margin_balance/collateral = 27.629041`）→ 权益 0 → sizing 0。改为账户级
  `total_asset_value → collateral → available_balance → margin_balance`，并加单测。
- sizing 下限可配（`limits.min_round_notional_usd`），低于下限**告警**而不是静默 `continue`；
  wave 模式下没有 `entry_bps_p95` 会 critical 告警（否则引擎退化成只观察）。
- `screen` 的输出现在包含 wave 需要的全部字段（`entry_bps_p95` / `exit_bps_p50` /
  `band_override_bps` / deadlines / `b_star_bps` / spreads / capture / net），
  例行重扫不会再悄悄把生产配置写坏。
- live 被拒时 CLI 打印一行并 `exit 2`（不再抛栈）；systemd 单元加
  `StartLimitIntervalSec=60 / StartLimitBurst=5`，不会 5 秒一次崩溃循环。

## v0.2.3 — 对抗式评审修正（2026-11-08）

一组 reviewer 透镜发现的 8 个缺陷，全部有测试：

- **post-only 价位错误**：卖单挂在 best bid、买单挂在 best ask（都可立即成交），
  live 下会被两所拒绝/自动撤销 → 拆 `passive_px()` / `marketable_px()`，仅 IOC 用后者。
- **live 成交是虚构的**：`poll_fills` 用盘口穿越推断成交且不看渠道回报；撤单失败被吞。
  现在 `simulate_fills` 只在 dry-run 生效，撤单失败告警，**live 启动直接拒绝**，
  直到订单状态对账落地（宁可拒绝运行，也不凭空生成 PnL）。
- **orphan 对账每 poll 执行**：会把自己 wave 的持仓当孤儿并永久阻断 → 只在启动执行，
  开盘中的 round 不参与判断。
- **ABORT_UNWIND 的成交记错腿** → 状态机卡死；已加路由与测试。
- **新轮不重置** `escalation_used / frozen / round_id` → 第二次升级死挂陈旧价；已重置。
- **gate/notional 失败会停掉持仓管理** → 非 FLAT 一律继续 poll_fills/step。
- **`max_unhedged_notional_usd` 无执行点**、ENTRY_PARTIAL 无逆向止损、forced 平仓用市价
  → 裸腿双阈值 abort；forced 改带 collar 的 IOC 限价。
- Lighter `client_order_index` 改为单调计数（同毫秒不再撞号）；幂等重发仍待办。

## v0.2.2 — Lighter 双 key 拆分（挂单 / 吃单）（2026-11-08）

- `LighterCredentials` 增加 `api_private_key_taker` / `api_key_index_taker`；
  `LighterVenue._trader(taker=...)` 按需选择：`post_only` 走挂单 key，`ioc` 走吃单 key
  （Lighter 每把 key 的交易严格串行，300ms 的 taker 不能和挂单共用一把）。
- 没有配置吃单 key 时自动回退到单 key，不报错（两个单测覆盖）。
- `docs/LIVE-accountbot2.md`：实盘接入清单（三条硬阻塞、$31.47 的规模上限算术）。

## v0.2.1 — wave 执行模式 + 第一台实机部署（2026-11-08）

- `strategy: wave`（`arbtwin/wave.py` + `wavetrader.py`）：一轮一清、纯限价、
  两腿必须同时出清；一腿成交后继续等另一腿（不市价对冲）；出场冻结触发时点价；
  仅在 deadline / 风险止损时允许一次 IOC。12 个单测覆盖状态机，2 个覆盖引擎端到端。
- dry-run 成交代理改为"**穿过才成交**"（挂在盘口的单不会自我成交），避免回放/演练高估成交。
- `config/symbols.starter.yaml` 增加 `entry_bps_p95` / `exit_bps_p50`（14 天实测分位）与
  两腿 deadline、max hold。
- 实机部署记录写进 `docs/DEPLOYMENT.zh.md` 第 8 节（swap 1G 持久化、systemd、nautilus 3.12 独立 venv、
  1C/1G 的资源约束与两个部署坑）。
- README 补 C 栏位：`0.3.1 为什么只能限价`（4 bps vs 10–17 bps 的算术）。

## v0.2.0 — 实盘手册、资金费单位修正、v1 starter universe（2026-11-08）

### 一、单位修正（100 倍错误，必须写死）

Lighter 的 `funding_rate` 是**百分比/小时**（`0.0012` = 0.0012%/h ≈ 10.5% APR），
OKX 的 `fundingRate` 是**小数/8 小时**（`0.0001` = 0.01%/8h ≈ 10.95% APR）。
此前 `MarketStats.funding_apr_pct` 用同一口径处理两所，导致 Lighter 的资金费被放大 100 倍
（3.5% 被算成 350%，并进一步让入场成本被高估 ~8bps）。现在 `venues/lighter.py` 在入口
统一折算成"小数/区间"，并加了两个回归测试锁死口径。

三方交叉验证：`/api/v1/funding-rates` 里 Lighter 的 BTC 标注 `9.6e-05`（≈10.5% APR，
与 Binance/Bybit/Hyperliquid 同量级）↔ WS `funding_rate` `0.0012`(%/h) ↔
`base_interest_rate 0.0100 / 8` —— 三者一致。

### 二、新增能力

- `max_hold_h`（默认 4h）：基差半衰期实测 0.8–1.7h，超时强制被动退出，避免"看着它"。
- `band_override_bps`：按标的锁定入场阈值（`SymbolState.band_override_bps`），
  让上线首日的阈值可预期、不随实时 σ 抖动。
- `config/symbols.starter.yaml`：v1 起步宇宙（SOXL/GRAM/VVV），附 14 天证据字段。
- `classify_session` 修正：GRAM/VVV/LIT/TRUMP 等 24/7 代币此前被误判为 `us_equity`，
  会导致夜间锚冻结、引擎退化成只减仓。
- `docs/DEPLOYMENT.zh.md`：核心理念（赚什么钱 / 为何成立 / 黑天鹅与对应架构）、
  v1 参数、三阶段上线、监控阈值、故障预案。

### 三、实盘口径（14 天实测，用于校验）

| 标的 | 基差 σ | p95 | ≥12bps 小时占比 | 半衰期 | 资金费中位年化 | 1% 深度 |
|---|---:|---:|---:|---:|---:|---:|
| SOXL | 6.34 | 10.8 | 4.5% | 0.76h | 28% | $4.55M |
| GRAM | 7.38 | 15.6 | 9.0% | 0.86h | 10.5% | — |
| VVV | 5.72 | 11.4 | 4.5% | 1.70h | 14.4% | — |

保守预期（1 次/天/标的、净 3–6bps）对 $2,000 权益约年化 5%–15%；乐观（2 次/天、净 6–8bps）
15%–35%。**MSTR 因 2026-11-11 MSCI 指数决定被排除**（事件风险，不是我们要赚的钱）。

## v0.1.0 — OKX ↔ Lighter 双塔基差引擎首版（2026-11-08）

首个可运行版本：把 @totofdn 近 21 条推文里可工程化的 8 条结论（P0-1 … P2-8）
落到一个**只做 OKX ↔ Lighter 两所**的独立仓库，并在此之前先回答了"换 venue 是否可行"。

### 一、可行性研究结论（先于代码）

**市场重叠（2026-11-08 现场拉取）**

| venue | 可比市场 | 备注 |
|---|---:|---|
| OKX `SWAP`（USDT 结算，live） | 528 | 含 AAPL / NVDA / TSLA / ASML / AMZN / COIN / ANTHROPIC / XAU |
| Lighter perp（active） | 215 | 含代币化股票、外汇、商品、pre-IPO |
| **配对交集** | **181**（`1000*` 归并后 186） | 远宽于 HL↔Lighter 的 HIP-3 覆盖 |

**单位一致性（7d 小时收盘，比值 = Lighter/OKX）**

| 标的 | 比值均值 | CV | 收益率相关 | 判定 |
|---|---:|---:|---:|---|
| ETH | 1.0001 | 0.011% | 0.9997 | 同一标的 |
| AAPL | 0.9987 | 0.033% | 0.9983 | 同一标的 |
| NVDA | 0.9988 | 0.031% | 0.9989 | 同一标的 |
| ANTHROPIC | 10.38 | 4.07% | 0.80 | **不同标的**，剔除 |

**经济性（基差 p95 vs 往返成本）**：ETH ≈2bps、BTC ≈5bps、NVDA ≈7bps、AAPL ≈9bps；
`hedge_now` 成本 ≈10bps+滑点延迟，`dual_maker` ≈4bps。结论写进 README 第 1.4 节：
主流 1:1 标的用 taker-hedge 做不出正期望，edge 只存在于中低流动性标的、激励期、
或纯被动双腿模式。**这条结论导致本版本新增了 `dual_maker` 执行模式（见下）。**

### 二、P0-P8 落地

- **P0-1 `calibration.py` + `spread.py`**：`same_instrument / ratio_stable /
  reject_divergent` 三态判定（CV ≤0.5%、收益率相关 ≥0.99）；同时输出
  `basis_bps`（看到的）、`executable_edge_bps`（跨价差能拿到的）、
  `maker_net_bps`（纯被动能拿到的）三个口径，避免把"看着有 8%"当成"能赚 8%"。
- **P0-2 `mechanics.py`**：mark 窗口（quote TTL < 窗口/2）、mark cap/floor、
  mark-index 偏离、OI 利用率、1% 深度、强平距离、延迟成本 `σ·√(t/1h)`。
  **放弃**了 OI/深度比作为硬门槛：实测 ETH 为 ≈1、NVDA ≈40，单一阈值要么排除 ETH
  要么形同虚设；改用"移动 25bps 需要的名义金额"作为可观测的薄盘口门槛。
- **P1-3 `anchor.py`**：`tradfi > RFQ > 两 venue index 中位数`；两 index 分歧超过
  45bps 直接拒绝定价；股票类闭市后锚冻结并降级为"只减仓"。
- **P1-4 `band.py`**：`band = clamp(max(8, k·σ_basis, fee·multiple), 8, 600)`，σ_basis<3bps 退化为 `fee×1.5`，
  避免零波动标的每 tick 交易；σ 极大时封顶不失效。
- **P1-5 `fees.py`**：`effective_bps = nominal − incentive_value`，按订单类型覆盖，
  往返成本对平仓腿取两侧较贵者（保守），并计入结算/跨稳定币摩擦。
- **P2-6 `issuer_risk.py`**：7 条硬红旗（递归抵押、单一 oracle、可调供给上限、
  发行方即借贷对手方、激励即退出流动性、pre-IPO 主张、无 index）+ 软flag缩规模。
- **P2-7 `manipulation.py`**：移动 25bps 的成本、单边深度不对称，按 √ 比例放大
  入场带宽与延迟成本（不直接 veto，veto 归 mechanics）。
- **P2-8 `exitqueue.py`**：带 deadline 的退出队列（0/2/5bps 被动阶梯 → 临近 deadline
  升级 IOC → 仅风控止损允许市价），替代原先"20→40→80→200bps 阶梯市价"的急躁退出。

### 三、新增能力

- **`dual_maker` 执行模式**：两条腿都 post-only（Lighter 0 费 + OKX 2bps），
  `_poll_maker` 每轮检测穿越成交；单腿成交超 `max_unhedged_ms` 立即转被动退出并 CRITICAL 告警。
  这是把"OKX taker 5bps 吃掉全部基差"这一现实写进架构的结果。
- **`hedge_now` 模式**：Lighter post-only 领先 → OKX IOC 对冲，对冲失败自动 reduce-only 反手，
  不会留下裸腿；失败路径有独立测试覆盖的告警。
- **`screening.py`**：两所价格 + 200 档盘口 + 结构风险 + 两种执行模式的净收益，
  输出可直接被引擎消费的 `config/symbols.yaml`（含证据字段）。
- **`monitor.py`**：8 个监控信号、JSONL 落盘、可选 webhook。
- **`backtest/replay.py`**：因果回放（t 决策 → t+1 成交，费用双边计入）+ walk-forward 分折。
- **资金费计入定价**：`funding_cost_bps = carry/8760×1e4×expected_hold_h`，方向相关的净 carry
  （Lighter 小时结算、OKX 8 小时结算分别年化）。实测 14 天中位：权益类 3.5%、加密类
  10.5%、SOXL 28%（v0.2.0 修正了此处的 100 倍单位错误，见上）。
- **CLI**：`doctor / screen / monitor / run / backtest / calibrate`；`run` 在非 dry-run 时
  必须显式 `--i-know-what-im-doing`。
- **46 个纯逻辑测试**，覆盖校准三态、退化带宽、费率口径、机制门槛、矩阵方向、
  双腿计划（含对冲失败反手、maker 双腿成交、单边成交转退出队列）、锚冻结、回放因果性、
以及筛选漏斗的通过/拒绝分支。

### 四、实测修正记录（写下来防止回退）

| 现象 | 原因 | 修正 |
|---|---|---|
| 入场带宽算出 54–72bps，永远不成交 | 用**价格** σ 计算基差带宽（价格 σ 是基差的 10–20 倍） | 改用基差自身 σ；`symbols.yaml` 增加 `basis_sigma_bps` |
| WS 一直 `disconnected` | `ThreadedWs.run` 与适配器的 `asyncio.run` 嵌套 | 去掉外层 `asyncio.run`，由适配器独占事件循环 |

| 现象 | 原因 | 修正 |
|---|---|---|
| 1% 深度严重低估 | OKX `books?sz=5` 只有 5 档 | REST 取 200 档（AAPL $12.4k → $736k，59 倍） |
| Lighter 深度取不到 | REST 字段是 `remaining_base_amount`，WS 是 `size` | `_parse_book` 同时兼容 |
| 筛选用 5 档盘口把 ETH 判成"盘口陈旧" | 快照时间与 gate 时间差 | gate 只校验盘口非空，新鲜度在拉取侧控制 |
| `OI/深度` 阈值把 ETH 也拦掉 | 该比值对主流标的本就 >50 | 降级为监控指标 |
| 资金费被当成"单腿过热"直接 veto | 单腿高费率在反方向上是收入，不是成本 | 改为按持仓方向计净 carry，并折入成本 |
| 单边成交后退出方向反了 | 退出侧从 `st.side` 标签推导，而不是从真实持仓 | `_open_exit` 改为按每条非零腿生成 ticket（两个测试覆盖） |
| 12 标的轮询一次 30s+ | 串行 24 次 REST + 失败重试 30s | snapshot 改为线程池并发取数；深度读 6s 超时 + 1 次重试；实测单轮 17s→（含启动） |

### 4.1 深度口径修正（P0-2 追加）

| 观察 | 数字 | 结论 |
|---|---|---|
| OKX `books?sz=5` 算"1% 深度" | AAPL $12.4k | tick=0.29bps，5 档只看到 **1.5bps**，视野差 40 倍 → 低估 59 倍 |
| `sz=200` | $736k（覆盖 58bps） | 仍**未**覆盖 100bps，是下界而非真值 |
| `sz=400` | $765k/$859k（覆盖 218/230bps） | 唯一能"完整覆盖 1%"的档数 → 默认改为 400 |
| Lighter REST 250 单 vs WS 快照 | $1,116,714 / $1,116,770（ratio 1.00） | Lighter 书稀疏（88 价位跨 5000bps），REST 已完整覆盖，**不需要**为深度改用 WS |
| BTC 400 档覆盖 | 仅 5.3bps（tick 0.013bps） | 任何档数上限都无法测 BTC 的 1% 深度 → 改成 **每 bp 名义密度** 闸门 |

因此：`Book.depth_covered()` / `depth_density_usd_per_bps()` 成为闸门口径，
`depth_complete` 一并输出；OKX 拆出 `quote()`（WS 5 档，只做定价）与 `book()`
（REST 400 档，只做闸门）两套缓存，避免快路径污染慢路径。

同期修掉一个静默 bug：OKX `/market/ticker` **不返回 `idxPx`**，原代码把 index 回退成
mark，导致 `mark-index` 偏离恒为 0（P0-2 的一个闸门形同虚设）。现在 index 来自
`/market/index-tickers?quoteCcy=USDT`（全市场一次拉取）。

### 五、首次全量筛选实测（2026-11-08）

175 个候选 → 12 个通过（831 秒，含 0.12s 节流）：
`GRAM SOXL VVV COIN LIT MSTR XPT EWY TRUMP MRVL ORCL SNDK`，
比值 CV 0.044%–0.076%、收益相关 0.991–0.9996。163 个被拒的原因分布：
成本高于基差 p95（主流 1:1 标的）、移动 25bps 成本 < $50k（薄盘口）、
比值/相关性不成立（SHEIN/NOK/GEV/XCU 等）。

### 六、已知限制

- 成交确认用轮询 + 盘口穿越，未接 Lighter 账户级 WS（最坏延迟 = 一个 poll 周期）。
- 回放不模拟队列位置与逐档吃单，只回答"基差是否覆盖成本"。
- OKX 实际返佣档位需人工写入 `config/venues.yaml`（接口不返回个人档位）。
- Lighter 的 mark 计算窗口未公开，`mark_window_s=300` 是保守假设，需按实际观察校准。