# arbTwin — OKX ↔ Lighter 双塔基差引擎

> **实盘手册（部署 / 获利前提 / 标的筛选 / 监控 / 故障预案）**：[`docs/DEPLOYMENT.zh.md`](docs/DEPLOYMENT.zh.md)
> **执行模式**：`config/risk.yaml → strategy: wave`（一轮一清、纯限价、两腿必须同时出清，见 `arbtwin/wave.py`）或 `maker_basis`（旧的两模式引擎）
> **本次对话完整存档**：[`docs/conversation.html`](docs/conversation.html)
>
> 一个交易所（OKX，USDT 本位 CEX）与一个 zk-rollup DEX（Lighter，USDC 本位）之间，
> **同一标的、同一单位**的两个报价之间的机械基差交易。默认只读、默认 dry-run、
> 每一道风控都是 veto 而不是建议。

```
arbtwin doctor     # 配置 / 费率 / 机械参数 / 两个 venue 可达性
arbtwin screen     # 从现场数据重建 universe（写入 config/symbols.yaml）
arbtwin monitor    # 溢价矩阵 + 告警（只读）
arbtwin run        # 交易循环（默认 dry-run）
arbtwin backtest   # 单标的因果回放 + walk-forward
```

---

## 一、为什么是 OKX ↔ Lighter（以及这和 HL ↔ Lighter 有什么本质不同）

上一版研究（`GLFTmm` / @totofdn 推文）里的标的是 **Hyperliquid ↔ Lighter**。换到
OKX 之后，可行性与风险结构都变了，必须先看清楚再写代码：

### 1.1 标的重叠：不是变少，而是变多

现场拉取（2026-11-08）：

| venue | 市场数 | 说明 |
|---|---:|---|
| OKX `SWAP`（USDT 结算，`state=live`） | 528 | 含 RWA/股票类：`AAPL/ NVDA/ TSLA/ ASML/ AMZN/ COIN/ ANTHROPIC/ XAU …` |
| Lighter（perp，`status=active`） | 215 | 含代币化股票、外汇、商品、pre-IPO：`AAPL/ NVDA/ ASML/ BYD/ XIAOMI/ EURUSD/ XAU/ SPACEX/ ANTHROPIC …` |
| **可直接配对的交集** | **181**（含 `1000*` 重定名后 186） | 这是 OKX↔Lighter 相对 HL↔Lighter 的最大优势：HIP-3 的标的覆盖面远小于 OKX 的 listing |

结论：**交易所层面的可行性成立**，而且是三个 venue 组合里最宽的一个。

### 1.2 但这是两种"物理性质不同"的 venue

| 维度 | OKX | Lighter | 对策略的直接影响 |
|---|---|---|---|
| 结算资产 | USDT | **USDC** | "delta 中性"仍带 USDT/USDC 基差；两腿权益、资金费率、盈亏各记一种稳定币 |
| Maker / Taker 费 | 2 bps / 5 bps（Lv1） | **0 / 0**（当前每个 perp 都发布 `maker_fee=0 taker_fee=0`） | 被动腿必须放 Lighter；主动腿放 OKX 要付 5 bps，这是最大成本项 |
| 链路时延 | 中心化撮合，IOC 秒级 | **taker 有 300ms speed bump**（Standard 档），post-only 无 bump | 结构上只能"Lighter 挂单领先、OKX IOC 对冲"，反过来等于花钱买延迟 |
| 资金费 | 8 小时一次 | **1 小时一次**（`funding_timestamp` 间隔 1h） | 年化必须分别换算：OKX `×3×365`，Lighter `×24×365`；同一 APR 在两边代表完全不同的成本 |
| 抵押/杠杆 | 分层 MMR，`lever` 最高 100x | `default_initial_margin_fraction`（如 AAPL 666 = 6.66% 初始保证金） | Lighter 的杠杆上限要读市场元数据，不能假设 |
| 下单身份 | API key + passphrase | **API key 索引 4–254 + zk 签名 + 每 key 独立 nonce 流** | 多进程/多用途要拆 key，否则一个慢交易会卡住后面所有交易 |
| 深度 | REST 可拉 400 档 | REST 250 档（订单级） | 见 1.5：档数决定"深度"是否可信，5 档会把 AAPL 的 1% 深度低估 **59 倍** |

