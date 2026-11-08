# 部署与实盘手册 · v1（OKX × Lighter 基差回归）

> 本文是 `arbTwin` 从零到实盘的唯一操作手册。**先读第 0 节**，再看参数。
> 所有参数都来自 2026-11-08 的现场实测（14 天小时线 + 实时盘口 + 资金费历史），
> 来源文件与命令在每节末尾标注，可复验。

---

## 0. 核心理念：我们到底在赚什么钱

### 0.1 三笔钱，按重要性排序

**① 报价错位（basis）** —— 主引擎
同一个标的、同一个单位、两个**独立撮合**的场所（OKX 的中心化撮合 + Lighter 的 zk-rollup
订单簿）。它们的中间价不会始终相同：订单流不对称、库存不平衡、两边做市商不是同一批人。
实测 14 天，12 个标的的基差小时级 σ 为 **4.1–7.4 bps**，AR(1) 半衰期 **0.8–1.7 小时**
—— 错位会回归，而且回归很快。我们挂被动单去吃这段错位，不去追速度。

**② 费率非对称** —— 决定"能不能赚"
Lighter 目前每个 perp 的 `maker_fee = taker_fee = 0.0000`；OKX Lv1 是 maker 2 bps /
taker 5 bps。同样的交易，全部被动（两边都挂）的往返成本约 **4 bps**，一旦用 taker
对冲就是 **10 bps**。而基差 p50 只有 3–5 bps、p95 是 10–15 bps —— **用 taker 对冲必然亏钱，
这是这个策略最硬的一条物理约束**。所以 v1 只跑 `dual_maker`。

**③ 资金费（carry）** —— 顺路的补贴
Lighter 权益/加密永续的资金费**长期为正**（14 天里 99–100% 的小时为正），因为它的
`base_interest_rate` 参数本身就是正的（加密类 0.01% / RWA 类 0.0032%，按官方公式除以 8
后逐小时支付）。实测中位年化 **3.5%（权益类）～28%（SOXL）**，均值被少数极端小时抬高到
**9%–39%**。关键点：**"Lighter 报贵 → 做空 Lighter"这个方向，恰好也是收资金费的方向**，
所以 carry 是顺路的，不是独立的赌注。

> 单位口径（容易错 100 倍）：Lighter 的 `funding_rate` 是**百分比/小时**（`0.0012` =
> 0.0012%/h ≈ 10.5% APR），OKX 的 `fundingRate` 是**小数/8 小时**（`0.0001` = 0.01%/8h ≈ 10.95% APR）。
> 代码里由 `venues/lighter.py` 统一折算成"小数/区间"，见 `models.MarketStats.funding_rate`。

### 0.2 为什么大部分时候成立

1. **是同一份风险**（可证）：单位比值 CV 0.04%–0.08%、收益率相关 0.991–0.9996（14 天小时线）。
   不是"看起来像"，是数学上同一资产。
2. **错位来自摩擦，不是来自定价错误**：跨所套利要搬 USDC/USDT、要过链上充提与 KYC、
   要各自抵押品、要各自 API 速率。这些摩擦让专业资金无法把 5 bps 的错位秒级抹平，
   于是错位能活几十分钟到几小时 —— 正好是 maker 挂单的猎场。
3. **我们赚的是"提供流动性的报酬"，不是"预测方向"**：两边同时挂单，成交即对冲，
   不承担净方向敞口；收益来自"别人急着成交"。
4. **下行被结构限制住**：单标的限额、单腿漂移止损、被动退出队列、账户回撤停机 —— 
   最坏情况是"少赚 + 小额摩擦"，不是"爆仓"。

### 0.3 什么时候不成立（以及系统里谁在守这一关）

