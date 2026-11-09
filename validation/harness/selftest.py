"""Self-test suite for the validation harness.

Generates synthetic two-venue book/trade data (no network required),
runs the replay + metrics, and produces a report.

Usage:
    python -m validation.harness.selftest
"""

from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .fill_model import conservative_fill, get_fill_model, queue_reactive_fill
from .manifest import (
    ALL_SCHEMAS,
    DataManifest,
    FILL_COLUMNS,
    QUOTE_COLUMNS,
    TRADE_COLUMNS,
    manifest_from_dict,
)
from .loaders import load_quotes, load_trades
from .metrics import (
    MetricResult,
    compute_abort_cost,
    compute_capture_bps,
    compute_escalation_rate,
    compute_markout,
    compute_p_fail,
    compute_p_fill,
    compute_p_fill_by_level,
    compute_time_to_pair,
)
from .replay import Event, EventKind, ReplayConfig, VenueBook, run_replay, run_sliced_replay

# ── Synthetic data generator ───────────────────────────────────────────────


def generate_synthetic_data(
    n_events: int = 2000,
    seed: int = 42,
    ou_half_life_s: float = 300.0,  # 5 min mean reversion
    sigma_bps: float = 5.0,          # basis volatility per sqrt(s)
    spread_bps: float = 4.0,
    mid_price: float = 100.0,
    tick_bps: float = 1.0,
    data_dir: Optional[Path] = None,
) -> tuple[Path, Path, Path, Path]:
    """Generate synthetic OKX + Lighter book/trade data as Parquet files.

    Returns (okx_quote_path, lt_quote_path, okx_trade_path, lt_trade_path).
    """
    rng = np.random.RandomState(seed)
    data_dir = data_dir or Path(__file__).resolve().parent.parent / "data" / "synthetic"
    data_dir.mkdir(parents=True, exist_ok=True)

    dt_s = 0.1  # 100 ms steps
    p = mid_price

    # Pre-generate mid-price paths (independent OU processes)
    okx_mid = np.zeros(n_events)
    lt_mid = np.zeros(n_events)
    okx_mid[0] = mid_price
    lt_mid[0] = mid_price * 1.001  # Lighter starts slightly rich (~10 bps premium)
    theta = math.log(2) / max(ou_half_life_s, 1.0)  # mean-reversion speed
    sigma_per_step = sigma_bps / 1e4 * mid_price * math.sqrt(dt_s)

    for i in range(1, n_events):
        drift_okx = -theta * (okx_mid[i - 1] - mid_price) * dt_s
        drift_lt = -theta * (lt_mid[i - 1] - mid_price) * dt_s

        # Correlated noise: both venues share a common factor
        common = rng.randn() * sigma_per_step
        okx_mid[i] = okx_mid[i - 1] + drift_okx + common + rng.randn() * sigma_per_step * 0.3
        lt_mid[i] = lt_mid[i - 1] + drift_lt + common + rng.randn() * sigma_per_step * 0.3
        okx_mid[i] = max(okx_mid[i], mid_price * 0.5)
        lt_mid[i] = max(lt_mid[i], mid_price * 0.5)

    # Generate quotes and trades
    quote_rows = []
    trade_rows = []
    base_ts = int(time.time() * 1000) - n_events * 100

    for i in range(n_events):
        ts = base_ts + i * 100  # 100ms steps in ms

        for venue, mid_arr in [("okx", okx_mid), ("lighter", lt_mid)]:
            m = mid_arr[i]
            half_spread = spread_bps / 1e4 * m / 2.0
            bid = m - half_spread
            ask = m + half_spread

            quote_rows.append(
                {
                    "venue": venue,
                    "symbol": "SYNTH",
                    "recv_ts": ts,
                    "venue_ts": ts,
                    "bid_px": round(bid, 4),
                    "bid_sz": round(rng.uniform(10, 100), 2),
                    "ask_px": round(ask, 4),
                    "ask_sz": round(rng.uniform(10, 100), 2),
                }
            )

            # Generate trades with ~10% probability
            if rng.rand() < 0.10:
                side = "buy" if rng.rand() < 0.5 else "sell"
                px = bid if side == "sell" else ask
                trade_rows.append(
                    {
                        "venue": venue,
                        "symbol": "SYNTH",
                        "recv_ts": ts + rng.randint(1, 50),
                        "venue_ts": ts + rng.randint(1, 50),
                        "trade_id": f"synth-{i:06d}",
                        "px": round(px + rng.uniform(-0.01, 0.01), 4),
                        "sz": round(rng.uniform(0.1, 5.0), 2),
                        "side": side,
                    }
                )

    quotes_df = pd.DataFrame(quote_rows)
    trades_df = pd.DataFrame(trade_rows)

    # Split by venue
    okx_quotes = quotes_df[quotes_df["venue"] == "okx"].reset_index(drop=True)
    lt_quotes = quotes_df[quotes_df["venue"] == "lighter"].reset_index(drop=True)
    okx_trades = trades_df[trades_df["venue"] == "okx"].reset_index(drop=True)
    lt_trades = trades_df[trades_df["venue"] == "lighter"].reset_index(drop=True)

    okx_q_path = data_dir / "okx_quotes.parquet"
    lt_q_path = data_dir / "lighter_quotes.parquet"
    okx_t_path = data_dir / "okx_trades.parquet"
    lt_t_path = data_dir / "lighter_trades.parquet"

    okx_quotes.to_parquet(okx_q_path, index=False)
    lt_quotes.to_parquet(lt_q_path, index=False)
    okx_trades.to_parquet(okx_t_path, index=False)
    lt_trades.to_parquet(lt_t_path, index=False)

    return okx_q_path, lt_q_path, okx_t_path, lt_t_path


