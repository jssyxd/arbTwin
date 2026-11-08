# accountbot2 实盘接入清单（2026-11-08）

> 本文件**不含任何密钥**。凭据只存在于 VPS `/opt/arbtwin/.env`（`640 arbtwin:arbtwin`）。
> 密钥曾在聊天中出现过，验证完成后请到两所后台轮换。

## 1. 账户事实（已核实部分）

| 项 | 值 | 来源 |
|---|---|---|
| Lighter account index | 767659 | 用户提供 |
| Lighter USDC | **$27.629041 可用**（`collateral` / `available_balance` / `total_asset_value`）；`assets[].balance` 字段恒为 `0.000000`，不能用它算权益 | `GET /api/v1/account?by=index&value=767659`，2026-11-08 |
| Lighter 已注册 API key | **0 个**（`api_keys: []`） | 同上 |
| Lighter 持仓 | 0 | 同上 |
| OKX API key | 已配置（读/交易），IP 白名单 155.254.60.38 / 23.27.186.126 | 用户提供 |
| OKX passphrase | **缺失** | 未提供 |
| 试验资金 | 31.47（据称，交易所侧尚未核到） | 用户提供 |

## 1.1 2026-11-08 21:0x 复核（截图 + 实盘 API）

| 项 | 事实 | 来源 |
|---|---|---|
| Lighter API key 4 / 5 | **已注册**，`NONCE = 1`（新生成） | Lighter「API 密钥」页截图 |
| Lighter 费率 | **挂单 0% / 吃单 0%** | Lighter 交易页截图 |
| Lighter 权益 | **$27.62**（`总余额 27.62 USDC`、`交易净值 $27.62`、`全仓杠杆 0x`） | 同上 + `available_balance` |
| 闭市机制 | CXMT 显示「指数价格冻结在最后一个外部价格，mark 最多偏离 ±20%」 | 截图 —— 这正是 `session` gate 要挡的场景 |
| OKX passphrase | 已收到（仅写入主机 `/opt/arbtwin/.env`，640，不入仓库） | 用户提供 |
| OKX 访问 | **50110：IP 白名单只含 155.254.60.38 / 23.27.186.126**；本机出口 95.40.146.30 被拒 → 一切 OKX 私有读取必须在 VPS 上执行 | `GET /api/v5/account/balance` |

## 1.2 2026-11-08 22:1x 实机核实（从白名单 IP、强制 IPv4）

| 项 | 实测 | 备注 |
|---|---|---|
| OKX `totalEq` | **31.4379 USDT**（USDT eq/avail 31.4794） | `/api/v5/account/balance` |
| OKX 费率 | **Lv1：maker −0.0002 / taker −0.0005** | OKX 的负号=支出 ⇒ maker **2 bps**、taker **5 bps**，与 `config/venues.yaml` 一致 |
| OKX 持仓 | 0 | — |
| Lighter 权益 | **27.629041 USDC** | `equity_usd()`（账户级字段，非 `assets[].balance`） |
| Lighter 持仓 | 0 | — |
| Lighter key 4 / 5 | **已注册**：`nextNonce` 返回 `code 200, nonce 1`（两把都是） | 只读查询，非猜测 |
| 行情通道 | OKX WS `connected`（age 0.06s）、Lighter WS `connected`（0.28s）、Lighter 盘口 238/189 与 63/124 档 | 在同一台机上实测 6 秒 |
| **合计资金** | **≈ $59.1** | 31.48（OKX）+ 27.63（Lighter） |
| Lighter 杠杆 | **0x**（截图） | 开仓前需设 3x，否则保证金口径不符 |
| 双栈坑 | 首次调用报 `50110 ... 2400:8d60:5::1:5774:50ed not in whitelist` | 白名单只有 IPv4；已加 `ARBTWIN_FORCE_IPV4=1`（默认开），强制 IPv4 解析 |

## 1.3 实盘已开启（2026-11-08 22:4x UTC+8）