| 破裂方式 | 表现 | 系统里的防线（代码位置 / 参数） |
|---|---|---|
| **单位被重新定义** | 两所同一 ticker 的比值突然变化（实测反例：ANTHROPIC 比值 10.4、CV 4.07%、相关 0.80） | `calibration.py`：CV ≤ 0.5%、收益率相关 ≥ 0.99，否则整只标的剔除；`screening` 每次重扫都重算；`band_override` 只允许在已校准前提下设定 |
| **venue 规则变更** | 费率改了、mark 口径改了、资金费参数改了、变成 reduce-only | 启动时快照 `orderBookDetails`/`instruments`；`fees.py` 用实时费率而不是常量；`monitor` 的 `reject_rate > 20%` 告警；每天 `doctor` 复核 |
| **单边流动性枯竭** | 一边盘口变薄，maker 单被吃穿 | `mechanics.py`：1% 深度密度 ≥ `min_depth_usd_1pct/100`；`move25bps_cost` ≥ $50k；单笔名义 ≤ 深度 10% |
| **预言机 / mark 机制异常** | mark 与 index 长期偏离，或贴近 venue 的 mark 上限 | `mechanics.py`：`mark_divergence_max_bps`（OKX 80 / Lighter 100；原值 30/40 会把健康的 3x ETF 判死 —— 实测 SOXL 在两所都有 60–70bps 的 mark-index 差：OKX 的 mark 跟期货、index 跟 ETF 净值；Lighter 的 mark 用 impact price，本身带溢价参数）；`mark_cap_px`/`mark_floor_px` 距离 < 8% 禁止该方向；`anchor.py`：两所 index 分歧 > 45 bps 直接拒绝定价 |
| **稳定币脱锚（USDC/USDT）** | 两腿权益计价不同步，PnL 出现非交易性波动 | 结构性防线：**两所各自独立保证金、各留缓冲**（见 3.2 资金分配）；`settlement_cost_bps` 计入摩擦；不因稳定币价格加杠杆 |
| **极端行情两所不同步（跳空/插针）** | 一边先跳，另一边还没跳；或一边被强平 | `engine.manage_risk`：单腿漂移 12% 强制双平；`reconcile`：一条腿归零立即平掉孤儿仓；`exitqueue`：18% 强平距离触发被动减仓 |
| **强制平仓级联** | venue 的 OI 相对盘口过大，一根针扫掉一片仓位 | `mechanics`：OI 利用率 ≤ 25%（Lighter 有公开 OI 上限）；`cascade_proxy` 作为监控指标；杠杆上限 3x、强平距离先算后下 |
| **自身 bug / 断线** | 该撤没撤、该平没平 | `ARBTWIN_DRY_RUN` 闸门（非 dry-run 必须 `--i-know-what-im-doing`）；order TTL 撤单；`state/*.jsonl` 全量落盘；55 个单测；kill switch 见 6.1 |
| **venue 破产 / 冻结提币** | 一个所的钱出不来 | 单所资金 ≤ 总资金 50%；每周做一次小额提现演练（见 5.3）；不把两腿都放在同一个 venue 体系 |
| **监管 / 地区限制** | OKX 地区限制、KYC 被限 | 部署前确认所在地区可用性；两所账户分离；保留纸面记录 |

### 0.3.1 为什么"只能限价"——用数字说死

你说得对，而且这不是风格问题，是算术问题：

| 方式 | 单边费用 | 再叠加跨价差 | 一次往返总成本 | 对比基差 p95 |
|---|---:|---:|---:|---|
| 两腿都 post-only（Lighter 0 + OKX maker 2bps） | 0 + 2 | 0（挂单不跨） | **4 bps** | 10–16 bps → **赚** |
| 一条腿 IOC/市价（OKX taker 5bps + 跨 1 档） | 5 + 2~6 | 2~6 | **10–17 bps** | 10–16 bps → **打平到亏** |

所以系统的默认行为是：**任何常规路径都不允许吃单**。只有两类例外，且都会被记录/告警：

1. 出场超过 `exit_deadline_s`（默认 60 分钟）仍未平完 → 一次 IOC；
2. 风险止损（单腿漂移 ≥ 3–12%、强平距离 < 18%）。

其余情况一律 post-only；如果挂不上（crossing）就重挂或撤单，绝不改成吃单。

### 0.4 一句话总纲

> 我们赚的是**两所独立订单流造成的短期报价错位**，用**零费的被动腿**收租，
> 顺路收 Lighter 的正向资金费；所有"可能让这件事不成立"的机制，都在代码里有一个
> 明确的、会**拒绝交易**的闸门，而不是一个"注意一下"的注释。

---

## 1. v1 标的：为什么是 SOXL / GRAM / VVV

### 1.1 14 天实测证据（小时线，400 根）

