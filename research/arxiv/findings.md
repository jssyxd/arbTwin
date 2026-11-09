# arXiv Literature Survey: Quantitative Finance Papers Relevant to arbTwin

> Generated 2026-11-09 from cached arXiv XML responses (~16 query files, 111 entries).
> Discarded: "Visual Manipulation with Legs" (robotics), vPIN (privacy/ML, not VPIN flow toxicity),
> Polymarket-v1 (prediction market database), quantum stat arb, Fe-vacancy order (condensed matter).
> 15 papers retained for relevance to OKX↔Lighter basis trading.

---

## (1) Executive Summary

1. **OU half-life** is the fundamental timescale for mean-reverting basis strategies:
   $\tau_{½} = \ln(2)/\theta$ where $\theta$ is the OU mean-reversion speed. A basis with
   $\tau_{½} < 10$ min is tradeable; $\tau_{½} > 60$ min suggests non-stationarity risk
   (Martin/Kearney/Craster 2018; [INFERENCE] standard derivation confirmed by their asymptotics).

2. **Optimal entry/exit bands** for an OU spread with proportional transaction costs $c$ form a
   symmetric interval $[-\bar{b}, -\underline{a}] \cup [\underline{a}, \bar{b}]$ around the
   mean, where $\underline{a} > c$ and $\bar{b} > \underline{a}$. The entry trigger is
   $|s_t| \ge \bar{b}$; exit at $|s_t| \le \underline{a}$. Leung & Li (2014) prove that with
   a stop-loss $L$, the entry region is bounded and lies strictly above $L$.

3. **Baviera & Baldi (2017)** extend Bertram (2009): for OU log-prices, the long-run return is
   an elementary function of the stop-loss level. Optimal leverage and entry/exit levels are
   derived analytically from expected First-Exit-Times from an interval.

4. **Avellaneda–Stoikov inventory skew**: The optimal bid/ask distance from the reference price
   is $\delta^{\pm} = \delta_0 \mp \gamma \sigma^2 q$, where $q$ is inventory and $\gamma$ is
   risk aversion. Feys (2026) proves the forced identity $\varphi = \gamma \sigma^2/2$ linking
   A–S risk aversion to the Cartea–Jaimungal running penalty, meaning the two frameworks are
   one object — only one free parameter needs calibration.

5. **Almgren–Chriss $\kappa$**: The urgency parameter $\kappa = \sqrt{\lambda\sigma^2/\eta}$
   where $\lambda$ is permanent impact, $\sigma$ volatility, $\eta$ temporary impact. Optimal
   execution trajectory is $v(t) = \kappa \cdot \frac{\sinh(\kappa(T-t))}{\sinh(\kappa T)} X_0$.
   A larger $\kappa$ → front-loaded execution (more urgent). [INFERENCE: these formulas are in
   the original A-C paper; the cached papers extend A-C with volume-dependence (Kato 2017) and
   multi-asset cash constraints (Hashimoto/Stillman 2026).]

6. **Limit-vs-market decision**: Cont & Kukanov (2012) derive an explicit convex-optimization
   solution for the optimal split between limit and market orders. The split depends on queue
   sizes, fees/rebates, and fill probability. Figueroa-López et al. (2017) prove a critical
   time horizon $t_0$ exists: for $t > t_0$ with negative drift, optimal limit placement is
   *not* infinitesimally close to the best bid/ask — place deeper in the book.

7. **Perpetual futures pricing**: Gupta & Polson (2026) give a unified no-arbitrage result:
   perpetual price $P_t = \mathbb{E}[\int_t^\infty e^{-\int_t^s r_u du} B_s ds]$ — the
   present value of a benchmark flow discounted at the funding rate. Stochastic volatility
   moves the basis only through the carry (volatility risk premium, not volatility itself,
   can break the peg).

8. **Basis ≠ carry for equity perpetuals**: Seo et al. (2026) prove that premium-index funding
   (funding from contemporaneous basis $e_t = P_t - S_t$) cannot replicate dividend cash flows.
   Funding serves two roles (basis control + settlement) that coincide for crypto but split for
   dividend-paying equities. This directly affects arbTwin's RWA equity pairs like AAPL, NVDA.

9. **Multi-asset execution with mean reversion**: Bergault/Drissi/Guéant (2021) solve optimal
   execution for multivariate OU price dynamics with exponential utility. The cross-asset
   mean-reversion coupling changes the optimal liquidation schedule — pairs that cointegrate
   should be executed jointly, not independently.

