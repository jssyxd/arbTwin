# RWA 双腿实盘研究：6 轮迭代计划（2026-11）

> 目标：在 `192.168.1.98` 上用 OKX ↔ Lighter 双腿，交易 **HIP-3 / RWA 代币化股票**，
> 每轮都做数据校验、数据测试、实盘执行、diff、多 agent 评审，评审通过的部分才进仓库。
> 约束：账户约 **31.47 USDC**（Lighter 实测权益 27.629；OKX 待 passphrase 确认），
> 单腿名义 $10–12，一次只做一个标的、一轮，亏损两轮即停。

## 0. 原则（poteto-mode）

- **Prove It Works**：每轮的结论必须来自实际命令输出、实盘成交/持仓日志，不接受“看起来对”。
- **Sequence Verifiable Units**：一轮只改一个可验证单元；先数据校验，再测试，再实盘，再 diff。
- **Never Block on the Human**：可逆工作直接做；只有缺凭据/不可逆动作才停。

## 1. 环境与部署状态

| 项 | 状态 |
|---|---|
| VPS | `192.168.1.98`（Ubuntu 24.04，Python 3.13.7，25 G 本地空闲） |
| OKX 出口 | 本机 DNS 被污染；已建 **LA 跳板 HTTP 代理**（`127.0.0.1:3128` → tinyproxy → losangeles），`doctor` OKX 528 markets |
| Lighter | 直连可达；account `767659`，key index 4/5；权益 27.629 USDC |
| SMB | `//192.168.1.6/iflow` 需要 SMB 账号密码（当前未挂载）；挂载点 `/mnt/iflow_database` |
| OKX 私有 API | **缺 `OKX_API_PASSPHRASE`**；未通过私有鉴权，live 下单 BLOCKED |
| 代码 | `~/arbTwin`，commit `ccaa796`，venv `.venv` |
| 数据带 | `arbtwin run` dry-run 写 `state/snapshot.jsonl`（含 `okx_spread_bps`/`lt_spread_bps`） |

## 2. 标的宇宙（RWA / HIP-3，排除 BTC/ETH）

优先（来自 2026-11-09 配对报告，剔除了 ANTHROPIC 单位错、KIOXIA 陈旧报价）：

`KORU, SAMSUNG, SKHYNIX, BMNR, MSTR, CBRS, US100, IBM, QQQ, AAOI, CRWV, US500, TSLA, CRCL, AAPL, TSM, MRVL, XAG, NVDA, SPY, XAU`

首轮候选（净额 p95−b* 为正且盘口可接受）：**QQQ、US100、IBM、CBRS、KORU、SAMSUNG、SKHYNIX**。
MSTR 在 2026-11-11 MSCI 决定前不做；GEV/ASML/NOW/IREN 因 Lighter 价差 33–83 bps 不做。

## 3. 六轮表

| 轮 | 阶段 | 数据校验 | 数据测试 | 实盘 | diff / 评审 | 通过标准 |
|---|---|---|---|---|---|---|
| **R1** | 前期 1：数据与准入 | 14 天 1m + 资金费 + 两所价差；单位比、缺口、陈旧报价 | `doctor`、`calibrate`、`screen`；tick 级价差分布 | 1 个标的、$10/腿、1 轮 | `config/symbols.rwa.yaml`、`docs/RWA-ROUND1.md`；glm+qwen 各 1 agent | 双边成交；无裸腿超时；实际净额 ≥ 0；数据缺口 <0.1% |
| **R2** | 前期 2：执行 P0 | 实盘成交/持仓日志对账 | `on_fill` VWAP、`round_pnl` 单位、`abort_cost`、clOrdId 幂等单测 | 同标的 2 轮 | `wave.py`/`engine.py`/`tests/` | 失败成本可见；重发不双开；单测全绿 |
| **R3** | 中期 1：补仓定价 | 首腿成交后的盘口/成交序列 | `PairBook` + 三区 `topup_limit` 回放 | 1–2 轮 | `wave.py` 补仓路径 + 回放报告 | 补仓后合并基差 ≥ b*；无同时刻 mid 决策 |
| **R4** | 中期 2：分片/再平衡 | 分片成交率、时间到配对 | A/B `n_slices=1` vs 3；DEGRADED 再平衡 | 2 轮 | `wave.py` 分片 + 配置 | 完成率提升且 abort 成本不升 |
| **R5** | 后期 1：风控/对账 | 断线、拒单、持仓漂移注入 | HALTED、fail-closed、kill switch | 2 轮 | `engine.py`/`reconcile.py` | 无未对账 delta；kill 5 s 内平仓 |
| **R6** | 后期 2：稳定性 | 连续 3 天 tape | 回放 + walk-forward + 复盘 | 2 轮 | 最终 CHANGELOG + 报告 | 完成率 ≥40%、净额 ≥2 bps、回撤可控 |

每轮固定动作：
1. `data-check`：`scripts/round_check.py` 输出缺口/单位/价差/资金费表；
2. `pytest`：相关单测；
3. 实盘（或 dry-run，若凭据未齐）：保存 `state/trades.jsonl`、`snapshot.jsonl`、`alerts.jsonl`；
4. `git diff` + 生成 `docs/ROUNDn-DIFF.md`；
5. 两个不同模型的 agent 独立评审（glm-5.3、qwen3.8-max），逐条给出 PASS/FAIL；
6. 仅两边 PASS 的 hunk 保留，提交并在 `CHANGELOG.md` 写“轮次 / 证据 / 保留项”。

## 4. 实盘风控（当前资金）

- 单腿名义 `$10–12`，杠杆 ≤3x；一次一个标的；最大同时持仓 1。
- 入场：`|basis| ≥ p95` 且 `maker_net ≥ 2 bps`；否则只记录。
- 裸腿上限 `$15`；90 min 未配对 → 被动 unwind；逆向 25 bps → 退出队列。
- 连续 2 轮净亏或单日回撤 >5% → 停手，保留日志，进入复盘轮。
- 所有实盘轮必须写 `state/rounds.jsonl`（round_id、symbol、entry/exit、abort_cost、unhedged_ms）。

## 5. 数据带与 cron

- `arbtwin-rwa.service`：`ARBTWIN_DRY_RUN=1 arbtwin run --interval 1`，写 `snapshot.jsonl`；
  先 dry-run 采集，凭据齐后切 live（`DRY_RUN=0`，`ARBTWIN_FILL_SOURCE=positions`）。
- `rwa-data-check.timer`：每小时跑 `scripts/round_check.py`，写 `state/checks/`。
- `rwa-screen.timer`：每天 02:00 重跑 `screen`，只更新 `symbols.rwa.yaml`，不自动上线。
- 数据落到 SMB（凭据到位后）：`/mnt/iflow_database/arbtwin/rwa/`；本地保留 7 天滚动。

## 6. 评审协议（多 agent）

- 每轮两个评审 agent，模型必须不同（glm/qwen），互不可见对方结论。
- 输入：`git diff`、`state/*.jsonl` 实盘证据、轮次报告、宪法
  `https://github.com/jssyxd/quant-research-constitution`。
- 输出：逐 hunk `KEEP / DROP / FIX` + 证据；两个都 KEEP 才保留；冲突由 captain 复算。
- 评审不通过不进入下一轮；修复后重新跑同轮数据测试。

## 7. 当前阻塞

1. `OKX_API_PASSPHRASE`（必需，live 私有 API）。
2. SMB `//192.168.1.6/iflow` 的账号/密码（>100 G 存储）。
3. 若 OKX 账户权益 <$5，单腿名义需降到 OKX 最小值以下，需先充值或只做 Lighter 侧研究。