| 标的 | 单位比 CV | 收益相关 | 基差 σ | p50 | p95 | 14d 最大 | \|基差\|≥12bps 的小时占比 | 回归半衰期 | 资金费中位（年化） |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **SOXL** | 0.069% | 0.9996 | 6.34 | 3.7 | 10.8 | 34.8 | 4.5% | **0.76 h** | **28%** |
| **GRAM** | 0.061% | 0.9995 | 7.38 | 4.6 | **15.6** | 30.8 | **9.0%** | 0.86 h | 10.5% |
| **VVV** | 0.060% | 0.9993 | 5.72 | 3.5 | 11.4 | 16.8 | 4.5% | 1.70 h | 14.4% |
| COIN | 0.050% | 0.9983 | 4.77 | 2.7 | 10.4 | 14.9 | 2.0% | 0.95 h | 6.1% |
| ORCL | 0.052% | 0.9992 | 4.94 | 3.2 | 9.1 | 17.9 | 2.0% | 1.20 h | 3.5% |
| SNDK | 0.045% | 0.9994 | 4.10 | 2.5 | 8.2 | 18.2 | 1.0% | 0.80 h | 23.7% |
| MRVL | 0.049% | 0.9992 | 4.57 | 3.3 | 8.5 | 18.0 | 2.0% | 1.10 h | 3.5% |
| XPT | 0.054% | 0.9959 | 5.10 | 3.6 | 10.0 | 14.4 | 1.0% | 1.70 h | 3.5% |
| EWY | 0.055% | 0.9959 | 5.18 | 2.7 | 9.7 | 35.1 | 3.0% | 0.80 h | 3.5% |
| TRUMP | 0.054% | 0.9990 | 5.02 | 3.5 | 9.8 | 17.6 | 2.0% | 0.70 h | 10.5% |
| MSTR | 0.055% | 0.9983 | 5.90 | 3.6 | 12.4 | 28.5 | 6.0% | 1.30 h | 3.5% |
| LIT | 0.064% | 0.9994 | 6.04 | 4.1 | 11.4 | 27.6 | 5.0% | 1.60 h | 10.5% |

**入选理由（宽松度优先）**：p95 最宽 + 触发频率最高 + 盘口最厚 + 事件风险最低。

* **SOXL**：1% 深度实测 **$4.55M**、移动 25bps 成本 **$678k**，是这批标的中最厚的；
  资金费中位 **28% 年化**（14 天里 99% 的小时为正）。模板：半导体 3x ETF 永续，双向都跟同一指数，无单边事件。
* **GRAM**：p95 **15.6 bps**、9% 的小时超过 12 bps —— 触发频率最高，宽容忍度的首选。
* **VVV**：半衰期 1.7h（回归慢一点，但 σ 稳、盘口干净），资金费 14.4% 年化。

### 1.2 刻意排除的标的（以及原因）

| 标的 | 排除原因 |
|---|---|
| **MSTR** | **2026-11-11 MSCI 指数纳入/剔除决定**（多家媒体在 10 月中下旬报道 MSCI 的新资格规则与 11-11 决定，变动 11-30 生效）。这是单一事件风险，不是我们赚的那类钱 → 至少等到 11-12 再评估。 |
| **LIT** | Lighter 自家代币。它的资金费/上下架/激励规则随时可能被运营方调整，属于"规则风险最高的标的"，二期再放。 |
| SNDK / MRVL / ORCL / EWY / XPT / TRUMP | 基差 p95 只有 8–10 bps，扣掉 4 bps 往返 + 2 bps 结构性摩擦后利润太薄，不值得占用调试精力。 |

> 新闻面核对（2026-11-08 检索）：MSTR 的 MSCI 风险是当期唯一明确的"日历型事件"；
> Lighter/OKX 侧无影响标的可用性的公告。每次重扫 universe 前都应重做这一步。

---

## 2. v1 参数（可直接抄）

### 2.1 资金与仓位