# ── Fill model tests ────────────────────────────────────────────────────────


def _test_fill_models() -> list[tuple[str, bool, str]]:
    """Test conservative and queue-reactive fill models."""
    results: list[tuple[str, bool, str]] = []

    # Test 1: price-through fill (buy limit at 100, trade at 99)
    resting = {"side": "buy", "px": 100.0, "remaining": 10.0}
    trade = {"px": 99.0, "sz": 5.0, "side": "sell", "ts": 1000}
    qty = conservative_fill(trade, resting)
    results.append(("conservative: buy limit filled on trade below", qty == 10.0, f"got {qty}"))

    # Test 2: no fill when trade above buy limit
    trade2 = {"px": 101.0, "sz": 5.0, "side": "sell", "ts": 1000}
    qty2 = conservative_fill(trade2, resting)
    results.append(("conservative: buy limit NOT filled on trade above", qty2 == 0.0, f"got {qty2}"))

    # Test 3: sell limit fill
    resting3 = {"side": "sell", "px": 100.0, "remaining": 10.0}
    trade3 = {"px": 101.0, "sz": 5.0, "side": "buy", "ts": 1000}
    qty3 = conservative_fill(trade3, resting3)
    results.append(("conservative: sell limit filled on trade above", qty3 == 10.0, f"got {qty3}"))

    # Test 4: queue-reactive same-level fill with no queue ahead
    resting4 = {"side": "buy", "px": 100.0, "remaining": 10.0}
    trade4 = {"px": 100.0, "sz": 3.0, "side": "sell", "ts": 1000}
    qty4 = queue_reactive_fill(trade4, resting4, queue_ahead=0.0)
    results.append(("queue-reactive: same-level fill, no queue", qty4 == 3.0, f"got {qty4}"))

    # Test 5: queue-reactive same-level fill with queue ahead → partial
    qty5 = queue_reactive_fill(trade4, resting4, queue_ahead=10.0)
    results.append(("queue-reactive: same-level fill, queue ahead", 0.0 < qty5 < 10.0, f"got {qty5:.4f}"))

    # Test 6: book update crossing
    book = {"bids": [(101.0, 5.0)], "asks": [(102.0, 5.0)]}
    qty6 = conservative_fill(book, resting3)  # sell limit at 100, best bid is 101 → crossed
    results.append(("conservative: book-update crossing sell limit", qty6 == 10.0, f"got {qty6}"))

    return results


# ── Replay test ────────────────────────────────────────────────────────────