**因此 OKX↔Lighter 不是"换个 venue 名字"，而是：成本从 HL 的 1.5bps maker 变成
OKX 的 2bps maker / 5bps taker；资金费频率变 8 倍；taker 结构上变慢 300ms。**

### 1.3 必须承认的第一个硬约束：单位一致性要证明，不能假设

实测 7 天小时收盘（同一时刻对齐，比值 = Lighter/OKX）：

| 标的 | 价格比均值 | 比值 CV | 收益率相关系数 | 判定 |
|---|---:|---:|---:|---|
| ETH | 1.0001 | 0.011% | 0.9997 | 同一标的 |
| AAPL | 0.9987 | 0.033% | 0.9983 | 同一标的 |
| NVDA | 0.9988 | 0.031% | 0.9989 | 同一标的 |
| **ANTHROPIC** | **10.38** | **4.07%** | **0.80** | **不是同一标的（契约单位/溢价产品不同）** |

所以 `calibration.py` 是**硬筛选**，不是装饰：比值不稳定或收益率解耦的品种直接剔除。
`arbtwin screen` 会把 ANTHROPIC 以这条原因拒掉（现场输出可复现）。

### 1.4 第二个硬约束：主流标的的基差常常不覆盖成本

7 天实测（小时收盘计算的基差 p95 vs 成本）：

| 标的 | 基差 p95 | taker-hedge 净收益* | maker/maker 净收益* |
|---|---:|---:|---:|
| ETH | ≈2 bps | 负 | 负 |
| AAPL | ≈9 bps | ≈−9.7 bps | ≈−0.2 bps |
| NVDA | ≈7 bps | ≈−11.0 bps | ≈−1.4 bps |
| BTC | ≈5 bps | ≈−13.4 bps | ≈−4.0 bps |

\* `净 = 基差 p95 − 往返成本 − 延迟成本 − 滑点 − 资金费`，成本口径见 5.2。

**这是真实的、不可回避的结论**：在流动性最好的 1:1 标的上，机械基差不足以支付 OKX 的
taker 费。更宽一点的品种（实测 FARTCOIN ≈13bps、VIRTUAL ≈9bps）仍然不够 10bps+ 的
taker-hedge 往返，只有 maker/maker（≈4bps）或激励期才有空间。

> 口径提醒：上表用**小时收盘**计算，会把盘中瞬时错价平滑掉（推文里"8%"是瞬时值）。
> 因此这是**保守下界**，不是上限。要看瞬时分布，请让 `arbtwin monitor` 连续跑一段时间，
> 它把每个 poll 的 basis 写进 `state/monitor.jsonl`；用那段 tick 级样本重算才是真实机会。

真正能赚钱的地方只有三类（README 第五节给出可核验的门槛）：

1. **中低流动性标的**：盘口宽、基差 p95 达到 20–60 bps，但 1% 深度和"移动 25bps 的
   成本"必须过闸；
2. **零费/激励窗口**：Lighter 当前 maker/taker 均 0，若 OKX 处于 maker 返佣或做市激励期，
   成本可从 4bps 降到 0 甚至为负；
3. **纯被动双腿（dual_maker）**：两边都 post-only，成本只剩 OKX 的 2bps maker，
   代价是承担单腿成交风险（引擎用 watchdog + 被动退出队列兜住）。

### 1.5 "5 档低估 59 倍"到底是什么意思（以及要不要弃用 REST 只用 WS）

**不是 REST vs WS 的问题，是"档数 × tick 密度"的问题。**