| 项 | 值 | 依据 |
|---|---|---|
| 总本金 | **$2,000** | 试验资金，两个所各一半 |
| OKX（USDT） | $1,000 | 只做**多腿**与 USDT 保证金 |
| Lighter（USDC） | $1,000 | 只做**空腿**与资金费收入 |
| 每标的每腿名义 | **$400** | 1% 深度的 0.01%（远小于 10% 上限） |
| 同时持仓标的数 | **2**（v1 建议先 1 个） | `max_open_symbols: 2` |
| 杠杆 | **3x**（两个所都设 cross 3x） | 保证金 $267/腿 → 两标的占用约 27% |
| 单标的硬上限 | $500 名义 | `max_notional_usd: 500` |
| 账户回撤停机 | 权益回撤 **3%** | 约 30 次"最坏单次"连续失败 |

### 2.2 入场（双挂，纯被动）

| 项 | SOXL | GRAM | VVV |
|---|---:|---:|---:|
| 入场阈值 `band_override_bps` | **12.7** | **14.8** | **12.0** |
| 挂单价格 | 富侧贴 bid、廉侧贴 ask（0 bps） | 同 | 同 |
| 二次加价阶梯 | 1 / 2 bps | 同 | 同 |
| 重挂周期 | 5 s | 5 s | 5 s |
| 未成交撤单 | 120 s | 120 s | 120 s |
| 同一时刻每标的挂单 | 1 买 + 1 卖（两所各一） | 同 | 同 |

> 最终生效的带宽 = `band_override_bps × 薄盘口惩罚系数`（P2-7，最高 4×）。也就是说薄盘口
> 仍会强制放宽，override 只保证"下限可预期"。
>
> `band_override_bps` 由 `max(12, 2σ)` 得出并写在 `config/symbols.starter.yaml` 里，
> 引擎读该字段（`SymbolState.band_override_bps`），不再用实时 σ 重算 —— 保证上线首日
> 阈值稳定、可预期。

### 2.2.1 一轮的完整生命周期（`strategy: wave`）

你的规则被实现成一台**每标的单槽状态机**（`arbtwin/wave.py`）：一轮没出清，绝不开始下一轮。

```text
FLAT ──|basis| ≥ p95──► ENTRY_QUOTING ──一腿成交──► ENTRY_PARTIAL
  ▲                          │                          │
  │                 (基差回到 p50)                (另一腿成交)
  │                          ▼                          ▼
  └── ABORT_UNWIND ◄── 撤单 + 被动平掉已成交腿          HEDGED
        │                                                 │
        └────────(两腿都平)──────── FLAT ◄── EXIT_PARTIAL ◄── EXIT_QUOTING
                                              (一腿先平)   (|basis| ≤ p50 或 4h)
```

四条硬规则（都有单测锁定）：

1. **不重叠**：`round.state != FLAT` 时，该标的不会挂任何新的开仓单；
2. **等另一腿**：一条腿成交后，另一条腿继续贴盘口挂（post-only），不市价追；等不到
   （默认 90 分钟）或基差先回到 p50 → 撤单 + **被动**平掉已成交腿（`ABORT_UNWIND`）；
3. **按触发时点价平仓**：出场触发时冻结两腿的限价（`freeze_px_on_trigger`），不追价；
4. **只允许一次升级**：出场 60 分钟仍未平完 → 允许一次 IOC（带标记，计入成本），之后回到
   被动挂单。这是"活着"的成本，不是常规路径。

阈值直接来自 14 天实测分位，写在 `config/symbols.starter.yaml`：

| 标的 | `entry_bps_p95` | `exit_bps_p50` |
|---|---:|---:|
| SOXL | 10.8 | 3.7 |
| GRAM | 15.6 | 4.6 |
| VVV | 11.4 | 3.5 |

> 注意：挂单成交与否由**对手方**决定。写成 p95/p50 是"在错位的极值挂被动单、在分布中枢
> 平掉"，不是"到价即成交"。引擎只能决定挂在哪、挂多久、什么时候撤。

### 2.3 出场（全部被动，除强平保护）

| 触发条件 | 动作 | 参数 |
|---|---|---|
| 基差回到 p50 | 双向 post-only 平仓 | SOXL 3.7 / GRAM 4.6 / VVV 3.5 bps |
| 已捕获 ≥ 8 bps | 立即被动平仓 | `exit_ratio` 逻辑的等价实现 |
| 持仓超过 **4 小时** | 被动平仓（不再"看着它"） | `max_hold_h: 4.0` |
| 逆向基差 ≥ 25 bps | 被动队列优先，60 s 未成交升级 IOC | `exitqueue.escalation_bps` |
| 单腿价格漂移 ≥ 3% | **强制双平**（唯一允许市价的路径之一） | `emergency_stop_loss_pct` 建议 v1 设 0.03 |
| 强平距离 < 18% | 立即减仓 | `min_liq_distance_pct` |

