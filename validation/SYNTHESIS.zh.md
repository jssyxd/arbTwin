# 必测项验证总结（回测/模拟优先）

> 2026-11-10。输入：`validation/{harness,ou,fill,venue,nt}/*`，`validation/PLAN.zh.md`。
> 结论只写实际跑过的；OKX 侧未跑的部分明确标 `BLOCKED`。

## 0. 环境结论（决定能做什么）

| 项 | 实测 | 影响 |
|---|---|---|
| Lighter mainnet/testnet | ✅ 可达 | 已拉 90 天 1m（14 标的）+ 7 分钟 L2/成交 |
| OKX | ❌ DNS 毒化（169.254.0.2） | OKX L2、demo、amend 测试 **BLOCKED**；需 VPS/代理 |
| Binance / Bybit | ❌ 直连不可达；**Binance Vision S3 可达** | 用 Binance 1m 静态数据做跨场所方法验证 |
| GitHub / Kaggle / Tardis API | ✅ | 源码/数据集可拉；Tardis 有 Lighter 历史，无 OKX |
| Python | 3.12.9；NT 1.219 | NT v2 需 **3.13/3.14**（见 §4） |
| 凭据 | 无 `.env` | OKX demo / Lighter testnet 测试 **BLOCKED** |
| 磁盘 | 84 GB | 已用约 300 MB（数据已 gitignore） |

**必须补的外部条件**：OKX 可达 VPS/代理；OKX demo key；Lighter testnet 双 key（index 4/5）。
没有这三项，T3/T4 只能做到源码/沙箱级验证。

## 1. T1 — OU 半衰期与阈值（已跑）

数据：Lighter 1m 90 天 ×14 标的（129k–135k 行/标的，gap=0）；
Binance USDT-M 1m 2026-08→10（BTC/ETH，131k 行）；OKX 静态数据 0 重叠。

| pair（真实跨场所） | n | τ½ | 95% CI | ADF | VR(5) | VR(60) | σ_basis | entry/exit/stop |
|---|---:|---:|---|---:|---:|---:|---:|---|
| Lighter↔Binance BTC | 116,079 | **6.2 min** | [3.5, 10.5] | −12.8 | 0.248 | 0.032 | 2.05 bps | 10.5 / 5.3 / 15.4 |
| Lighter↔Binance ETH | 116,050 | **5.9 min** | [3.8, 8.7] | −13.0 | 0.238 | 0.030 | 2.35 bps | 10.5 / 5.3 / 17.1 |

同一 basis 在 5m/60m 重采样：BTC τ½ 37.3/227.4 min，ETH 34.7/215.7 min →
**1m 是微观噪声的快速回归，小时级还叠加一个慢溢价因子（3.5–5 h）**。
仓库小时线 0.8–1.7 h 是两者的混合；**入场带应按持仓尺度选**，不要用 1m τ½ 直接定 p95 阈值。

单标的 `close-vs-EMA60` 的 τ½≈20 min 只是流水线校验，**不是场所基差**，不能拿来设 band。
脚本：`validation/ou/ou_calibrate.py`、`cross_venue.py`；结果：`results_{1m,5m}.json`、
`binance/{BTC,ETH}_cross_venue_ou.json`。
OKX 侧补齐只需 VPS 上 `history-candles?bar=1m` 90 天，再跑同一 `cross_venue.py`。

## 2. T2 — 成交概率 / markout / p_fail（部分跑通）

- **真实 Lighter 采集**：7 分钟（420 s），28,702 条 book、1,424 条 stats、3,445 笔 trades、
  371 次 250 档快照；ETH/VVV/COIN/LIT 中位价差 0.61/1.57/1.53/1.58 bps。
  文件：`validation/fill/data/sample/`（已 gitignore）。
- **框架**：`validation/harness` 自测 **PASS**（6 类 schema、保守/队列成交、回放、指标）；
  `validation/fill/{fill_model,metrics,analysis}.py` 已有队列反应模型 + markout/p_fail。
- **当前问题（必须修）**：smoke 分析给出 touch 60 s p_fill ≈ 0.97、queue 模型 1.0，
  与 Maglaras–Moallemi–Wang（1 min 45%）和 Daru（touch 逆向选择为负）明显不符 →
  说明「价格穿过」判定过宽/队列校准未标定。**这些 p_fill 数值不能用于定仓位**，只证明管线可跑。