AAPL-USDT-SWAP：价格 342.28、`tickSz=0.01` → **1 tick = 0.29 bps**。所谓"1% 深度"是
343 个 tick 之外的区间，而 `sz=5` 只给你 5 档 ≈ **1.5 bps** 的视野：

| 取数 | 覆盖带宽 | 1% 区间内名义 |
|---|---:|---:|
| OKX `books?sz=5` | ≈1.5 bps | **$12.4k**（只数了 5 档） |
| OKX `books?sz=200` | ≈58 bps | $736k（仍是下界） |
| OKX `books?sz=400` | ≈218–230 bps | **$765k / $859k（完整覆盖 1%）** |

59 倍就是这么来的（736k / 12.4k ≈ 59）：不是流动性差 59 倍，是**视野窄了 40 倍**。

对照 Lighter：它的订单簿是**稀疏**的（AAPL 88 个价位就跨了 5000bps，NVDA 235 个价位跨
5100bps），所以 `orderBookOrders?limit=250` 已经**完整覆盖 1% 区间**；实测 Lighter
REST 深度与 WS `order_book` 快照深度**完全一致**（ratio = 1.00，AAPL/NVDA 双检）：

| 标的 | REST 250 单 | WS 快照 | 1% 深度（WS） |
|---|---|---:|---:|
| AAPL | 99 / 91 单 | 88 / 88 价位 | $1,116,714 / $1,282,943 |
| NVDA | 250 / 165 单 | 235 / 147 价位 | $1,317,264 / $1,360,567 |

**因此当前实现（也是建议）：**

* **定价/成交判定**：WS top-of-book（OKX `books5`、Lighter `order_book` 增量），低延迟；
* **深度闸门**：REST 深档快照（OKX 400 / Lighter 250，TTL 1.5s 复用），因为闸门需要的是
  "1% 内有多少钱"，不是"最新一档多少钱"；
* 两者**代码路径分离**：`OkxVenue.quote()`（WS，5 档）与 `OkxVenue.book()`（REST 400 档）
  不共用缓存——否则一个 5 档 WS 书会悄悄喂给深度闸门，把 gate 变成噪声。

**不建议只留 WS**：OKX `books` 通道（400 档/100ms）确实能替代 REST，但代价是本地维护
订单簿（`seqId`/`prevSeqId` 断档重同步）＋ Lighter 侧 `offset`/`begin_nonce` 连续性校验；
一旦断档没发现，你拿到的是**静默残缺的深度**，比"REST 慢 1 秒"危险得多。当前方案用
REST 快照把这类风险挡在外面，等你需要 <300ms 的 gate 刷新再上 WS 全量。

**一个必须知道的边角**：档数上限对不同 tick 密度含义完全不同。BTC tick = 0.013 bps，
400 档只能看到 **5.3 bps**——BTC 的"1% 深度"根本无法从任何一档快照里测出来。所以闸门
不能直接比 `depth@1%`，应比**每 bp 的名义密度**：

```
depth_usd_covered(100bp)  -> (usd, covered_bps, complete)
depth_density_usd_per_bps = usd / min(100, covered_bps)   # 与 tick 密度无关
gate: depth_density >= min_depth_usd_1pct / 100
```

BTC 的 400 档只是"5.3bp 视野"，但密度很高 → 通过；薄山寨的 100bp 视野密度很低 → 拒绝。
`depth_complete` 会一起打印，方便区分"真的没流动性"和"只是档数不够"。

---

## 二、P0–P8：每个研究结论落到哪个文件