### 2.4 预期收益（把话说死，便于上线后对照）

假设：每天每标的 1 次双边成交（14 天样本里 GRAM 有 9% 的小时 ≥12bps，约 2 次/天；
SOXL/VVV 约 1 次/天，取保守值 1 次）。

```
单次毛利 = 入场基差(12~15bps) − 两所半价差之和(2~4bps) ≈ 8~11 bps
单次净利 = 毛利 − 往返费用(4bps) − 结构性摩擦(1bps) ≈ 3~6 bps
$400 名义 → 单次 $0.12~0.24
每天 2 次（两标的各 1 次）→ $0.24~0.48/天
资金费补贴：$400 名义 × (3.5%~28% APR) / 365 ≈ $0.04~0.31/天
合计：约 $0.3~0.8/天 → 对 $2,000 权益 ≈ 年化 5%~15%
```

**这是保守口径**（只算 1 次/天/标的、只算 3–6 bps 净额）。乐观情形（2 次/天、净 6–8 bps）
是 年化 15%~35%。**任何声称"月翻倍"的方案在这个基差分布下都是错的** —— 这条写在这里，
是为了让你在上线第一周能用实数校验，而不是靠感觉。

> 关键校验：如果你的实测"单次净利"长期 < 2 bps，说明你的成交质量不如预期
> （多半是半价差算多了，或吃到的是"坏流"），此时应提高 band 而不是加仓。

---

## 3. 上线步骤（30 分钟 → 3 阶段）

### 3.1 阶段 0：只读验证（≈30 分钟）

```bash
cd /opt/arbTwin && . .venv/bin/activate
export ARBTWIN_PROXY=            # VPS 直连则留空

arbtwin doctor                                    # 两所可达 + 费率 + 机械参数 + universe 自检
arbtwin screen --only SOXL,GRAM,VVV --days 14 \
        --out config/symbols.starter.yaml          # 用最新 14 天重算证据
arbtwin monitor --iterations 20 --interval 5      # 观察 100 秒，确认矩阵/告警正常
ARBTWIN_DRY_RUN=1 ARBTWIN_SYMBOLS=config/symbols.starter.yaml \
        arbtwin run --iterations 200 --interval 2  # 决策级 dry-run
```

合格标准（全部满足才进下一阶段）：

* `doctor` 两个 venue 都是 `ok: true`，3 个标的 `unit_ratio` 都 OK；
* `monitor` 里 `basis_z` 有值（说明历史窗口在累积），无 `critical`；
* dry-run 的 `state/snapshot.jsonl` 里，每轮都有 3 个标的的 `action`/`net_bps`/`band_bps`，
  且 `action` 会在 `MAKER`/`WAIT` 之间切换（说明阈值不是死的）。

> ⚠️ **本 build 只支持 dry-run。** `ARBTWIN_DRY_RUN=0` 会在启动时被 `_assert_live_is_safe()`
> 拒绝（CLI 打印 `refusing to run: ...` 并 `exit 2`），因为成交仍靠盘口穿越推断、
> 尚未接交易所订单状态。systemd 单元已加 `StartLimitIntervalSec=60 / StartLimitBurst=5`，
> 不会变成 5 秒一次的崩溃循环。解除条件：venue 订单状态轮询 + `client_order_index` 幂等落地。

### 3.2 阶段 1：单标的、最小仓（1 天）

```bash
# 只留 SOXL，$150/腿
# config/symbols.starter.yaml 里临时把 GRAM/VVV 注释掉
ARBTWIN_DRY_RUN=0 ARBTWIN_SYMBOLS=config/symbols.starter.yaml \
  arbtwin run --interval 2 --i-know-what-im-doing
```

观察三件事：① 双边成交是否在 120s 内配对；② 单次实现净额（`trades.jsonl` 的
`expected_net_bps` vs 实际 basis 变化）；③ 有没有出现单腿裸奔（`alerts.jsonl` 里的 critical）。