def _test_replay() -> tuple[bool, str, dict]:
    """Run a synthetic replay and return (passed, reason, stats)."""
    okx_q, lt_q, okx_t, lt_t = generate_synthetic_data(n_events=2000, seed=123)

    # Load the data
    q_okx = load_quotes(okx_q)
    q_lt = load_quotes(lt_q)
    t_okx = load_trades(okx_t)
    t_lt = load_trades(lt_t)

    # Build event list
    events: list[Event] = []

    for _, row in q_okx.iterrows():
        events.append(
            Event(
                ts=int(row["recv_ts"]),
                kind=EventKind.BOOK,
                venue="okx",
                symbol="SYNTH",
                payload={"bids": [(row["bid_px"], row["bid_sz"])],
                         "asks": [(row["ask_px"], row["ask_sz"])]},
            )
        )

    for _, row in q_lt.iterrows():
        events.append(
            Event(
                ts=int(row["recv_ts"]),
                kind=EventKind.BOOK,
                venue="lighter",
                symbol="SYNTH",
                payload={"bids": [(row["bid_px"], row["bid_sz"])],
                         "asks": [(row["ask_px"], row["ask_sz"])]},
            )
        )

    for _, row in t_okx.iterrows():
        events.append(
            Event(
                ts=int(row["recv_ts"]),
                kind=EventKind.TRADE,
                venue="okx",
                symbol="SYNTH",
                payload={"px": row["px"], "sz": row["sz"], "side": row["side"]},
            )
        )

    for _, row in t_lt.iterrows():
        events.append(
            Event(
                ts=int(row["recv_ts"]),
                kind=EventKind.TRADE,
                venue="lighter",
                symbol="SYNTH",
                payload={"px": row["px"], "sz": row["sz"], "side": row["side"]},
            )
        )

    events.sort(key=lambda e: e.ts)

    # Single-slice replay
    config = ReplayConfig(symbol="SYNTH", unit_ratio=1.0, n_slices=1)
    result = run_replay(events, config)

    # Sliced A/B replay
    config_a = ReplayConfig(symbol="SYNTH", unit_ratio=1.0, entry_bps=12.0, exit_bps=4.0)
    config_b = ReplayConfig(symbol="SYNTH", unit_ratio=1.0, entry_bps=16.0, exit_bps=6.0)
    sliced = run_sliced_replay(events, config_a, config_b)

    stats = {
        "n_events": result.n_events,
        "n_book": result.n_book,
        "n_trade": result.n_trade,
        "elapsed_fake_s": result.elapsed_fake_s,
        "n_signals": len(result.signals),
        "a_signals": sliced.comparison.get("a_signals", 0),
        "b_signals": sliced.comparison.get("b_signals", 0),
    }

    ok = result.n_events > 0 and result.elapsed_real_s < 5.0
    return ok, f"{result.n_events} events, {len(result.signals)} signals", stats


# ── Metrics test ────────────────────────────────────────────────────────────


def _test_metrics() -> tuple[bool, str, dict]:
    """Run synthetic metrics computation."""
    okx_q, lt_q, okx_t, lt_t = generate_synthetic_data(n_events=2000, seed=456)

    quotes_okx = load_quotes(okx_q)
    quotes_lt = load_quotes(lt_q)
    trades_okx = load_trades(okx_t)
    trades_lt = load_trades(lt_t)

    # Combine for cross-venue metrics
    all_quotes = pd.concat([quotes_okx, quotes_lt], ignore_index=True)
    # Add venue to fills
    trades_okx["venue"] = "okx"
    trades_lt["venue"] = "lighter"
    all_fills = pd.concat([trades_okx, trades_lt], ignore_index=True)

    results = {}
    errors = []

    for fn, name in [
        (lambda: compute_p_fill(all_fills, all_quotes, 0.0, 60.0), "p_fill_0bps_60s"),
        (lambda: compute_p_fill(all_fills, all_quotes, 5.0, 60.0), "p_fill_5bps_60s"),
        (lambda: compute_time_to_pair(all_fills, "SYNTH"), "time_to_pair"),
        (lambda: compute_p_fail(all_fills, 5400.0, "SYNTH"), "p_fail"),
        (lambda: compute_markout(all_fills, all_quotes), "markout"),
        (lambda: compute_capture_bps(all_fills, all_quotes, 4.0), "capture_bps"),
        (lambda: compute_abort_cost(all_fills, all_quotes), "abort_cost"),
    ]:
        try:
            r = fn()
            results[name] = r.to_dict()
        except Exception as e:
            errors.append(f"{name}: {e}")

    ok = len(errors) == 0
    return ok, f"{len(results)} metrics computed, {len(errors)} errors", results