| 编号 | 结论 | 实现 | 关键行为 |
|---|---|---|---|
| P0-1 | 多 venue 溢价矩阵、单位归一 | `calibration.py`, `spread.py` | 比值 CV / 收益率相关硬门槛；`basis_bps`、`executable_edge_bps`、`maker_net_bps` 三个不同口径 |
| P0-2 | venue 微观机制风控 | `mechanics.py` | mark 窗口、mark cap/floor、OI 利用率、1% 深度、"移动 25bps 的成本"、强平距离；quote TTL 必须 < mark 窗口/2 |
| P1-3 | 定价锚不是 venue mid | `anchor.py` | `tradfi > RFQ > 两个 index 中位数`；两个 index 分歧过大 → 拒绝定价；收盘后冻结锚、只允许减仓 |
| P1-4 | σ→0 的退化分支 | `band.py` | `band = clamp(max(floor, k·σ_basis, fee×multiple), floor, cap)`；σ_basis<3bps 退化为 `fee×1.5`；σ 极大时封顶不失效 |
| P1-5 | 费率是 (venue, 订单类型, 激励期) 的函数 | `fees.py` | `effective = nominal − incentive_value`；往返成本含平仓保守估计与结算摩擦 |
| P2-6 | 发行方/机制风险卡 | `issuer_risk.py` | 7 个硬红旗（递归抵押、单一 oracle、可调供给上限、发行方即交易对手、激励即退出流动性、pre-IPO 主张、无 index）+ 软红旗缩规模 |
| P2-7 | 操纵成本 | `manipulation.py` | "移动 25bps 需要多少钱"；薄盘口 → 按 sqrt 放大入场带宽与延迟成本；单边深度不对称也放大 |
| P2-8 | 意图式退出 | `exitqueue.py` | 退出=带 deadline 的挂单队列（0/2/5 bps 阶梯），临近 deadline 才升级 IOC，只有风控止损才允许市价 |

不适配的部分（Polymarket 决议套利、WETH 事件性 overpeg 单次交易、依赖某个 venue
bug 的周末/forex 玩法）**没有**进入本仓库。

---

## 三、架构

```
                      ┌───────────── config/*.yaml + .env ─────────────┐
                      │ venues(费率/机制)  risk(限额/registry)  symbols │
                      └───────────────┬────────────────────────────────┘
                                      │
   OKX REST/WS ──► venues/okx.py ─┐   │   ┌── venues/lighter.py ◄── Lighter REST/WS
                                  ├─► engine.py ──► spread.py（溢价矩阵 + 双腿计划）
   Lighter SDK ──► venues/lighter_trade.py（签名/下单/撤单）
                                      │        │
                    ┌─────────────────┴────────┼──────────────┐
                    ▼                          ▼              ▼
              mechanics.py                band.py        monitor.py
              anchor.py                   fees.py        （JSONL + webhook）
              issuer_risk.py              manipulation.py
```

执行模型（`engine.execute_best`）：

* `action=ENTER`（可立即跨价差）→ **Lighter post-only 领先 → OKX IOC 对冲**，
  未对冲窗口硬上限 `max_unhedged_ms`（默认 800ms），对冲失败则 reduce-only 反手拆掉领先腿；
* `action=MAKER`（基差够覆盖被动成本）→ **双腿 post-only**，`_poll_maker` 每轮检测成交，
  单腿成交超过 `max_unhedged_ms` 立即转成被动退出队列并告警；
* 任何持仓触及单腿漂移 `emergency_stop_loss_pct`（12%）→ 强制退出（唯一允许市价的路径）。

---

## 四、部署

### 4.1 机器与前置

* Linux x86_64（Ubuntu 22.04/24.04 实测口径），Python ≥ 3.10；Windows 仅用于研究/dry-run。
* 能直连 `api.okx.com` 与 `mainnet.zklighter.elliot.ai` 的网络。若在受限网络：
  `export ARBTWIN_PROXY=http://host:port`（代码只对 REST 走代理，WS 走直连）。
* **一笔两所各自的保证金**：OKX 用 USDT，Lighter 用 USDC。两边的缓冲互不共享，
  这是硬性资金前提（见 6.1）。

### 4.2 安装

```bash
git clone https://github.com/jssyxd/arbTwin.git
cd arbTwin
python3 -m venv .venv && . .venv/bin/activate
pip install -U pip
pip install -e '.[live,dev]'       # live = lighter-sdk；只做研究与监控可省掉 live
cp .env.example .env && $EDITOR .env
```

### 4.3 OKX 凭据