### 3.3 阶段 2：加满 3 标的（3 天）

把名义提到 $400/腿、并同时开 2 个标的。每天做一次 5.4 的 checklist。

### 3.4 阶段 3：例行

* 每周一次 `arbtwin screen --days 14` 重扫（市场结构会变，标的会进出）；
* 每周一次小额提现演练（USDC 从 Lighter 提回、USDT 从 OKX 提回），确认通道可用；
* 每周核对一次实际资金费收入 vs 预期（`state/monitor.jsonl` 的 `funding_carry`）。

---

## 4. 部署（VPS）

### 4.0 这台机器的物理限制（1 核 / 1 GB / 10 GB / Debian 12 / Python 3.11）

| 约束 | 现实 | 对策 |
|---|---|---|
| 内存 960 MB | 单进程跑 arbTwin 足够（~80 MB），但**不能**和 NautilusTrader 同机同跑（import 就要 300–500 MB） | 两个 venv 分开；服务只挂 `arbtwin`；nautilus 只做回放/研究 |
| Python 3.11.2 | `nautilus_trader 1.231` 要求 **≥3.12**，且只发 `manylinux_2_35` 的 cp312+ 轮子（189 MB） | 用 `uv python install 3.12` + 独立 venv，不做源码编译（1 核编译 Rust 会跑死） |
| 磁盘 8 GB 可用 | nautilus 轮子 + 依赖 ≈ 400 MB；swap 1 GB | 够用，但不要 `git clone` nautilus 的完整历史（`--depth 1`） |
| 网络 | 我们无法假设这台机器能直连两所 | 部署后先跑 4.6 的两条 `curl` 检查；不通就 `ARBTWIN_PROXY=` |
| 单核 | `arbtwin screen` 全量 175 标的会占满 CPU 十几分钟 | 只跑 `--only`；全量扫描放本地/另一台机器 |

### 4.1 前置

* Ubuntu 22.04/24.04 x86_64、Python ≥ 3.10（`tzdata` 已随包安装）；
* 能直连 `api.okx.com` 与 `mainnet.zklighter.elliot.ai`（含 `wss://`）；
* 两所账户各自完成 KYC / 充币：OKX 充 USDT（TRC20 最便宜），Lighter 充 USDC（其 L1 桥）。

### 4.2 安装

```bash
sudo useradd -r -s /usr/sbin/nologin arbtwin
sudo mkdir -p /opt/arbTwin /var/log/arbtwin && sudo chown -R arbtwin: /opt/arbTwin /var/log/arbtwin
sudo -u arbtwin git clone https://github.com/jssyxd/arbTwin.git /opt/arbTwin
cd /opt/arbTwin
sudo -u arbtwin python3 -m venv .venv
sudo -u arbtwin .venv/bin/pip install -U pip
sudo -u arbtwin .venv/bin/pip install -e '.[live]'
sudo -u arbtwin cp .env.example .env && sudo -u arbtwin chmod 600 .env
```

### 4.3 OKX 凭据

后台 → API → 新建：权限 **Read + Trade**（**不要 Withdraw**）、绑定 VPS 出口 IP。

```dotenv
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_API_PASSPHRASE=...
OKX_SIMULATED=0
```

### 4.4 Lighter 凭据（zk 签名，与 CEX 完全不同）

```bash
# 1) 查你的 account_index
curl "https://mainnet.zklighter.elliot.ai/api/v1/accountsByL1Address?l1_address=0x你的地址"
# 2) 用 SDK 注册 API key（一次性；L1 地址签名，私钥不要放进仓库）
python - <<'PY'
import lighter
c = lighter.SignerClient(url="https://mainnet.zklighter.elliot.ai",
                         account_index=<account_index>,
                         api_private_keys={4: "<api_private_key_hex>"})
c.create_api_key()
PY
```

```dotenv
LIGHTER_ACCOUNT_INDEX=...
LIGHTER_API_KEY_INDEX=4
LIGHTER_API_PRIVATE_KEY=...
```

> **务必拆 key**：`4` 挂单/撤单、`5` 主动腿、`6` 转账。同一个 key 的交易严格串行，
> 一个慢交易会把后面所有交易一起拖住（post-only 无 speed bump，taker 有 300ms）。