# ── Main selftest runner ───────────────────────────────────────────────────


def run_selftest() -> dict:
    """Run all self-tests and return a result dict."""
    t_start = time.monotonic()

    sections: list[dict] = []

    # 1. Fill models
    fill_results = _test_fill_models()
    fill_passed = all(p for _, p, _ in fill_results)
    sections.append(
        {
            "name": "Fill models",
            "passed": fill_passed,
            "tests": [{"name": n, "passed": p, "detail": d} for n, p, d in fill_results],
        }
    )

    # 2. Manifest schemas
    manifest_tests = []
    for schema_name, schema in ALL_SCHEMAS.items():
        manifest_tests.append(
            {"name": f"schema_{schema_name}", "passed": len(schema.names) > 0,
             "detail": f"{len(schema.names)} columns"}
        )
    dm = DataManifest(symbol="SYNTH", quotes_okx="okx_quotes.parquet", quotes_lighter="lighter_quotes.parquet")
    manifest_tests.append({"name": "manifest_mode", "passed": dm.mode() == "quote",
                            "detail": dm.mode()})
    sections.append(
        {"name": "Schemas & Manifest", "passed": all(t["passed"] for t in manifest_tests),
         "tests": manifest_tests}
    )

    # 3. Replay
    replay_ok, replay_msg, replay_stats = _test_replay()
    sections.append(
        {"name": "Replay engine", "passed": replay_ok,
         "tests": [{"name": "replay_run", "passed": replay_ok, "detail": replay_msg}],
         "stats": replay_stats}
    )

    # 4. Metrics
    met_ok, met_msg, met_results = _test_metrics()
    met_tests = [{"name": k, "passed": v.get("value", -999) != -1, "detail": f"value={v.get('value')}"}
                 for k, v in met_results.items()]
    sections.append(
        {"name": "Metrics", "passed": met_ok,
         "tests": met_tests, "stats": met_results}
    )

    elapsed = time.monotonic() - t_start
    all_passed = all(s["passed"] for s in sections)

    return {
        "title": "validation/harness selftest",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "passed": all_passed,
        "elapsed_s": round(elapsed, 3),
        "sections": sections,
    }


def main() -> int:
    """Entry point for `python -m validation.harness.selftest`.

    Prints a report to stdout and writes a summary to
    `validation/reports/harness-selftest.md`.
    """
    print("=" * 72)
    print("  validation/harness selftest")
    print("=" * 72)
    print()

    result = run_selftest()

    for sec in result["sections"]:
        status = "✓" if sec["passed"] else "✗"
        print(f"  [{status}] {sec['name']}")
        for t in sec.get("tests", []):
            ts = "✓" if t["passed"] else "✗"
            print(f"       [{ts}] {t['name']}: {t.get('detail', '')}")
        if "stats" in sec:
            for k, v in sec["stats"].items():
                if isinstance(v, dict):
                    continue
                print(f"       stats.{k} = {v}")
        print()

    elapsed = result["elapsed_s"]
    all_passed = result["passed"]
    print(f"  {'✓ ALL PASSED' if all_passed else '✗ SOME FAILED'} ({elapsed:.3f}s)")
    print()

    # Write report
    report_dir = Path(__file__).resolve().parent.parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "harness-selftest.md"

    lines = [
        "# validation/harness selftest report",
        "",
        f"**Timestamp**: {result['timestamp']}",
        f"**Result**: {'✓ ALL PASSED' if all_passed else '✗ SOME FAILED'} ({elapsed:.3f}s)",
        "",
    ]

    for sec in result["sections"]:
        status = "✓" if sec["passed"] else "✗"
        lines.append(f"## {status} {sec['name']}")
        lines.append("")
        for t in sec.get("tests", []):
            ts = "✓" if t["passed"] else "✗"
            lines.append(f"- [{ts}] **{t['name']}**: {t.get('detail', '')}")
        lines.append("")

        if "stats" in sec:
            lines.append("### Metrics")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            for k, v in sec["stats"].items():
                if isinstance(v, dict):
                    continue
                lines.append(f"| {k} | {v} |")
            lines.append("")

    lines.append("## Commands")
    lines.append("")
    lines.append("```bash")
    lines.append("# Run selftest")
    lines.append("python -m validation.harness.selftest")
    lines.append("```")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Report written to {report_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())