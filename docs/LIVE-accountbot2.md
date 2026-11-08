# accountbot2 实盘接入清单（2026-11-08）

> 本文件**不含任何密钥**。凭据只存在于 VPS `/opt/arbtwin/.env`（`640 arbtwin:arbtwin`）。
> 密钥曾在聊天中出现过，验证完成后请到两所后台轮换。

## 1. 账户事实（已核实部分）

| 项 | 值 | 来源 |
|---|---|---|
| Lighter account index | 767659 | 用户提供 |
| Lighter USDC 余额 | **0.000000** | `GET /api/v1/account?by=index&value=767659` |
| Lighter 已注册 API key | **0 个**（`api_keys: []`） | 同上 |
| Lighter 持仓 | 0 | 同上 |
| OKX API key | 已配置（读/交易），IP 白名单 155.254.60.38 / 23.27.186.126 | 用户提供 |
| OKX passphrase | **缺失** | 未提供 |
| 试验资金 | 31.47（据称，交易所侧尚未核到） | 用户提供 |

## 2. 当前不能进实盘的三个硬阻塞

| # | 阻塞 | 证据 | 解除方式 |
|---|---|---|---|
| 1 | OKX 私钥请求无法签名 | `.env` 中 `OKX_API_PASSPHRASE` 为空；OKX 所有私有接口都要求 passphrase | 提供 API key 的 passphrase（创建时自己设的那串，不是登录密码） |
| 2 | Lighter 无抵押品 | 账户 767659 的 USDC 余额为 0 | 从 L1 往 Lighter 充值 USDC（建议 ≥ $10，最小下单就是 $10 名义） |
| 3 | Lighter API key 未注册 | `api_keys: []` | 用持有 767659 的 L1 钱包做一次 `ChangePubKey`（Lighter 网页或 SDK `create_api_key()`）；随后给我 key index + 私钥 |

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
#    改 .env: ARBTWIN_DRY_RUN=0；systemd ExecStart 加 --i-know-what-im-doing
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