### 4.5 systemd

```bash
# 这台机器上一律先 dry-run（没有 key 时也只会 dry-run）
sudo cp deploy/arbtwin.service /etc/systemd/system/arbtwin.service
sudo systemctl daemon-reload && sudo systemctl enable --now arbtwin
sudo journalctl -u arbtwin -f
```

`deploy/arbtwin.service` 默认 `ARBTWIN_DRY_RUN=1`，**先这样跑够一天**，再改成 0 并加
`--i-know-what-im-doing`（`ExecStart` 里改）。

---

## 5. 监控与运维

### 5.1 八个信号（`state/monitor.jsonl`，每轮一条）

| 信号 | 正常 | 告警阈值 | 含义与动作 |
|---|---|---|---|
| `basis_z` | \|z\| < 2 | \|z\| > 4 | 溢价离开 30 天分布；暂停该标的新开仓，人工看是否有机制变化 |
| `net_bps` | 持仓时为正 | 持仓时 < 0 且持续 30 分钟 | 这笔在付钱持有；考虑提前按"逆向 25bps"规则退出 |
| `depth_density_usd_per_bps` | > 200 | < 200 | 盘口变薄；降低名义或停该标的 |
| `depth_complete` | true | false 且密度下降 | 档数不够（多见于 BTC 这类密 tick）；看密度，不看绝对深度 |
| `move_25bps_cost_usd` | > $40k | < $40k | 一根针能扫掉你；停该标的 |
| `oi_utilization` | < 10% | > 25% | venue 结构性拥挤；不加仓 |
| `ws_age_s` | < 5 s | > 30 s | 行情陈旧；引擎会自动停开仓 |
| `reject_rate` | 0 | > 20%（近 50 单） | venue 改了规则/权限/最小下单量；立刻人工介入 |

`arbtwin monitor` 的输出里有两列费用，别混：

```
symbol        basis      z    hedge    maker    band  h_fee  m_fee  action
SOXL          -3.8b   0.00   -18.9b    -8.6b   12.7b  10.0b   4.0b    WAIT
```

* `hedge` = `hedge_now` 模式的净额（含 10 bps 往返费用），**v1 不交易这一列**；
* `maker` = `dual_maker` 模式的净额（含 4 bps 往返费用），**v1 只看这一列**；
* `basis` 是看到的基差，`band` 是触发阈值（本行 12.7 = `band_override_bps`）。

### 5.2 每天 5 分钟 checklist

```bash
# 1) 有没有 critical
jq -r 'select(.level=="critical") | [.ts,.scope,.message]|@tsv' /opt/arbTwin/state/alerts.jsonl | tail
# 2) 今天成交与实现净额
jq -r '[.ts,.symbol,.mode,.expected_net_bps]|@tsv' /opt/arbTwin/state/trades.jsonl | tail -20
# 3) 当前基差矩阵
arbtwin monitor --iterations 1
# 4) 两所余额与持仓（各自独立核对，别只看一个所）
#    OKX: /api/v5/account/balance   Lighter: /api/v1/account?by=index&value=...
```

### 5.3 每周

* 一次小额提现演练（两所各 $20）；
* 重扫 universe（`--days 14`）；
* 复核 `state/basis14d.json` 式的统计：σ、p95、半衰期是否变化（结构变了就换标的）。

---

## 6. 故障预案

### 6.1 Kill switch（30 秒内全平）

```bash
sudo systemctl stop arbtwin                 # 先停引擎，避免它继续挂单
# 再手动/脚本按 venue 平仓：OKX /api/v5/trade/close-position，Lighter 用 SDK reduce-only IOC
# 最后核对两所持仓都为 0（_position_okx / _position_lighter 对应的 API 查询）
```

### 6.2 场景 → 动作

