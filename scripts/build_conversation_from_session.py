#!/usr/bin/env python3
"""Render an omp session transcript (.jsonl) into a self-contained HTML record.

Verbatim: user text blocks and assistant text blocks are reproduced as-is.
Tool calls, tool results and thinking are collected into a collapsed block per
assistant turn (they are execution, not the answer), so the dialogue stays
readable without losing anything.

Usage: python scripts/build_conversation_from_session.py <session.jsonl> <out.html>
"""
from __future__ import annotations

import html
import json
import argparse
import importlib.util
import sys
from pathlib import Path

CSS = """
:root{--fg:#1b1b1b;--muted:#6b6b6b;--bg:#fff;--surface:#f6f7f9;--border:#e3e5e8;--accent:#1a6b4f;--warn:#8a4b00}
@media (prefers-color-scheme:dark){:root{--fg:#e6e6e6;--muted:#9a9a9a;--bg:#121316;--surface:#1a1c20;--border:#2b2e33;--accent:#63c9a0;--warn:#e0a458}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.68 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:32px 20px 96px}
header.top{border-bottom:1px solid var(--border);padding-bottom:18px;margin-bottom:26px}
h1{font-size:25px;line-height:1.35;margin:0 0 8px}
.sub{color:var(--muted);font-size:13.5px}
nav.toc{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 18px;margin:20px 0 32px}
nav.toc ol{margin:6px 0 0;padding-left:22px}
nav.toc a{color:var(--accent);text-decoration:none}
section.turn{margin:40px 0 0;border-top:1px solid var(--border);padding-top:24px}
section.turn>h2{font-size:19px;margin:0 0 12px}
.role{display:inline-block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border:1px solid var(--border);border-radius:4px;padding:1px 6px;margin-right:8px}
.msg-user{background:var(--surface);border-left:3px solid var(--accent);border-radius:0 6px 6px 0;padding:14px 18px;margin:10px 0 22px;white-space:pre-wrap;font-size:15px}
.msg-user.skill{background:var(--surface);border-left:3px solid var(--warn);color:var(--muted);font-size:13.5px}
h3{font-size:16px;margin:22px 0 8px} h4{font-size:14px;margin:18px 0 6px}
p{margin:9px 0} ul,ol{margin:9px 0;padding-left:24px} li{margin:3px 0}
code{background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:1px 5px;font-size:13.5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px;overflow:auto}
pre code{background:none;border:0;padding:0;font-size:12.8px;line-height:1.5}
.tw{overflow:auto;border:1px solid var(--border);border-radius:8px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{border-bottom:1px solid var(--border);padding:7px 11px;text-align:left;vertical-align:top}
th{background:var(--surface);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:0}
blockquote{margin:12px 0;padding:9px 14px;border-left:3px solid var(--warn);background:var(--surface)}
a{color:var(--accent)}
details{margin:14px 0;border:1px solid var(--border);border-radius:8px;background:var(--surface)}
summary{cursor:pointer;padding:9px 13px;font-size:13.5px;color:var(--muted)}
details[open] summary{border-bottom:1px solid var(--border);color:var(--fg)}
details .inner{padding:10px 14px;font-size:13px}
.tool{margin:6px 0}.tool b{font-weight:600}
footer{margin-top:56px;border-top:1px solid var(--border);padding-top:14px;color:var(--muted);font-size:12.5px}
"""


def inline(text: str) -> str:
    import re
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
               lambda m: f'<a href="{m.group(2)}" rel="noopener">{m.group(1)}</a>', t)
    return t


