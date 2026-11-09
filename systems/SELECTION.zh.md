# 系统与组件选型：论文/案例作者、开源生态、REA 结论

> 日期 2026-11-10。输入：`systems/{papers,oss,rea,stack}/*`。
> 结论按“近期更新 + 更新频率 + 模块丰富度 + 测试/CI + 许可证 + 与本项目相关性”排序。

## 0. 先给结论

1. **论文作者基本没有官方代码。** Leung/Li、Avellaneda/Stoikov、Guéant、Almgren/Chriss、
   Cont/Kukanov、Bertram 均无作者维护的实现；可用的是第三方复现（见 §1）。
2. **案例作者**：CBB、TierZero、NeuralArb 是**服务端 desk，没有你可获得的客户端二进制**；
   XEMM（djienne）、funding-arb-bot、counterfactual5、pfba 是开源，直接读代码即可。
3. **REA（逆向工程分析）不值得做**：没有合法且有价值的目标；闭源系统的机制已在作者
   自述里公开，开源系统无需逆向。法律边界与工具链见 `systems/rea/findings.md`。
   若 REA 指 Rapid Evidence Assessment：本仓库已做过一轮，建议补 PRISMA-lite 搜索日志。
4. **采购 > 逆向**：开源覆盖约 80%（执行引擎、适配器、数据、部分风控/对账），
   只有基差专用的 20%（配对补仓定价、wave 集成、校准）必须自建。
5. **栈的选择**：现在跑 Stack A（保留自研引擎 + 最小运维）；中期迁移 NT v2 时必须用
   **Python 3.13/3.14**（v2.0.0rc8 无 cp312 wheel）。

## 1. 论文/案例作者 → 可用系统

| 作者/团队 | 官方代码 | 最佳可用参考 | 活跃度 | 许可证 | 可复用组件 |
|---|---|---|---|---|---|
| Leung & Li | 无 | `hudson-and-thames/arbitragelab` | 2024 后停滞 | BSD-3 | OU/XOU 入场-出场带、半衰期 |
| Avellaneda–Stoikov | 无 | `joaquinbejar/market-maker-rs` | 2026-11-09，819 c/90d | MIT | A–S/GLFT 报价、库存偏斜、风险闸门、滚动校准 |
| Guéant | 无 | 同上 `glft.rs` | 同上 | MIT | GLFT 闭式解、`φ=γσ²/2` |
| Almgren–Chriss | 无 | `AshJha0/electronic-trading` | 2026-11-06，74 c/90d | MIT | κ 轨迹、限价/市价拆解、TCA、对冲 |
| Cont–Kukanov | 无 | `AshJha0/electronic-trading/placement.py`（近似） | 同上 | MIT | 挂/打决策 |
| Bertram | 无 | 无值得采用的实现 | — | — | OU 止损/杠杆思路 |
| CBB | 无（GitHub 0 repo） | 闭源；X 文章参数 | — | — | maxDelta、cancelDelta、takerRatio |
| TierZero | 闭源（org 仅无关工具） | 博客 | — | — | 资金费时间窗、成本模型 |
| NeuralArb | 无 GitHub | 博客 | — | — | 子单拆分、clip 上限 |
| XEMM / djienne | **有** | `CROSS_EXCHANGE_MARKET_MAKING_RUST` | 2026-11-09，174 c/90d | **无许可证（只读参考）** | quote/hedge 状态机、库存、Lighter 签名、dry-run |
| funding-arb-bot | 有（快照） | `alexanderlmoran/funding-arb-bot` | 1 commit，停更 | 无许可证 | 重试/平仓、kill-switch 规则 |
| perp-funding-basis-arb | 有 | `pranay123-stack/perp-funding-basis-arb` | 2026-09，3 commits | MIT | HedgeCycle、DEGRADED 再平衡、三方对账、风险阶梯 |
| counterfactual5 | 有 | `counterfactual5/funding-arb` | 2026-11-09，106 c/365d | MIT | **Lighter+OKX 适配器**、rollback executor、结算错配 |

**优先阅读顺序**：market-maker-rs（风险闸门/校准）→ electronic-trading（执行/TCA）→
djienne CEMMR（只读，生产形态）→ counterfactual5（Lighter 适配器）→ arbitragelab（阈值数学）。
**hftbacktest**（MIT，5.0k★）是队列位置/延迟回测的行业标准，正好补 dry-run 的空白。

## 2. 开源平台/组件评分（`systems/oss/scores.csv`）

评分维度：recency、activity（90d commits）、modularity、tests/CI、docs、license fit、
relevance，满分 35。原始 API 数据见 `sources.jsonl`。