| 场景 | 立刻做 | 之后做 |
|---|---|---|
| 单腿裸奔（一条腿成交、另一条没成） | 看 `alerts.jsonl` 的 `UNWIND FAILED`；引擎会自动 reduce-only 反手 | 检查该标的盘口是否单边；换标的或降名义 |
| 对冲/开仓被拒（reject_rate 上升） | 停该标的；`arbtwin doctor` 看两所规格是否变了 | 更新 `config/venues.yaml` 的费率/最小下单量 |
| WS 断开 > 2 分钟 | 引擎自动停开仓（`ws_health`）；确认 REST 仍可用 | 检查网络/代理；`systemctl restart` |
| Lighter 维护/OI 上限被占满 | 只平不开 | 等维护结束，重跑 `screen` |
| 稳定币脱锚 > 0.5% | 记录，**不要**因此加杠杆或换腿 | 评估是否需要把两边资金重新配比 |
| 某一所提币拥堵 | 降低该所仓位，直到能正常提 | 把单所限额降到 30% |
| 账户权益回撤 3% | 停机（写进 stop-loss 阈值） | 复盘：成交质量、摩擦假设、标的切换 |

---

## 8. 部署实录（2026-11-08，目标机 155.254.60.38）

| 项 | 记录 |
|---|---|
| 主机 | Debian 12（`6.1.0-50-amd64`），1 vCPU / 960 MB RAM / 9.8 GB 磁盘 |
| Swap | `/swapfile` 1 GB，`/etc/fstab` 已写 `/swapfile none swap sw 0 0`，`vm.swappiness=20`；用 `swapoff -a && swapon -a` 验证过重启可自动挂载 |
| 代码 | `/opt/arbtwin`（`git clone --depth 1`），venv `/opt/arbtwin/.venv`，`arbtwin 0.2.0`，Python 3.11.2 |
| 服务 | `arbtwin.service`（User=arbtwin，`ARBTWIN_DRY_RUN=1`，`--interval 1`），`active`，`NRestarts=0`，RSS ≈ 52 MB，CPU ≈ 15% |
| universe | `ARBTWIN_SYMBOLS=config/symbols.starter.yaml`（SOXL / VVV / GRAM，wave 模式，p95→p50） |
| NautilusTrader | 独立 venv `/opt/nautilus-venv`，Python **3.12.16**，`nautilus_trader 1.231.0`（该版本要求 ≥3.12，用 `uv` 装 CPython 3.12 + manylinux 轮子，未做源码编译） |
| 连通性 | 从该机：OKX `200`；Lighter `orderBookDetails` `200`（BTC mark 75884.8）→ 无需代理 |
| 状态文件 | `/opt/arbtwin/state/snapshot.jsonl` 每轮落盘，示例：`SOXL WAIT basis -1.66 / trigger 10.8`、`GRAM BLOCK（薄盘口）` |

两个部署期踩到的坑（已修进文档/配置）：

1. **`User=arbtwin` 没建用户** → systemd `status=217/USER` 死循环；`useradd -r` + `chown -R` 后正常。
2. **`.env` 里 `ARBTWIN_SYMBOLS` 指向全量 12 标的** → 服务跑的是全量 universe；改成 `config/symbols.starter.yaml` 才是 v1 的 3 标的。

> 1 GB 机器上不要同时跑 nautilus 与实盘引擎：`import nautilus_trader` 本身就要 300–500 MB，
> swap 只是 OOM 缓冲，不是性能来源。

## 7. 附：本次交付的验收清单

- [x] 单位可证（CV ≤ 0.08%、相关 ≥ 0.999）→ `calibration.py`，每次 `screen` 复算
- [x] 成本可算（4 bps 双挂 / 10 bps taker-hedge）→ `fees.py`，用实时费率
- [x] 深度可测（400/250 档 + 每 bp 密度 + `depth_complete`）→ `mechanics.py`
- [x] 资金费口径正确（Lighter 百分比/小时 → 小数/区间）→ `venues/lighter.py` + 两个单测
- [x] 回归可验（半衰期 0.8–1.7h）→ `backtest/replay.py`（因果、t+1 成交）
- [x] 事件风险可查（MSTR 11-11 MSCI）→ 本文 1.2 + `screen` 前新闻复核
- [x] 停机路径存在（drift 3–12%、max hold 4h、回撤 3%、kill switch）→ `engine.manage_risk`
- [x] 可审计（`state/*.jsonl` 全量落盘、`trades.jsonl` 含 `expected_net_bps`）
- [x] 可回滚（`ARBTWIN_DRY_RUN=1` 即完全不下单；`systemctl stop`）