| 项 | 配置 / 证据 |
|---|---|
| 开关 | `.env`: `ARBTWIN_DRY_RUN=0` + `ARBTWIN_FILL_SOURCE=positions`；unit `ExecStart ... --i-know-what-im-doing` |
| 成交来源 | **持仓增量对账**（`arbtwin/reconcile.py`）：每 poll 读两所持仓，delta→`wave.on_fill`；账面（`state/positions.json`）持久化，启动时 `seed()` 采纳现有持仓，**不会把昨天的仓位当成交** |
| 启动动作 | 两所 `cancel_all`（清昨日挂单）→ `ledger.seed()` → 正常轮询 |
| 规模上限 | `ARBTWIN_MAX_NOTIONAL=15`（每腿）、`ARBTWIN_TARGET_LEVERAGE=3`、`MAX_MARGIN_USAGE_PCT=0.35` |
| 服务 | `active`，MainPID 35591，`dry_run=False` 的 snapshot 每 1.5s 刷新；周一会话关 → 两标的 `BLOCK`（正确） |
| 门禁（仍生效） | 会话关闭禁开仓、mark 偏离、深度密度、薄盘口、OI 利用率、强平距离、`min_edge` |

## 2. 当前不能进实盘的硬阻塞（更新）

| # | 阻塞 | 证据 | 解除方式 |
|---|---|---|---|
| 1 | ~~OKX passphrase 缺失~~ **已解除** | passphrase 已收到；但仍受 IP 白名单限制（见 1.1） | 把 passphrase 写进主机 `.env`（需 SSH 可达） |
| 2 | ~~Lighter 无抵押品~~ **已解除** | `available_balance = 27.629041`（此前读 `assets[].balance` 误判为 0） | 无需充值；引擎已改用账户级总字段 |
| 3 | ~~Lighter key 未注册~~ **已解除** | 截图显示 index 4 / 5 均存在，NONCE 1 | 无需操作（公开 API 的 `api_keys` 字段不返回它们，之前误判） |
| 4 | ~~本 build 拒绝 live~~ **已解除** | `ARBTWIN_FILL_SOURCE=positions` 接入持仓增量对账 | 需要 OKX+Lighter 凭据齐全（已满足） |
| 5 | Lighter 杠杆 0x | 交易页截图 `全仓杠杆 0x` | 开仓前需 `update_leverage` 到 3x（SDK 交易未实现，属下一批代码） |

> 两腿策略要求两所都有保证金。任何一侧为 0，引擎的 `_target_notional()` 返回 0，不会下单。

## 3. $31.47 的真实规模上限

| 约束 | 数值 | 说明 |
|---|---|---|
| Lighter 最小下单 | **$10 名义** | `min_quote_amount = 10 USDC`，这是硬地板 |
| 一轮两腿名义 | ≈ $20–24 | Lighter $10–12 + OKX 对齐同名义 |
| 3x 杠杆下占用保证金 | ≈ $3.3–4 / 所 | 剩余自由保证金仍可容纳一次失败重试 |
| 单轮毛利（15 bps 基差） | ≈ $0.015 | $10 × 15bps |
| 单轮净利（4 bps 往返费） | ≈ **$0.011** | 也就是 1 美分级别 |
| 单次 abort 成本 | $0.004–0.03 | 撤单 + 被动平掉裸腿的摩擦 |
| 资金费补贴 | $0.001–0.003 / 天 | Lighter 10.5% APR 作用在 $10 上 |

**结论**：$31.47 足够做"机制验证"（一轮能否完整开→持→平、成交/中止率、真实滑点），
但**不足以产生有意义的收益**——一天的波动就是几美分。若要当作收益引擎，单所资金需 ≥ $200。

## 3.1 RWA-only 的标的选择（你说的是"RWA 波动偏差 bps"）

`config/symbols.starter.yaml` 里混了两个非 RWA 代币（GRAM / VVV）。若严格只做 RWA
（代币化股票/ETF），可选集与 14 天实测如下：