OKX 后台 → API → 创建 API key：

* 权限：**Read + Trade**（不要开 Withdraw）；
* 绑定 IP 白名单（VPS 出口 IP）；
* passphrase 自己设，写入 `.env` 的 `OKX_API_PASSPHRASE`；
* 用模拟盘演练时设 `OKX_SIMULATED=1`（会带 `x-simulated-trading: 1`）。

`.env`：

```
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_API_PASSPHRASE=...
OKX_SIMULATED=0
```

### 4.4 Lighter 凭据（和 CEX 完全不同的流程）

Lighter 用 **API key + zk 签名**，每个 key 自己一条 nonce 流：

1. 用持有账户的 L1 地址（Ethereum 私钥）在 SDK 里注册 API key（一次性 `ChangePubKey`）：

```python
# python -c "..." 或放进自己的初始化脚本，不在本仓库保存 L1 私钥
import lighter
client = lighter.SignerClient(url="https://mainnet.zklighter.elliot.ai",
                              account_index=<你的 account_index>,
                              api_private_keys={<api_key_index>: "<api_private_key_hex>"})
client.create_api_key()      # 输出需要由 L1 地址签名的注册交易
```

2. 用 `accountsByL1Address` 查 `account_index`：

```bash
curl "https://mainnet.zklighter.elliot.ai/api/v1/accountsByL1Address?l1_address=0x你的地址"
```

3. 写入 `.env`：

```
LIGHTER_ACCOUNT_INDEX=...
LIGHTER_API_KEY_INDEX=4         # 4..254，0-3 是 Lighter 自用
LIGHTER_API_PRIVATE_KEY=...
```

> 建议至少三把 key：`4` 挂单/撤单、`5` 主动腿（taker）、`6` 转账。
> 同一 key 的交易严格串行，一个 300ms 的 taker 会把后续 post-only 一起拖慢。

### 4.5 部署前必做（顺序不可跳）

```bash
arbtwin doctor                 # 1) 可达性 + 费率 + 机械参数 + universe 自检
arbtwin screen --days 7        # 2) 现场重建 universe（会写 config/symbols.yaml）
arbtwin backtest AAPL --days 30  # 3) 单标的因果回放 + walk-forward
arbtwin monitor --iterations 5 --interval 5   # 4) 只读观察 30 秒，确认矩阵/告警正常
ARBTWIN_DRY_RUN=1 arbtwin run --iterations 200 --interval 1   # 5) 决策级 dry-run
```

`run` 在 `ARBTWIN_DRY_RUN=0` 时必须额外带 `--i-know-what-im-doing`，否则拒绝启动。

### 4.6 systemd

```ini
# /etc/systemd/system/arbtwin.service
[Unit]
Description=arbTwin OKX<->Lighter basis engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=arbtwin
WorkingDirectory=/opt/arbtwin
EnvironmentFile=/opt/arbtwin/.env
Environment=ARBTWIN_DRY_RUN=1
ExecStart=/opt/arbtwin/.venv/bin/arbtwin run --interval 1
Restart=always
RestartSec=5
StandardOutput=append:/var/log/arbtwin/engine.log
StandardError=append:/var/log/arbtwin/engine.log
# 交易进程不需要 root，也不该有写系统目录的权限
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin arbtwin
sudo mkdir -p /opt/arbtwin /var/log/arbtwin && sudo chown -R arbtwin: /opt/arbtwin /var/log/arbtwin
sudo systemctl daemon-reload && sudo systemctl enable --now arbtwin
sudo journalctl -u arbtwin -f        # 或 tail -f /var/log/arbtwin/engine.log
```

### 4.7 状态文件（都在 `ARBTWIN_STATE_DIR`，默认 `state/`）

| 文件 | 内容 |
|---|---|
| `snapshot.jsonl` | 每轮决策：每标的 action / basis / net / band |
| `monitor.jsonl` | 每轮监控快照（含 basis z、健康度） |
| `trades.jsonl` | 已开仓交易（含 `unhedged_ms`） |
| `alerts.jsonl` | 所有告警（warn / critical） |
| `backtest_<SYM>.json` | 回放统计 |