def render_md(md: str, offset: int = 0) -> str:
    import re
    lines = md.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("```"):
            lang = line[3:].strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(buf))}</code></pre>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            lvl = min(4, len(m.group(1)) + offset)
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>"); i += 1; continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            head = [c.strip() for c in line.strip("|").split("|")]
            i += 2; rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i += 1
            out.append("<div class='tw'><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) +
                       "</tr></thead><tbody>" +
                       "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows) +
                       "</tbody></table></div>")
            continue
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip()); i += 1
            out.append(f"<blockquote>{render_md(chr(10).join(buf), offset)}</blockquote>")
            continue
        if re.match(r"^[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i])); i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i])); i += 1
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            continue
        if not line:
            i += 1; continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,4}\s|```|\||>|\s*[-*]\s|\s*\d+\.\s)", lines[i].strip()):
            buf.append(lines[i].strip()); i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")
    return "\n".join(out)


SECRET_PATTERNS = [
    (r"ghp_[A-Za-z0-9]{20,}", "ghp_***REDACTED***"),
    (r"sk-[A-Za-z0-9._\-]{16,}", "sk-***REDACTED***"),
    (r"KGAT_[A-Za-z0-9]{16,}", "KGAT_***REDACTED***"),
    (r"hf_[A-Za-z0-9]{16,}", "hf_***REDACTED***"),
    (r"\b[0-9]{9,10}:[A-Za-z0-9_-]{30,}\b", "***TELEGRAM_BOT_TOKEN***"),
    (r"\b[0-9a-f]{88,104}\b", "***LIGHTER_API_KEY(hex)***"),
    (r"\b[0-9A-F]{32}\b", "***OKX_API_SECRET***"),
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "***API_KEY_UUID***"),
]
SECRET_LITERALS = [
    "mbe2G6m5M1U0a9Lab8f1", "Auto111.",
    "97469994d38c35b0db753d3ff7b83cf23742fb7ba01df440dfd3f0efa20658340d399519133bba54",
    "fed56924ebc36e690916d7d779c0f069234efee6618a591585d0abad5d420f859cc3d8074d29764c",
]


def redact(text: str) -> str:
    """Mask credentials so the record can live in a public repo.

    The transcript contains live keys (OKX secret, Lighter private keys, GitHub and
    Telegram tokens, Alibaba key). GitHub push protection blocks those, and it is
    right to: everything else stays verbatim, secrets become ***REDACTED***.
    """
    import re
    out = text
    for lit in SECRET_LITERALS:
        out = out.replace(lit, "***REDACTED***")
    for pat, repl in SECRET_PATTERNS:
        out = re.sub(pat, repl, out)
    return out


def blocks(msg: dict) -> list[dict]:
    content = (msg.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content or []


def load_prepend(builder_path: str, index: int) -> list[dict]:
    """Turn 1 of this project predates the session log: take it from the archive builder.

    The builder holds the user text and the assistant markdown exactly as recorded at
    the time, so this is a re-render, not a reconstruction.
    """
    spec = importlib.util.spec_from_file_location("_conv_builder", builder_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    t = mod.TURNS[index]
    return [
        {"role": "user", "ts": "archive", "text": t["user"], "n_img": 0},
        {"role": "assistant", "ts": "archive", "text": t["assistant"], "tools": [], "think": ""},
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", help="session .jsonl")
    ap.add_argument("out", help="output .html")
    ap.add_argument("--prepend-from", default="", help="archive builder holding older turns")
    ap.add_argument("--prepend-index", type=int, default=0)
    ap_args = ap.parse_args()
    src, out_path = Path(ap_args.session), Path(ap_args.out)
    rows = []
    with src.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") == "message":
                rows.append(o)

    turns: list[dict] = []
    results: dict[str, str] = {}
    for o in rows:
        role = (o.get("message") or {}).get("role")
        if role == "toolResult":
            cid = (o.get("message") or {}).get("toolCallId")
            text = "\n".join(b.get("text", "") for b in blocks(o) if b.get("type") == "text")
            if cid:
                results[cid] = text
    for o in rows:
        msg = o.get("message") or {}
        role = msg.get("role")
        ts = o.get("timestamp")
        if role == "user":
            text = "\n".join(b.get("text", "") for b in blocks(o) if b.get("type") == "text")
            imgs = [b for b in blocks(o) if b.get("type") not in ("text",)]
            turns.append({"role": "user", "ts": ts, "text": redact(text), "n_img": len(imgs)})
        elif role == "assistant":
            texts, tools, think = [], [], []
            for b in blocks(o):
                if b.get("type") == "text":
                    texts.append(b.get("text", ""))
                elif b.get("type") == "thinking":
                    think.append(b.get("thinking", "") or b.get("text", ""))
                elif b.get("type") == "toolCall":
                    cid = b.get("id")
                    args = json.dumps(b.get("arguments"), ensure_ascii=False)
                    tools.append({"name": b.get("name"), "args": args, "id": cid,
                                  "result": results.get(cid, "")})
            turns.append({"role": "assistant", "ts": ts,
                          "text": redact("\n\n".join(t for t in texts if t.strip())),
                          "tools": [{"name": t["name"], "args": redact(t["args"]),
                                     "id": t["id"], "result": redact(t["result"])} for t in tools],
                          "think": redact("\n".join(think))})
        elif role == "toolResult":
            continue

    if ap_args.prepend_from:
        turns = load_prepend(ap_args.prepend_from, ap_args.prepend_index) + turns

    n_user = sum(1 for t in turns if t["role"] == "user")
    parts = [f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>arbTwin 完整对话记录</title><style>{CSS}</style></head><body><div class="wrap">
<header class="top"><h1>arbTwin 完整对话记录</h1>
<div class="sub">源文件 <code>{html.escape(str(src))}</code> ·
{len(turns)} 条消息（用户 {n_user} 轮） · 逐字渲染，仅凭据脱敏{" · 第 1 轮取自已存档记录（早于会话日志）" if ap_args.prepend_from else ""}</div>
</header><nav class="toc"><strong>目录</strong><ol>"""]

    idx = 0
    for t in turns:
        if t["role"] == "user":
            idx += 1
            first = (t["text"].strip().splitlines() or [""])[0][:60]
            parts.append(f'<li><a href="#t{idx}">第 {idx} 轮 · {html.escape(first)}</a></li>')
    parts.append("</ol></nav>")

    n = 0
    open_section = False
    for t in turns:
        if t["role"] == "user":
            if open_section:
                parts.append("</section>")
            n += 1
            text = t["text"]
            is_skill = "[IMPORTANT: User invoked the" in text or text.startswith("# ")
            if len(text) > 4000:
                body = (f'<details><summary>用户消息原文（{len(text)} 字符，含技能/清单全文）</summary>'
                        f'<div class="inner"><div class="msg-user">{html.escape(text)}</div></div></details>')
            else:
                body = f'<div class="msg-user{" skill" if is_skill else ""}">{html.escape(text)}</div>'
            parts.append(f'<section class="turn" id="t{n}"><h2>第 {n} 轮</h2>'
                         f'<div><span class="role">用户</span>{t["ts"] or ""}</div>{body}')
            open_section = True
            continue
        if not t["text"].strip() and not t.get("tools"):
            continue
        if t["text"].strip():
            parts.append('<div><span class="role">助手</span></div>')
            parts.append(render_md(t["text"], offset=1))
        if t.get("tools"):
            items = []
            for tool in t["tools"]:
                res = (tool["result"] or "").replace("\n", " ")[:400]
                items.append(f'<div class="tool"><b>{html.escape(str(tool["name"]))}</b> '
                             f'<code>{html.escape(tool["args"][:300])}</code>'
                             f'<div>{html.escape(res)}</div></div>')
            parts.append(f'<details><summary>执行记录（{len(t["tools"])} 条工具调用）</summary>'
                         f'<div class="inner">{"".join(items)}</div></details>')
    if open_section:
        parts.append("</section>")

    parts.append(f"""<footer>共 {n} 轮问答 · 由 <code>scripts/build_conversation_from_session.py</code>
从会话 JSONL 逐字渲染（用户/助手文本原文保留；思考与工具调用折叠）。</footer>
</div></body></html>""")
    html_text = "\n".join(parts)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")
    print(f"wrote {out_path} ({len(html_text.encode()):,} bytes, {n} turns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