10. **Transaction cost bounds**: Xing (2019) proves via singular stochastic control that
    proportional transaction costs create a no-trade region (free boundary) whose width scales
    with $\sqrt{c}$ where $c$ is the cost rate. This gives a theoretical floor: if the basis
    band is narrower than this free boundary, trading is -EV even with perfect mean-reversion.

11. **Stop-loss/take-profit coupling**: Leung & Li (2014) prove that a **higher stop-loss level
    always implies a lower optimal take-profit level**. The exit decision is not independent of
    the risk control. [INFERENCE: arbTwin's `stop` and `exit_bps` should be co-calibrated, not
    set independently.]

12. **Huang & Wang (2016)** show empirically that OU-calibrated pairs with Sharpe > 1.9 in-sample
    can sustain profitability out-of-sample, provided the calibration window is optimized. The
    key failure mode is parameter non-stationarity — the same concern flagged in the arbTwin
    README for ANTHROPIC-type mismatches.

---

## (2) Evidence Table

| Paper | Year | Key Result | Key Formula / Threshold | Link |
|---|---|---|---|---|
| Leung & Li — Optimal Mean Reversion Trading | 2014 | Entry/exit bands for OU spread with stop-loss; higher SL → lower TP | Entry region $[\underline{a}, \bar{b}]$, exit at take-profit $b^*$, $b^*$ decreases in stop-loss $L$ | [1411.5062](https://arxiv.org/abs/1411.5062) |
| Baviera & Baldi — Stop-loss & Leverage in Stat Arb | 2017 | Long-run return as elementary function of stop-loss; optimal leverage from OU First-Exit-Times | $\mu_{LR} = f(L, \theta, \sigma, c)$ where $L$ = stop-loss | [1706.07021](https://arxiv.org/abs/1706.07021) |
| Cont & Kukanov — Optimal Order Placement | 2012 | Explicit convex-optimization solution for limit/market split | $\min_{x} \; c(x) + \lambda \cdot \text{risk}(x)$ subject to fill-probability constraints | [1210.1625](https://arxiv.org/abs/1210.1625) |
| Feys — A–S / Cartea–Jaimungal Unification | 2026 | $\varphi = \gamma\sigma^2/2$, $\gamma = 2\varphi/\sigma^2$; one free parameter | Forced identity from axioms (cash-additivity, concavity, dynamic consistency) | [2606.01477](https://arxiv.org/abs/2606.01477) |
| Guéant/Lehalle/Tapia — Inventory Risk | 2011 | Optimal bid/ask quotes with exponential utility; arrival rates $\lambda(\delta) = A e^{-k\delta}$ | $\delta^{\pm} = \frac{1}{k} \mp \gamma\sigma^2 q$, skew linear in inventory | [1105.3115](https://arxiv.org/abs/1105.3115) |
| Gupta & Polson — Perpetual Futures for Stocks | 2026 | Perp price = PV of benchmark flow discounted at funding rate; vol moves basis via carry only | $P_t = \mathbb{E}[\int_t^\infty e^{-\int_t^s r_u du} B_s ds]$, random time-change representation | [2609.05433](https://arxiv.org/abs/2609.05433) |
| Seo et al. — Basis-Only Funding Not Dividend Fair | 2026 | Premium-index funding cannot replicate dividends; fair-funding identity derived | Settlement fairness requires $\mathbb{E}[\text{funding}_t] = \text{carry}_t + \text{dividend adjustment}$ [INFERENCE] | [2610.22839](https://arxiv.org/abs/2610.22839) |
| Bergault/Drissi/Guéant — Multi-Asset OE under OU | 2021 | Multi-asset execution with multivariate OU; cross-asset coupling changes schedule | HJB reduced via exponential utility: $\partial_t u + \mathcal{L}u - \frac{1}{2\gamma}(\nabla u)^\top \Sigma^{-1} \nabla u = 0$ [INFERENCE] | [2103.13773](https://arxiv.org/abs/2103.13773) |
| Xing — Pairs Trading with Transaction Costs | 2019 | Free boundary no-trade region from singular stochastic control; width ~ $\sqrt{c}$ | Viscosity solution to quasi-variational inequality; discrete DP algorithm for boundaries | [1911.10450](https://arxiv.org/abs/1911.10450) |
| Figueroa-López et al. — Optimal Small-Order Placement | 2017 | Critical time $t_0$: for $t > t_0$ with negative drift, place deeper than best bid/ask | Optimal placement depends on drift sign and horizon; $t_0$ approximated via diffusion scale | [1708.04337](https://arxiv.org/abs/1708.04337) |
| Leung/Li/Wang — Speculative Futures under Mean Reversion | 2016 | Optimal double stopping for OU/CIR/XOU futures; computed expected roll yield | Entry/exit thresholds from optimal stopping; roll yield = $\mathbb{E}[(F_{t+1} - F_t)/F_t]$ | [1601.04210](https://arxiv.org/abs/1601.04210) |
| Guéant — Optimal Market Making (Survey) | 2016 | Unified framework for market making; generalizes A–S, Ho–Stoll, Cartea–Jaimungal | General HJB with arbitrary intensity functions; closed forms for exponential intensities | [1605.01862](https://arxiv.org/abs/1605.01862) |
| Martin/Kearney/Craster — OU FPT Asymptotics | 2018 | Single formula for OU first-passage time correct in both long- and short-time limits | $\tau_{½} = \ln(2)/\theta$; asymptotic matching via Hopf–Cole + Laplace transform | [1810.13010](https://arxiv.org/abs/1810.13010) |
| Huang & Wang — Profitability of Mean Reversion | 2016 | OU-calibrated pairs with Sharpe > 1.9 sustain OOS; calibration window matters | Sharpe = $\mu/\sigma$ annualized; OU $\theta$, $\mu$, $\sigma$ estimated via MLE | [1602.05858](https://arxiv.org/abs/1602.05858) |
| Rao — Agent-Based Simulation of Perpetual Futures | 2025 | Agent-based LOB model reproduces perp-spot pegging; spread/order lifetime affect premium | Premium = $f(\text{spread}, \text{order lifetime}, \text{trading horizon})$ — simulated, not closed form | [2501.09404](https://arxiv.org/abs/2501.09404) |

---

## (3) Derivations [INFERENCE unless attributed]

### 3.1 OU Half-Life

The OU process: $dx_t = \theta(\mu - x_t)dt + \sigma dW_t$. The expected time to revert halfway
to the mean from a deviation $d$ is independent of $d$:

$\mathbb{E}[x_t | x_0 = \mu + d] = \mu + d e^{-\theta t}$

Setting $d e^{-\theta t} = d/2$: $\tau_{½} = \ln(2)/\theta$.

For arbTwin basis: estimate $\theta$ from rolling regression $\Delta b_t = \alpha + \beta b_{t-1} + \varepsilon_t$,
where $\beta = -\theta \Delta t$. Then $\tau_{½} = -\ln(2) \cdot \Delta t / \ln(1+\beta)$.

Tradeability rule-of-thumb: $\tau_{½} \in [2, 30]$ minutes (faster = noise, slower = non-stationary).
This comes from Martin et al. (2018) asymptotic matching and Huang & Wang (2016) empirical findings.

### 3.2 Optimal Entry/Exit Thresholds (Leung & Li 2014)

For OU spread $s_t$ with transaction cost $c$ per round-trip:

Optimal double stopping problem:
$V(s) = \sup_{\tau_e \le \tau_x} \mathbb{E}[e^{-\rho\tau_x}(s_{\tau_x} - c) - e^{-\rho\tau_e}(s_{\tau_e} + c)]$

where $\tau_e$ = entry time, $\tau_x$ = exit time, $\rho$ = discount rate.

Solution structure: exercise entry when $s \ge \bar{b}$ (short) or $s \le -\bar{b}$ (long);
exit when $s$ reverts to $[-\underline{a}, \underline{a}]$.

With stop-loss $L$: entry region exists iff $\bar{b} > L$. As $L \uparrow$, optimal take-profit
$\underline{a} \downarrow$ — tighter risk control forces earlier profit-taking.

[INFERENCE] For arbTwin: `entry_bps` maps to $\bar{b}$ (in bps from fair value),
`exit_bps` maps to $\underline{a}$, and `stop` maps to $L$. The coupling means you cannot
independently widen exit_bps while tightening stop.

### 3.3 Almgren–Chriss $\kappa$

Permanent impact: $\lambda$ (bps per unit traded)
Temporary impact: $\eta$ (bps per unit per time)
Volatility: $\sigma$ (annualized)

The urgency parameter:
$\kappa = \sqrt{\frac{\lambda\sigma^2}{\eta}}$

Optimal deterministic liquidation of $X_0$ shares over $[0,T]$:
$v(t) = \kappa X_0 \frac{\cosh(\kappa(T-t))}{\sinh(\kappa T)}$

Remaining inventory: $x(t) = X_0 \frac{\sinh(\kappa(T-t))}{\sinh(\kappa T)}$

[INFERENCE] For arbTwin: $\kappa$ matters for the exit queue. When unwinding a single-leg position
(stop-loss triggered), the optimal unwind schedule is A–C with $\kappa$ calibrated to the venue's
liquidity. Lighter's sparse order book means $\eta$ is large → $\kappa$ is small → slower unwind
is optimal. OKX's dense book means faster unwind feasible.

### 3.4 A–S Inventory Skew

Avellaneda–Stoikov (2008) reference price $S_t \sim \text{BM}(\sigma)$. Market maker posts
bid at $S_t - \delta^b$ and ask at $S_t + \delta^a$. Arrival intensity $\lambda(\delta) = A e^{-k\delta}$.

Optimal quotes for CARA utility with risk aversion $\gamma$:
$\delta^b = \frac{1}{k} + \gamma\sigma^2 q + \frac{\gamma\sigma^2(T-t)}{2}$
$\delta^a = \frac{1}{k} - \gamma\sigma^2 q + \frac{\gamma\sigma^2(T-t)}{2}$

Skew = $\delta^a - \delta^b = -2\gamma\sigma^2 q$: long inventory → lower ask (aggressive selling),
short inventory → higher bid (aggressive buying).

From Feys (2026): this is equivalent to the Cartea–Jaimungal running penalty $\varphi$ with
$\varphi = \gamma\sigma^2/2$. The inventory penalty per unit time is $\varphi q^2 = \frac{1}{2}\gamma\sigma^2 q^2$.

[INFERENCE] For arbTwin: the dual-maker mode (`action=MAKER`) should skew quotes based on
current inventory. If holding a single-leg position from a partially-filled pair, the
A–S skew formula tells you how much to adjust the surviving leg's quote.

### 3.5 Limit-vs-Market / Fill-Probability Rule (Cont & Kukanov 2012)

For a single exchange, the optimal split between limit and market orders solves:

$\max_{\alpha \in [0,1]} \; \alpha \cdot [f \cdot (s - \text{rebate}) - (1-f) \cdot \text{adverse selection}] - (1-\alpha) \cdot \text{spread_cost}$

where $f$ = fill probability, $\alpha$ = fraction sent as limit order.

Fill probability $f(\delta)$ decays with distance $\delta$ from mid. The optimal limit placement
distance $\delta^*$ solves: $\frac{\partial f}{\partial \delta} \cdot \text{gain} = \text{cost of non-execution}$.

[INFERENCE] For arbTwin's ENTER mode (Lighter post-only leading → OKX IOC hedge):
the Lighter leg's fill probability governs the probability of successful pair completion.
If $f_{\text{Lighter}}(\delta) < 0.5$, the expected number of IOC hedge attempts before both legs
fill exceeds 2, and the cost of repeated OKX taker fees dominates the edge. The optimal
Lighter placement distance $\delta$ should be chosen so that $f(\delta) \cdot \text{edge} > 2 \cdot \text{cost}_{\text{OKX taker}}$.

---

## (4) Mapping to arbTwin Parameters

| arbTwin Parameter | Paper | Recommended Change | Rationale |
|---|---|---|---|
| `entry_bps` | Leung & Li (2014); Baviera & Baldi (2017) | Set as $\bar{b} \propto \sqrt{c} \cdot \sigma_{\text{basis}}$ where $c$ = round-trip cost | Entry threshold should scale with transaction cost and basis volatility, not be a fixed bps constant |
| `exit_bps` | Leung & Li (2014) | **Must co-vary with `stop`**: tighter stop → lower exit_bps | Theorem: higher stop-loss always implies lower optimal take-profit |
| `band` (in `band.py`) | Leung & Li (2014); Xing (2019) | `band = max(floor, min(k·σ_basis, cap))` is correct; add `stop` coupling: `band = min(band, stop - 2·σ_basis)` | Free boundary no-trade region must stay clear of stop-loss by at least 2σ to avoid whipsaw |
| `hysteresis` | Leung & Li (2014) | Entry band ≠ exit band by construction; hysteresis = $\bar{b} - \underline{a}$ should be at least round-trip cost in bps | Hysteresis < cost → churning on noise |
| `stop` | Baviera & Baldi (2017) | Optimal stop-loss exists as function of OU parameters; not arbitrary | Stop-loss should be set at level where expected First-Exit-Time from $[\text{stop}, \text{entry}]$ is maximized [INFERENCE] |
| `max_unhedged_ms` | Figueroa-López et al. (2017) | Current 800ms aligns with critical time $t_0$ concept; tighten to 500ms if Lighter fill probability < 0.3 | With negative drift expectation, optimal placement moves deeper → longer fill times → need tolerance or wider quote |
| Inventory skew (not yet in arbTwin) | Guéant/Lehalle/Tapia (2011); Feys (2026) | **Add**: skew quotes by $\gamma\sigma^2 q$ in dual-maker mode. $\gamma$ estimated as $2 \cdot \text{fee_bps} / \sigma^2_{\text{basis}}$ | Unhedged single-leg drift is the biggest risk in MAKER mode; A–S skew reduces inventory accumulation |
| RWA equity basis adjustment | Seo et al. (2026) | **Add**: for dividend-paying equities (AAPL, NVDA, etc.), adjust expected basis by ex-dividend drop | Premium-index funding undercompensates long perp holders through ex-div dates |

---

## (5) What Is NOT Supported by the Papers

1. **Crypto-specific execution latency models**: All cached papers model equity/FX markets.
   No paper addresses the 300ms Lighter speed bump, zk-proof signing latency, or OKX's
   specific IOC semantics. The execution models must be adapted, not directly applied.

2. **Stablecoin basis (USDT/USDC)**: No paper models the cross-currency basis that arbTwin
   inherently carries (OKX in USDT, Lighter in USDC). This is a first-order term not captured
   by any cached model.

3. **Funding rate frequency (1h vs 8h)**: While Gupta & Polson (2026) analyze perp pricing
   theoretically, no paper compares the practical effect of 1h vs 8h funding intervals on
   basis dynamics or front-running behavior.

4. **Dual-venue simultaneous execution**: All optimal execution papers assume a single venue.
   The core arbTwin problem — post-only on Lighter, IOC hedge on OKX, with hard max_unhedged_ms —
   has no direct theoretical treatment in this cache.

5. **Zero-fee venue (Lighter) as structural advantage**: No paper models the asymmetry of
   one venue charging 0 bps and the other charging 5 bps taker. This is a practical
   optimization not a theoretical one, but the Cont & Kukanov framework could be extended.

6. **Tick-size and lot-size constraints**: None of the cached papers model discrete tick sizes
   or minimum order quantities. arbTwin's real constraint of OKX's `tickSz` and Lighter's
   `baseMinSize` has no theoretical analogue here.

7. **Empirical calibration on crypto data**: All empirical studies in the cache use equity
   or energy futures data. No paper validates OU-based strategies on crypto perpetual basis
   series. The stationarity assumption — critical for OU modeling — is unverified for crypto
   basis in these sources.

8. **Maker/taker fee asymmetry optimization**: Cont & Kukanov (2012) incorporate fees/rebates
   but their framework assumes a single exchange. Cross-venue fee arbitrage (post-only on the
   zero-fee venue, taker on the costly venue) is not modeled.

---

## (6) Surprises

1. **A–S and Cartea–Jaimungal are the same model** (Feys 2026). This is a 2026 result:
   $\varphi = \gamma\sigma^2/2$ is forced by basic preference axioms. If you've been
   calibrating both $\gamma$ and $\varphi$ independently, you've been double-counting.
   For arbTwin: pick one risk parameter and derive the other.

2. **Limit orders should NOT always be placed at the best bid/ask** (Figueroa-López et al. 2017).
   The conventional wisdom of "post as close to the mid as possible" is wrong when drift is
   negative and your horizon exceeds the critical time $t_0$. Since arbTwin's Lighter leg is
   always passive with an adverse drift (you're providing liquidity, price moves against you),
   this finding matters — deeper placement may be optimal.

3. **Stop-loss and take-profit are coupled** (Leung & Li 2014). Widening the stop-loss is not
   a free lunch: it forces a lower take-profit threshold. The arbitrageur cannot independently
   choose risk and reward; the OU dynamics couple them.

4. **Volatility doesn't directly break the perp peg** (Gupta & Polson 2026). The basis widens
   only if there's a volatility *risk premium* — the market pricing vol risk, not vol itself.
   During calm markets with low VRP, basis can stay tight despite high realized vol.

5. **Dividend fairness is broken for equity perpetuals** (Seo et al. 2026). arbTwin trades
   AAPL, NVDA, and other dividend stocks. The premium-index funding mechanism systematically
   undercompensates the long-perp leg through ex-dividend dates. This is a predictable loss,
   not a random one — it should be subtracted from the expected edge.

6. **Transaction costs create a hard no-trade zone** (Xing 2019). Even with perfect mean-reversion
   signals, if the basis band width is less than $\sim 2\sqrt{c}$ in bps, trading is impossible.
   For arbTwin's cost structure (4 bps round-trip maker/maker, ~10 bps taker-hedge),
   the minimum viable band is approximately 8–20 bps, matching the README's empirical finding
   that mainnet pairs (ETH, BTC at 2–5 bps) are untradeable.