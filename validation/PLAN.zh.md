# 双腿基差策略：必测项的验证环境与数据计划（2026-11-10）

> 目标：在**回测/模拟**里先验证 4 个必测项，只有无法模拟的部分才进 testnet/小资金实盘。
> 本文件是环境与数据清单；执行代码在 `validation/`，报告在 `validation/reports/`。

## 0. 本机实测结论（决定数据路线）

| 项 | 实测 | 后果 |
|---|---|---|
| Lighter mainnet / testnet | ✅ 可达（`mainnet/testnet.zklighter.elliot.ai` 200） | 可直接录制 L2/成交、拉 candles |
| OKX | ❌ 不可达：DNS 把 `www.okx.com` 解析到 `169.254.0.2`，`api.okx.com` 无地址 | **必须** Linux VPS 或代理才能采 OKX 数据/做 demo 测试 |
| Binance / Bybit | ❌ 不可达 | 不能拿它们做 OKX 的替代腿；只能作为外部对照 |
| GitHub / Kaggle API / Tardis API | ✅ 可达 | 可拉静态数据集、开源实现 |
| 本机 Python | 3.12.9；pandas 2.3.3 / pyarrow 25.0.1 / numpy 2.5.1；NT 1.219.0 | 回测可用；NT v2 需独立 venv |
| 凭据 | 仓库无 `.env`（仅 `.env.example`） | OKX demo / Lighter testnet 测试需用户提供 key |
| 磁盘 | W: 84 GB 可用 | 足够 20–40 GB 的 tick 数据 |

**最小外部前提（按优先级）**
1. **一台能连 OKX 的 Linux VPS**（Ubuntu 22.04/24.04，能访问 `api.okx.com`、`ws.okx.com:8443`），
   或本机可用的 HTTP/SOCKS 代理（设置 `ARBTWIN_PROXY`）。
2. **OKX 模拟盘 API key**（Read+Trade，`OKX_SIMULATED=1`）：用于改单/撤单/`clOrdId` 幂等测试。
3. **Lighter testnet API key**（index 4/5/6）+ account index：用于多 key nonce 与订单状态测试。
4. 可选：Tardis.dev API key（历史 L2 样本），或 OKX 历史 L2 数据供应商。

## 1. 环境清单

### 1.1 回测/仿真（本机即可）
- Python 3.12 + pandas/pyarrow/numpy（已装）
- `validation/` 包：数据加载、事件回放、队列成交模型、指标
- NT 1.219 只用于现有 API 检查；**NT v2.0.0rc8 单独 venv**：`python -m venv .venv-nt2 && pip install nautilus_trader==2.0.0rc8`
- 数据目录：`validation/data/{okx,lighter}/{symbol}/{datatype}.parquet`
- 状态/报告：`validation/reports/`

### 1.2 采集（需 VPS 或代理）
- OKX：`books`（400 档，100ms/1s）、`trades`、`funding-rate`、`instruments`、`mark/index`
- Lighter：`orderBookOrders`（250 档）、WS `order_book`/`market_stats`、`trades`、candles
- 采集器：5s 连续快照 + 价差 top-N 事件的 100ms 突发；单进程内存 < 1GB
- 时钟：UTC ms；每 venue 记录 `recv_ts` 与 venue `ts`，用于延迟标定

### 1.3 测试网/模拟盘
- OKX demo：`OKX_SIMULATED=1`，测订单生命周期（不下真钱）
- Lighter testnet：`https://testnet.zklighter.elliot.ai`，测 API key/nonce/订单
- NT v2：`sandbox` 执行客户端 + 上述 testnet/demo

## 2. 数据清单（最小可用集）

| 数据 | 覆盖 | 频率/深度 | 用途 | 估算 |
|---|---|---|---|---|
| 1m K 线 | 12 标的 + BTC/ETH，90 天 | 1m | OU 半衰期、带宽、事件研究 | <1 GB |
| 1s K 线 | 同上，14 天 | 1s | 盘中回归、markout 基准 | 1–2 GB |
| L2 快照 | 4 个标的，14 天 | 连续 10s；事件窗 100ms；OKX 400 档 / Lighter 250 档 | 队列成交、p_fill、滑点 | 原始 ~33 GB，Parquet+zstd ~8–12 GB |
| 逐笔成交 | 4 个标的，14 天 | tick | markout、VPIN、逆向选择 | 1–3 GB（压缩后 <1 GB） |
| 资金费 | 12 标的，90 天 | OKX 8h / Lighter 1h | carry、入场 veto | <100 MB |
| 合约规格 | 全部 | 静态 | tick/lot/min notional | <10 MB |
| 持仓/订单事件 | testnet 会话 | 事件 | 对账、nonce | <1 GB |

**标的优先级**：COIN、VVV、LIT、SOXL、GRAM、MSTR（现有 12 个候选里流动性/基差较宽的前 6 个），
外加 BTC/ETH 作为“不可做”的对照。

## 3. 必测项与验收标准

### T1 OU 半衰期与阈值
- 估计：`Δb_t = α + β·b_{t−1}`，`θ = −β/Δt`，`τ½ = ln2/θ`；1m 与 5m 两个尺度，报告置信区间；
- 同时做 ADF/方差比，区分“真回归”与“采样伪影”；
- 输出：每标的 `θ, τ½, σ_basis, entry/exit/stop` 的耦合建议。
- 验收：能在 1m 数据上复现，且给出与小时线估计的差异说明。

### T2 成交概率 / markout / p_fail
- 定义：`p_fill(distance, level, queue_ahead, horizon)`；markout = 成交后 1/5/30 min 中间价相对成交价；
- `p_fail` = 首腿成交后 `entry_deadline` 内未配对的概率；
- 方法：队列反应模型 + 录制的 L2/成交；至少两种参数化，报告敏感性；
- 验收：在 Lighter 录制数据上跑通；OKX 数据到位后同一代码复跑；给出 `capture_fraction` 估计。

### T3 Lighter 多 key nonce（NT v2）
- 两个 `LighterExecutionClient`（key index 4/5）同 account：并发下单/撤单；
- 检查 nonce 冲突、订单丢失、对账漂移；
- 没有 testnet key 时：用源码/文档 + sandbox 证明协议，标注“未实盘验证”。
- 验收：脚本 + 日志 + 结论（能用/不能用/需一 key 一进程）。

### T4 OKX amend 与 clOrdId 幂等
- amend 是否保队列优先级：文档 + demo 行为（订单 id/时间戳/成交顺序）；
- 同一 `clOrdId` 重发：拒绝/覆盖/重复成交？在 demo 上逐一测；
- 验收：脚本 + 证据表；队列优先级若无法观测，明确标为“仅协议级”。

## 4. 回测/模拟边界（先做哪些）

1. **纯回放**：用录制的 L2/成交 + 固定规则，测 p_fill/markout/p_fail（不需要实盘）。
2. **策略回放**：把 `wave.py` 的新补仓逻辑接进假时钟，A/B `n_slices=1` vs 分片。
3. **NT 回测**：把同一批 tick 导入 `ParquetDataCatalog`，用 `BacktestNode` 复核。
4. **testnet**：只验证协议/对账/nonce，不用它评估收益。
5. **小资金实盘**：仅当 1–4 的误差 < 20% 才进入。

## 5. 当前阻塞项（需要用户提供）

- OKX 可达的 VPS/代理（否则 OKX 侧 L2、demo、amend 测试全部无法执行）；
- OKX demo key、Lighter testnet key（否则 T3/T4 只能做代码级验证）；
- 可选：历史 L2 数据（Tardis/Kaiko/CoinAPI）用于补足录制窗口。