---

## 五、获利前提（不满足就不要上）

一条一条核，全部成立才有正期望：

### 5.1 结构性前提

1. **单位已被证明一致**：`calibration` 判定 `same_instrument` 或 `ratio_stable`，
   且比值 CV < 0.5%、收益率相关系数 > 0.99（`arbtwin calibrate <SYM>` 可复验）。
2. **两个 venue 都活着**：`doctor` 一次拉全市场；任何一个 venue 的 `/orderBookDetails`
   或 `/instruments` 失败，引擎直接不报价。
3. **标的是"同一份风险"**：`issuer_risk` 无硬红旗（尤其 pre-IPO/合成现货/单一 oracle）。
4. **锚不歧义**：`anchor` 能给出可用锚；股票类闭市时锚是冻结的，此时只允许减仓。

### 5.2 数学前提（每个标的都要过）

```
基差 p95  >  往返成本 + 延迟成本 + 滑点 + 入场带宽
往返成本   = 开仓(领先腿 + 对冲腿) + 平仓(保守取两侧较贵)  + 结算摩擦
延迟成本   = σ_1h × sqrt(latency_ms / 3.6e6)        （Lighter taker 300ms 必须计入）
入场带宽   = clamp(max(8bps, 2×σ_basis, 1.0×fee), 8bps, 600bps)
             σ_basis = 基差自身的小时波动（不是价格波动）；σ_basis<3bps 时退化为 max(8bps, 1.5×fee)
```

用当前的默认参数（OKX 2/5 bps、Lighter 0/0）：

| 模式 | 往返成本 | 要求基差 p95 至少 |
|---|---:|---:|
| `hedge_now`（Lighter 挂 + OKX IOC） | ≈10 bps + 延迟 + 滑点 | **≥18–20 bps** |
| `dual_maker`（两边都挂） | ≈4 bps | **≥10–12 bps**（且需承担单腿风险） |

### 5.3 资金与规模前提

* 单 leg 名义 ≤ `min(本 venue 权益 × 杠杆 × 0.35, 1% 深度 × 10%, $3000)`；
* 两所权益各自覆盖最坏情况的单腿漂移 12%：即单边权益 ≥ 名义 × 12% × 杠杆缓冲；
* 同时持仓数 ≤ `max_open_symbols`（默认 4），因为退出通道（被动队列）是共享稀缺资源；
* 净资金费被**计入成本/收入**：`funding_cost_bps = carry_APR/100/8760 × 1e4 × expected_hold_h`
  （默认 2 小时）。单位口径是这里最容易错 100 倍的地方：**Lighter 的 `funding_rate`
  是百分比/小时**（`0.0012` = 0.0012%/h ≈ 10.5% APR），**OKX 的 `fundingRate` 是小数/8 小时**。
  实测 14 天中位年化：权益类 **3.5%**、加密类 **10.5%**、SOXL **28%**（均值被少数极端小时抬到 9–39%）。
  方向对我们有利时它是补贴（monitor 会提示 `funding harvest regime`），不利时才计入成本。

### 5.4 不要交易的情形（引擎会自己拒绝，但要理解为什么）

* mark 与 index 偏离超过阈值（`mark_divergence_max_bps`）：说明定价机制异常或 oracle 抖动；
* OI 使用率 > `oi_utilization_max`：venue 结构性拥挤（Lighter 有公开 OI 上限可算）；
* 移动 25bps 的成本 < `min_move_cost_usd`（默认 $50k）：盘口薄到可以被人为制造；
* 强平距离 < 18%：mark 走到那里只需要一次插针；
* quote TTL > mark 窗口/2：你挂的单会活过 venue 的定价窗口，被"用旧价成交"。

---

## 六、如何筛选标的

### 6.1 漏斗（`arbtwin screen`）