| 标的 | 14d 基差 p95 | σ | 半衰期 | 1% 深度（OKX） | 备注 |
|---|---:|---:|---:|---:|---|
| **COIN** | 10.4 bps | 4.77 | 0.88 h | $1.33M | b* 6.8（价差最紧）→ v1 首选 |
| **VVV** | 11.4 bps | 5.72 | 1.70 h | — | b* 8.1，资金费 14% APR |
| **LIT** | 11.4 bps | 6.04 | 1.60 h | — | b* 6.5；Lighter 自家代币，规则风险最高 |
| SOXL | 10.8 bps | 6.34 | 0.76 h | $4.55M | **b* 11.5 > p95 → 暂不做**（Lighter 侧价差 4.3 bps） |
| GRAM | 15.6 bps | 7.38 | 0.86 h | — | **b* 16.0 > p95 → 暂不做**（OKX 侧价差 9.4 bps） |
| MSTR | 12.4 bps | 5.90 | 1.30 h | $2.9M | **2026-11-11 MSCI 决定**，11-12 前不碰 |
| ORCL / MRVL / EWY / XPT / SNDK | 8.2–10.0 bps | 4.1–5.2 | 0.8–1.7 h | — | 按 b* 口径 p95−b* < +1bps，暂不做 |

在 $31.47 资本下**只能开一只**（Lighter 最小 $10 名义 + 保证金占用），因此：

```yaml
# config/symbols.starter.yaml 只留 COIN；同时把上限写死，避免误配
limits:
  max_open_symbols: 1
  max_notional_usd: 15      # 每腿 ~$10-15，正好压在 Lighter 最小单之上
```

## 3.2 诚实预期（30 天、分 session 的事件研究）

| 标的 | 触发 | 出场 | b\* | 单次捕获 | 单次净额 | 事件数/30d | 完成率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SOXL | 12.4 | 2.85 | 9.27 | 14.78 | **+4.12 bps** | 5 | 80% |
| COIN | 10.54 | 3.92 | 8.12 | 13.06 | **+3.71 bps** | 5 | 100% |

即 **每月约 10 次机会**（两标的合计），$12/腿 → 每次 ≈ $0.004~0.005。
**结论：$27.62 的账户是"机制验证"规模，不是一个收益引擎。**

## 4. 拿到缺失项后的启动顺序

```bash
# 1) 写入 .env（640 arbtwin），确认三件事
grep -c . /opt/arbtwin/.env
grep -q '^OKX_API_PASSPHRASE=..*' /opt/arbtwin/.env && echo ok

# 2) 只读体检：两所规格 + 3 标的 ratio
cd /opt/arbtwin && .venv/bin/python -m arbtwin.cli doctor

# 3) 干跑 200 轮（当前就是这个状态）
systemctl status arbtwin --no-pager | head -5

# 4) 单标的、最小合法名义进实盘（先 SOXL，$10–12/腿）
#    ⚠️ 本 build 拒绝 live：ARBTWIN_DRY_RUN=0 会在启动时 exit 2
#    （成交对账未落地）。以下步骤在订单状态轮询完成前不可执行。
#    改 config/symbols.starter.yaml 只留 SOXL

# 5) 观察 24h
jq -r 'select(.level!="info")|[.ts,.scope,.message]|@tsv' state/alerts.jsonl
jq -r '[.ts,.symbol,.state,.pnl_bps]|@tsv' state/rounds.jsonl
```

判定标准（不达标就回 dry-run，不要加钱）：

* `rounds.jsonl` 出现完整 `flat` 收尾，且 `pnl_bps > 0`；
* 单轮 `abort_unwind` 占比 < 30%；
* 无 `critical` 告警（单腿裸奔 / 强平距离 <18% / 拒单率 >20%）。

## 5. Lighter API key 使用约定

| key index | 用途 | 现状 |
|---|---|---|
| 4 | 挂单/撤单（`post_only`） | 待注册 |
| 5 | 一次性 IOC 升级（`exit_deadline_s` 之后、风险止损） | 待注册；代码已按此拆分（`_trader(taker=...)`，两个单测） |

配置项：`LIGHTER_API_KEY_INDEX=4` + `LIGHTER_API_KEY_INDEX_TAKER=5`，
对应两把私钥分别写入 `LIGHTER_API_PRIVATE_KEY` / `LIGHTER_API_PRIVATE_KEY_TAKER`。

> 同一个 key 的交易严格串行（一个 300ms 的 taker 会拖住后续 post-only），
> 所以两把 key 是正确做法；拆 key 的代码改动已记录在 `CHANGELOG` 的后续项。
