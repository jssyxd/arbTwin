# Environment

## 2026-11-09 — python3 is absent on this Windows box
`python3` exits 9009. Use `python` (3.12.9 at
`C:\Users\da\AppData\Local\Programs\Python\Python312\python.exe`).

## 2026-11-09 — git-bash `where` with `2>/dev/null` errors
`where foo 2>/dev/null` prints `bash: line 1: /dev/null: 系统找不到指定的路径`.
Use `command -v foo` in loops.

## 2026-11-09 — omp model ids present locally
`pi --list-models` (same catalog omp uses) lists provider `qwen-token-plan-cn`:
`glm-5`, `glm-5.1`, `glm-5.2`, `glm-5.3`, `qwen3.6-flash/plus`, `qwen3.7-max/plus`,
`qwen3.8-flash/max`, `deepseek-v3.2`, `deepseek-v4-flash`, `deepseek-v4-pro`,
`deepseek-v4.1-flash`. Pass via `omp --model=<id>`.

## 2026-11-09 — GitHub API auth header
`Authorization: Bearer <PAT>` returned `401 Bad credentials` for this classic PAT;
`Authorization: token <PAT>` returned 200 on `GET /user`. Token lives in
`.secrets/github_token` (gitignored). Use `token` scheme.

## 2026-11-09 — herd-dispatch spawns on Windows
Run `python <skill>/scripts/herd-dispatch ...`. `--args` is shlex-split; omp needs
`--args "--model=glm-5.3 --approval-mode=yolo"` (omp has no MODEL_FLAG mapping).

## 2026-11-09 — long curl output can break `curl | python -c json.load`
Truncated/mixed stream produced `JSONDecodeError` at line 10612. Save to a file first,
then parse.

## 2026-11-09 — herd-dispatch `watch` fails on the SMB-backed workspace
`\\fnos2\iflow\dynamicTWIN` (W:): `watch`/`assign` can die with
`PermissionError: [WinError 5]` on `os.replace(...\.index.json)`. A plain `os.replace`
in the same directory succeeds; concurrency (parallel assigns, overlapping watch loops)
triggers it. Kill duplicate watchers; supervise with `herdr agent list` when it persists.

## 2026-11-09 — NautilusTrader already installed locally
`python -c "import nautilus_trader"` → 1.219.0. Adapters present: okx, bybit, binance,
dydx, polymarket, interactive_brokers, sandbox, … — **no Lighter adapter**.
`OmsType.HEDGING`, `OrderEmulator`, `SyntheticInstrument`, `ContingencyType.OCO/OTO/OUO`
exist. `adapters/okx/execution.py` mentions post_only, reduce_only, IOC, FOK, GTC.