```
两所同时上市且 active
  → 7d 小时收盘对齐 → 比值 / 比值CV / 收益率相关      （去掉"看着像同一标的"的）
  → σ_1h → 入场带宽；基差分布 p50/p95/命中率
  → 两所 200 档盘口 → 1% 深度、移动 25bps 成本、单边不对称
  → 结构风险（OI 利用率、mark 偏离、保证金比例、registry 红旗）
  → 计分 = 最优模式净收益 × 命中率 × 风险缩规模系数
  → 排序取前 N，写入 config/symbols.yaml（含 unit_ratio / σ / session / 证据）
```

对每个落选项，`symbols.yaml` 记录的证据字段：

```yaml
- symbol: NVDA
  unit_ratio: 0.99876
  sigma_1h_bps: 56.8
  basis_sigma_bps: 2.6
  band_bps: 10.4
  session: us_equity
  preferred_mode: dual_maker
  notes:
    ratio_cv_pct: 0.031
    return_corr: 0.99894
    basis_p95_bps: 7.3
    fill_probability: 0.21
    score: 0.31
```

`preferred_mode` 只是"人类复核时的提示"（哪种模式历史经济性更好），引擎每轮会用实时
费率/基差重新决策，不读这个字段。

### 6.2 首次全量实测（2026-11-08，7 天小时线）

```
candidates: 175
approved 12 / 175 in 831.5s
  GRAM 1.0011 / SOXL 0.9988 / VVV 1.0003 / COIN 0.9984 / LIT 1.0007 / MSTR 0.9987
  XPT 0.9986 / EWY 0.9988 / TRUMP 1.0001 / MRVL 0.9990 / ORCL 0.9988 / SNDK 0.9989
rejected 163，主要三类原因：
  · 基差 p95 不够覆盖成本（SPY/DOT/AMZN/NVDA … ≈ -10bps）
  · 移动 25bps 的成本 < $50k（APT/DASH/DYDX/RIVER …）→ 薄盘口
  · 比值不稳/收益解耦（SHEIN 0.26%/0.84、NOK、GEV、XCU）→ 不是同一标的
```

这 12 个是"值得再看一眼"的名单，不是"现在就能开仓"的名单：实测它们当下的
`net` 仍在 −7 ~ −80bps（盘口宽度 20–50bps），说明**入场发生在瞬时错价，而不是稳态**。
把这 12 个丢进 `monitor` 持续采样（tick 级），等到 z 值/净收益触发的那些小时才是交易窗口。

### 6.3 实操建议

* 一次全量扫描 ≈ 180+ 标的、3–6 分钟（内置 0.12s 节流，遵守 OKX 20 req/2s）。
* 扫描口径是 7 天**小时线**：比真实瞬时错价保守，适合做"能不能做"的初筛；
  精确机会分布靠 `monitor` 的 tick 级采样（见 1.4 的提醒）。
  定时任务建议每小时一次，**只对 score 前列做人工复核**。
* `--only AAPL,NVDA,ETH` 用于定点复验；`--max-symbols` 控制入册数量。
* 全量扫描结果里 `rejected` 段落是资产：它记录了每个品种"为什么不能做"，
  这是比"能做什么"更有价值的长期数据（可以接进 BI 看市场结构变化）。
* 换月/上新后必须重扫：新上市标的常常单位未标定（比值不成立）或深度不足。

---

## 七、如何监控

### 7.1 你真正要盯的 8 个信号

| 信号 | 触发 | 含义 |
|---|---|---|
| `ws_age_s` | > 30s | 行情陈旧，正在用旧价决策 |
| `basis_z` | \|z\| > 4（近 30 天窗口） | 溢价离开分布，可能是机制变化而不是机会 |
| `net_bps` | 持仓时 < 0 | 这笔交易在付钱持有 |
| `unhedged_usd` / 单腿成交超时 | > `max_unhedged_ms` | 一条腿成交、另一条没跟上 |
| `liq_distance_pct` | < 18% | mark 正在走向你的强平 |
| `funding_carry_apr` | > 25% | 基差在给亏损融资（注意 Lighter 是小时费率） |
| `oi_utilization` | > 25% | venue 结构性拥挤 |
| `reject_rate` | > 20%（最近 50 单） | venue 改了规则/权限/最小下单量 |