| 角色 | 选择 | 分数/许可证 | 说明 |
|---|---|---|---|
| 执行引擎 | **nautilus_trader v2** | 28 / LGPL-3.0 | 唯一同时有官方 OKX + Lighter 适配器的引擎；LGPL 作为库链接可接受 |
| 通用连接 | **ccxt** | 30 / MIT | 只用于只读探测/扩展，绝不放订单路径 |
| 数据采集 | **cryptofeed** | 21 / 许可证不明 | OKX L2/成交/资金费；许可证需先确认 |
| 本地存储 | **duckdb** | 24 / MIT | 嵌入式 OLAP，直接查 Parquet |
| 服务端时序 | **ClickHouse** | 24 / Apache-2.0 | tick 级；单机先用 QuestDB 更轻 |
| 回测 | **LEAN**（研究）或 `hftbacktest`（队列） | 24 / Apache-2.0；MIT | LEAN 无 Lighter，不能做实盘引擎 |
| 风险模式 | **pfba risk.py** | MIT | 残差/基差/场所集中度 + 锁存 kill switch |
| 运维 | Prometheus + Grafana | Apache-2.0 / AGPL-3.0 | Grafana 仅内部使用，避免 AGPL 网络分发问题 |

**不要采用**：Hummingbot `ArbitrageExecutor`（双腿市价、无部分成交处理）、Freqtrade 核心
（单场所、GPL）、Backtrader（2024 后停更）、Kafka/Redpanda（两场所不需要）。

**可直接抄的 5 个组件**：pfba 的 `HedgeCycle`、三方对账、先补后减再平衡、风险升级阶梯；
counterfactual5 的 Lighter 适配器。

## 3. REA：能不能逆向闭源项目？

**结论：不做二进制逆向；做公开面 + clean-room。**

- **没有目标**：CBB/TierZero/NeuralArb 是服务端系统，没有你这一侧的客户端二进制；
  它们的机制、参数、事故复盘已在公开文章里，`research/x-medium/findings.md` 已提炼成 R1–R11。
- **开源的不需要逆向**：XEMM、funding-arb-bot、counterfactual5、pfba 直接读。
- **法律边界**：公开文档/API/论文/自有账户流量 = 绿区；绕过认证/限速、反混淆、
  泄露代码 = 红区（CFAA、DMCA §1201、EU 2009/24/EC、商业秘密法、场所 ToS）。
- **工具链**：Ghidra/IDA/radare2/Frida/mitmproxy/Wireshark/jadx；但本项目没有合法高价值目标。
- **若 REA = Rapid Evidence Assessment**：用 PRISMA-lite 搜索日志 + 纳入标准 + 置信分级，
  把现有 `research/` 的实践制度化。
- **替代方案**：公开面情报（API/SDK、错误码、作者文章、专利、招聘）+ clean-room 规格；
  预算 0 小时二进制逆向。

## 4. 三套参考栈（`systems/stack/findings.md`）

| 栈 | 适用 | 核心组件 | 触发条件 |
|---|---|---|---|
| **A 最小** | 现在 | 自研引擎 + Parquet/SQLite/JSONL + Prometheus textfile + systemd + restic + sops/age | 单操作员、两场所、≤$150 裸腿 |
| **B 机构** | 验证后 | NT v2 LiveNode + NATS + QuestDB/ClickHouse/ArcticDB + 独立 risk/recon 服务 + OTel/LGTM + OpenBao | 资金/团队规模上升，或单 VPS 故障已造成实际损失 |
| **C OSS-max** | 研究/扩展 | cryptofeed/lighter-python → NT；Dagster 研究面；pfba/counterfactual5 挖组件 | 需要多场所/多策略并行 |

**路线图（P0，本周）**：restart recovery、`client_order_index` 幂等、`PairBook` VWAP 修复、
`abort_cost_bps`、Prometheus 8 信号、systemd watchdog、备份演练、依赖锁定。
**P1（2–4 周）**：NT v2（Python 3.13/3.14）离线回测、tick 录制、队列/延迟模型、testnet 影子。
**P2（2–3 月）**：移植 NT `TwoLegBasisStrategy`，影子对账 ≥2 周后再切实盘。

## 5. 最终建议

- **执行底座**：保留自研引擎到 P1 结束；NT v2 只做影子/回测，稳定版再切。
- **优先采用的组件**：market-maker-rs 的风险闸门/校准、electronic-trading 的 κ/TCA、
  counterfactual5 的 Lighter 适配器、pfba 的状态机/对账、hftbacktest 的队列回测。
- **只读参考（许可证不允许复制）**：djienne 系列（无 license）、funding-arb-bot（无 license）。
- **REA**：0 小时；把精力放在配对补仓定价与执行鲁棒性，这是唯一没人开源的部分。