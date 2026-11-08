"""CLI: `arbtwin <doctor|screen|monitor|run|backtest|calibrate>`.

Every subcommand is safe by default: reads are public, orders need
`ARBTWIN_DRY_RUN=0` *and* credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__
from .backtest.replay import replay, walk_forward
from .calibration import calibrate
from .config import AppConfig, write_state
from .data import both_hourly
from .engine import TwinEngine
from .fees import build_fee_book
from .mechanics import build_mechanics
from .monitor import Monitor
from .screening import build_symbols_yaml, screen_symbols
from .venues.lighter import LighterVenue
from .venues.okx import OkxVenue


def _proxy() -> str | None:
    return os.environ.get("ARBTWIN_PROXY") or os.environ.get("HTTPS_PROXY") or None


def _venues(cfg: AppConfig):
    proxy = _proxy()
    return OkxVenue(cfg.okx, proxy=proxy), LighterVenue(cfg.lighter, proxy=proxy)


def cmd_doctor(args) -> int:
    cfg = AppConfig.load()
    okx, lighter = _venues(cfg)
    print(f"arbTwin {__version__}\n")
    print("config:")
    print(json.dumps(cfg.redacted(), indent=2, ensure_ascii=False))
    print("\nfees (config):")
    print(build_fee_book(cfg.venues).describe())
    print("\nmechanics:")
    print(build_mechanics(cfg.venues).describe() or "  (no venues configured)")

    print("\nvenue reachability:")
    status = {}
    for name, venue in (("okx", okx), ("lighter", lighter)):
        t0 = time.time()
        try:
            specs = venue.specs()
            status[name] = {"ok": True, "markets": len(specs), "ms": round((time.time() - t0) * 1000)}
        except Exception as exc:
            status[name] = {"ok": False, "error": str(exc)[:200]}
    print(json.dumps(status, indent=2))

    symbols = [e.get("symbol") for e in cfg.symbols]
    if symbols:
        print(f"\nuniverse check ({len(symbols)} symbols):")
        okx_specs, lt_specs = okx.specs(), lighter.specs()
        for entry in cfg.symbols:
            sym = str(entry.get("symbol", "")).upper()
            ratio = float(entry.get("unit_ratio") or 0)
            problems = []
            if sym not in okx_specs:
                problems.append("not on OKX")
            if sym not in lt_specs:
                problems.append("not on Lighter")
            if ratio <= 0:
                problems.append("unit_ratio missing — rerun `arbtwin screen`")
            print(f"  {sym:<10s} ratio={ratio:<10.6f} {'OK' if not problems else '; '.join(problems)}")
    else:
        print("\nuniverse: empty — run `arbtwin screen` to generate config/symbols.yaml")
    return 0


def cmd_screen(args) -> int:
    cfg = AppConfig.load()
    cfg.dry_run = True
    okx, lighter = _venues(cfg)
    t0 = time.time()
    rows = screen_symbols(cfg, okx, lighter, days=args.days, min_score=args.min_score,
                          max_symbols=args.max_symbols, verbose=True,
                          only=[s for s in (args.only or "").split(",") if s])
    payload = build_symbols_yaml(rows)
    out = args.out or os.environ.get("ARBTWIN_SYMBOLS", "config/symbols.yaml")
    import yaml

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)
    print(f"\napproved {payload['universe']['approved']} / {payload['universe']['candidates']} "
          f"in {time.time() - t0:.1f}s -> {out}\n")
    for row in sorted(rows, key=lambda r: -r.score):
        if row.approved:
            print(f"  [OK ] {row.symbol:<10s} score={row.score:7.2f} ratio={row.unit_ratio:<10.6f} "
                  f"cv={row.ratio_cv_pct:.3f}% corr={row.return_corr:.4f} sigma={row.sigma_1h_bps:.1f}bps")
    rejects = [r for r in rows if not r.approved]
    if rejects:
        print(f"\n  rejected {len(rejects)}:")
        for row in rejects[:20]:
            print(f"  [--] {row.symbol:<10s} {'; '.join(row.reject_reasons[:2])[:140]}")
    return 0


def cmd_monitor(args) -> int:
    cfg = AppConfig.load()
    okx, lighter = _venues(cfg)
    engine = TwinEngine(cfg, okx, lighter)
    engine.load_universe()
    engine.start_streams()
    mon = Monitor(cfg.state_dir, cfg.alert_webhook)
    limits = cfg.risk.get("limits") or {}
    for _ in range(args.iterations):
        snap = engine.snapshot()
        health = {"okx": okx.ws_health(), "lighter": lighter.ws_health()}
        payload = mon.observe(snap, health, limits)
        print(f"--- {time.strftime('%H:%M:%S')} ---")
        print(mon.render(payload))
        time.sleep(args.interval)
    engine.okx.stop()
    engine.lighter.stop()
    return 0


def cmd_run(args) -> int:
    cfg = AppConfig.load()
    okx, lighter = _venues(cfg)
    if not cfg.dry_run and not args.i_know_what_im_doing:
        print("refusing to run live without --i-know-what-im-doing "
              "(reads ARBTWIN_DRY_RUN=0 and live credentials)", file=sys.stderr)
        return 2
    engine = TwinEngine(cfg, okx, lighter)
    print(f"arbTwin {__version__} | dry_run={cfg.dry_run} | symbols={len(cfg.symbols)}")
    engine.run_forever(iterations=args.iterations, interval_s=args.interval or None)
    return 0


def cmd_backtest(args) -> int:
    cfg = AppConfig.load()
    okx, lighter = _venues(cfg)
    okx_series, lt_series = both_hourly(okx, lighter, args.symbol, args.days)
    if not okx_series or not lt_series:
        print(f"no history for {args.symbol}", file=sys.stderr)
        return 1
    calib = calibrate(args.symbol, okx_series, lt_series)
    if not calib.tradable:
        print(f"{args.symbol}: not tradable — {'; '.join(calib.reasons)}", file=sys.stderr)
        return 1
    from .fees import fee_from_market_specs

    spec_okx = okx.specs([args.symbol]).get(args.symbol) or okx.specs().get(args.symbol)
    spec_lt = lighter.specs([args.symbol]).get(args.symbol)
    fee_bps = fee_from_market_specs(spec_okx, spec_lt).round_trip_bps("lighter", "okx")
    res = replay(args.symbol, okx_series, lt_series, calib.ratio, fee_bps,
                 latency_bps=args.latency_bps, slippage_bps=cfg.limits.max_slippage_bps)
    print(json.dumps(res.stats(), indent=2))
    folds = walk_forward(okx_series, lt_series, calib.ratio, fee_bps)
    if folds:
        print("\nwalk-forward folds:")
        for f in folds:
            print(f"  fold {f['fold']}: trades={f['n_trades']:>3} net={f['total_net_bps']:>9.1f}bps "
                  f"win={f['win_rate']:.0%} dd={f['max_drawdown_bps']:.1f}bps")
    write_state(f"{cfg.state_dir}/backtest_{args.symbol}.json", res.stats())
    return 0


def cmd_calibrate(args) -> int:
    cfg = AppConfig.load()
    okx, lighter = _venues(cfg)
    okx_series, lt_series = both_hourly(okx, lighter, args.symbol, args.days)
    calib = calibrate(args.symbol, okx_series, lt_series)
    print(json.dumps(calib.to_dict(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arbtwin", description="OKX <-> Lighter twin-venue basis engine")
    parser.add_argument("--version", action="version", version=f"arbtwin {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="configuration + venue reachability")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("screen", help="rebuild config/symbols.yaml from live data")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--min-score", type=float, default=0.0)
    p.add_argument("--max-symbols", type=int, default=12)
    p.add_argument("--only", default=None, help="comma separated subset, e.g. --only AAPL,NVDA,ETH")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("monitor", help="print the premium matrix and alerts")
    p.add_argument("--iterations", type=int, default=1)
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("run", help="trade loop (dry-run unless ARBTWIN_DRY_RUN=0)")
    p.add_argument("--iterations", type=int, default=0, help="0 = forever")
    p.add_argument("--interval", type=float, default=0.0)
    p.add_argument("--i-know-what-im-doing", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("backtest", help="causal replay of one symbol")
    p.add_argument("symbol")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--latency-bps", type=float, default=0.5)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("calibrate", help="unit ratio / same-instrument report")
    p.add_argument("symbol")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=cmd_calibrate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())