### 7.2 命令与产物

```bash
arbtwin monitor --iterations 1                 # 一屏矩阵 + 告警
ARBTWIN_ALERT_WEBHOOK=https://... arbtwin run  # 告警同时 POST 到 webhook
tail -f state/alerts.jsonl                     # 结构化告警流
jq -r 'select(.level=="critical") | [.ts,.scope,.message]|@tsv' state/alerts.jsonl
```

矩阵输出（`monitor.render`）示例：

```
symbol        basis        z      net    band    fee  action
AAPL            8.7b    1.42     -0.2b   12.4b   4.0b  WAIT
NVDA            6.1b    0.88     -1.4b   14.0b   4.0b  WAIT
```

### 7.3 建议的报警分级

* **critical（立即人看）**：单腿裸奔、`UNWIND FAILED`、强平距离 < 18%、拒单率 > 20%；
* **warn（工作时间看）**：ws 断连、无权益、maker 单边成交转退出、对冲超时；
* **info（趋势）**：基差 z 极值、某个标的分歧导致锚不可用。

### 7.4 已知限制（必须知道，不要在故障时误判）

* 单次 `snapshot` 对每个标的并发拉深度；OKX 的 mark/index/OI 走**三个全市场批量接口**
  （`/public/mark-price`、`/public/open-interest`、`/market/index-tickers`）5s TTL，
  资金费 300s TTL —— 12 标的从 36 次/轮降到 3 次/轮。
* 深度快照 TTL 1.5s；`quote()`（WS 5 档）与 `book()`（REST 400 档）分开缓存，
  快路径永远不会污染深度闸门。
* 成交确认：当前用 **轮询持仓 + 盘口穿越** 判定，未接 Lighter 账户级 WS 推送；
  收益是"实现简单、无鉴权依赖"，代价是最坏一个 `poll_interval` 的确认延迟。
* 回放不模拟队列位置与逐档吃单，只回答"基差是否够覆盖成本"这一件事。
* OKX 账户级费率（返佣档位）需要人工填 `config/venues.yaml`，接口不返回你的实际档位。

---

## 八、目录

```
arbtwin/
  calibration.py  spread.py        P0-1 单位证明 + 溢价矩阵 + 双腿计划
  mechanics.py                     P0-2 mark 窗口/cap、OI、深度、移动成本、延迟成本
  anchor.py                        P1-3 定价锚 + 交易时段冻结
  band.py                          P1-4 σ×费率带宽（含 σ→0 退化）
  fees.py                          P1-5 费率 = venue × 订单类型 × 激励
  issuer_risk.py                   P2-6 结构风险卡
  manipulation.py                  P2-7 操纵成本 / 薄盘口惩罚
  exitqueue.py                     P2-8 带 deadline 的被动退出队列
  engine.py                        决策、执行、maker 报价轮询、风控退出
  screening.py                     universe 漏斗
  monitor.py                       监控快照 + 告警
  data.py                          两所小时线（带缓存）
  venues/{okx,lighter,lighter_trade}.py, venues/base.py
  backtest/replay.py               因果回放 + walk-forward
config/{venues,risk,symbols}.yaml
tests/                            46 个纯逻辑测试（无网络）：校准三态、带宽退化、
                                  费率口径、机制门槛、矩阵方向、双腿计划、
                                  maker 单边成交转退出、回放因果性
```

## 九、免责声明

本项目是研究/工程实现，不构成投资建议。跨所基差交易会因 venue 规则、资金费、稳定币
汇率、链上拥堵和 api 变更而产生真实亏损；先用 `ARBTWIN_DRY_RUN=1` 跑够样本，
再考虑小额真实资金。