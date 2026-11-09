# Microstructure / execution evidence

## 2026-11-09 — touch quoting is adverse-selected (Daru, SSRN 7461901)
Across 125.8M fills on Bybit/Binance/HL, back-of-queue touch quoting had net 10 s markout
−0.39 to −0.84 bp before fees; quoting the 5th level lost less per fill (−0.33 bp) but filled
only 12% as often. Queue position on HL BTC is worth ~0.8 bp/fill.
Source: https://daru.finance/research/crypto-adverse-selection (via research/x-medium).

## 2026-11-09 — two-leg sync is structurally rare (OneChronos 2026)
Across 8 liquid pairs, the share of notional filled within 1 ms on both legs never exceeded
10%; the lagging leg's slippage vs the leader-time NBBO was 1.5–14 bp.
Decision rule: cross when `(lag slippage + |spread drift|) / half-spread > 1`.
Source: https://www.onechronos.com/insights/pairs-part-one/ (via W:/twinCheck/research2).

## 2026-11-09 — stale hedge-venue feed is the expensive failure
CBB's published post-mortem: a stale IBKR feed made the bot "correct" a phantom delta into
~$120M of naked gold shorts, −$1.1M. Mitigation is data freshness vetoes, not better signals.
Source: x.com/Cbb0fe/article/2095250548744405297 (via research/x-medium).

## 2026-11-09 — maker fill probability, not impact, is the binding constraint
Maglaras–Moallemi–Wang (NASDAQ ITCH): touch orders fill 45%/76%/84% within 1/5/10 min;
>80% of real touch quotes are cancelled within 1 min. At a 4-tick spread, the "rest vs cross"
threshold is P(fill) ≳ 0.3–0.5. Slicing is an abort option, not an impact reducer.
Source: https://moallemi.com/ciamac/papers/deep-lob-2021.pdf (via W:/twinCheck/research2).