- 采集还暴露：`mean_latency_ms ≈ −725 ms`（venue ts 与 recv_ts 时钟偏斜），必须做时钟对齐；
  `offset_gaps` 每条消息都变，说明 book 是增量流，重放需维护 book。
- 下一步：把 fill model 的触发条件改成「trade/queue 消耗到我们的价位且 queue_ahead→0」，
  用真实 Lighter 订单（testnet 或最小 mainnet 单）做 tier-3 校准；OKX 侧待 VPS。

## 3. T3 — Lighter 多 key / nonce（源码 + 沙箱已验证）

- NT **v2.0.0rc8** 安装成功于 **Python 3.14.10**（`.venv-nt2`）；**无 cp312 wheel**，
  `requires_python >=3.13,<3.15`；Lighter 适配器在 core wheel 内（无独立 extra）。
- 源码结论（`.src/nonce.rs`、`execution.rs`、`dispatch.rs`）：
  `NonceManager` 以 `(account_index, api_key_index)` 为键，**每个 client 独立**；
  skip window 16、CAS 分配、`nextNonce` 刷新与重连恢复。
  → **同一账户、不同 key index 的两个 client 可共存**（协议级）。
- 本地构造 `LiveNodeConfig` 两个 exec client（key 4/5）成功；沙箱 nonce 777/key、0 重复/0 缺口。
- **未做**：testnet 实际下单/撤单（无 `LIGHTER_TESTNET_*` 凭据）。
  注意：同一 key 不能跨进程共用（无跨进程 nonce 协调）；`cancel_all` 是账户级。
- 脚本：`validation/nt/test_lighter_two_keys.py`；凭据清单见 `validation/nt/findings.md` §4。

## 4. T4 — OKX amend / clOrdId 幂等（源码 + 测试套件就绪，实跑 BLOCKED）

- amend 是 **modify-in-place**：订单在 amend 检查期间仍在簿上（第三方镜像原文），
  `ordId` 不变；**队列优先级是否保留没有官方明文**，只能 demo 实测。
- `clOrdId`：32 字符、字母开头、**区分大小写**；唯一性范围是「当前 pending 订单」
  （账户级），终态后可复用（*may*）；**重复提交的确切错误码无公开文档**，必须 demo 观测。
- 幂等契约：重试必须复用同一 `clOrdId`；重发前先 `GET /trade/order?clOrdId=`。
- `validation/venue/test_okx_idempotency.py`（stdlib，`--simulate` 8/8 通过）+
  `checklist.md`；在 VPS 上带 `OKX_SIMULATED=1` 跑即可得到证据表。

## 5. 结论与下一步

1. **可以开始回测的部分**：cursor OU 框架、跨场所 basis 统计、NT v2 适配器构造、L2 采集。
2. **不能下结论的部分**：p_fill/markout 的绝对数值（模型未标定）、OKX amend 队列优先级、
   OKX clOrdId 重复错误码、Lighter testnet 实际多 key 下单。
3. **最短补齐路径**：
   - 提供一台能连 OKX 的 VPS + `ARBTWIN_PROXY`（或在本机配代理）；
   - 提供 OKX demo key、Lighter testnet 两个 key；
   - 跑 `validation/venue/test_okx_idempotency.py --simulate` → demo；
   - 跑 `validation/nt/test_lighter_two_keys.py`（testnet）；
   - 用成交通道数据重标定 `validation/fill` 的 p_fill，再 A/B `n_slices=1` vs 2–4。
4. 验收线（沿用 `research/SYNTHESIS.zh.md` §11）：完成率 ≥40%、单次净额 ≥2 bps、
   abort ≤0.5×单次毛利；p_fill 模拟与实盘偏差 <20% 才加规模。

## 6. 产物索引

| 项 | 文件 |
|---|---|
| 计划/环境/数据 | `validation/PLAN.zh.md` |
| T1 | `validation/ou/{findings.md,ou_calibrate.py,cross_venue.py,results_*.json}` |
| T2 | `validation/harness/`、`validation/fill/{record_lighter.py,fill_model.py,metrics.py,analysis.py}` |
| T3 | `validation/nt/{findings.md,test_lighter_two_keys.py}` |
| T4 | `validation/venue/{findings.md,checklist.md,test_okx_idempotency.py}` |
| selftest | `validation/reports/harness-selftest.md` |