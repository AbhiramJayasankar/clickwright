"""Self-contained HTML report for one run (report.html next to the images)."""
import html
import json
from pathlib import Path

from .runlog import read_run

CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#6b6a65;--line:#e3e1db;--ok:#1f7a4d;--bad:#b3261e;
--mark:#ff3b30;--mark2:#0a84ff;--code:#f1efe9}
@media (prefers-color-scheme:dark){:root{--bg:#151514;--card:#1f1f1d;--ink:#ecebe6;--mute:#9b9a94;
--line:#33322f;--ok:#5cc48d;--bad:#ff7b72;--code:#2a2a27}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 "Segoe UI",system-ui,sans-serif}
header{padding:20px 24px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:5}
h1{font-size:17px;margin:0 0 6px}.meta{color:var(--mute);display:flex;flex-wrap:wrap;gap:6px 18px}
.meta b{color:var(--ink);font-weight:600}.status{font-weight:700}.status.done{color:var(--ok)}
.status.failed,.status.error,.status.stuck,.status.stopped{color:var(--bad)}
main{max-width:1500px;margin:0 auto;padding:16px 24px 60px}
.step{display:grid;grid-template-columns:minmax(0,3fr) minmax(280px,2fr);gap:16px;background:var(--card);
border:1px solid var(--line);border-radius:10px;padding:14px;margin:14px 0}
@media (max-width:900px){.step{grid-template-columns:1fr}header{position:static;padding:16px}main{padding:8px 16px 40px}}
.shot{position:relative;line-height:0;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.shot img{width:100%;height:auto}
.mk{position:absolute;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;border:3px solid var(--mark);
box-shadow:0 0 0 2px #fff8}.mk span{position:absolute;left:16px;top:-6px;font:700 11px/1 sans-serif;color:#fff;
background:var(--mark);padding:2px 4px;border-radius:3px;white-space:nowrap}
.mk.end{border-color:var(--mark2)}.mk.end span{background:var(--mark2)}
.zoomrect{position:absolute;border:2px dashed var(--mark2)}
h2{font-size:14px;margin:0 0 8px;display:flex;gap:10px;align-items:baseline}h2 small{color:var(--mute);font-weight:400}
.act{font-family:Consolas,monospace;font-size:12.5px;background:var(--code);border-radius:6px;padding:6px 8px;margin:4px 0}
.act.bad{border-left:3px solid var(--bad)}.act.ok{border-left:3px solid var(--ok)}
.txt{white-space:pre-wrap;word-break:break-word}details{margin:6px 0}summary{cursor:pointer;color:var(--mute)}
pre{white-space:pre-wrap;word-break:break-word;background:var(--code);padding:8px;border-radius:6px;font-size:12px;
max-height:360px;overflow:auto}.zoom img{max-width:100%;border:1px solid var(--line);border-radius:6px;margin-top:6px}
"""


def _pct(v, total):
    return f"{max(0.0, min(100.0, v / max(1, total) * 100)):.3f}%"


def write_report(run_dir):
    run_dir = Path(run_dir)
    meta, steps, summary = read_run(run_dir)
    summary = summary or {}
    tot = summary.get("totals", {})
    status = summary.get("status", "running")
    e = html.escape
    out = [f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Run {e(meta.get('profile', ''))}</title><style>{CSS}</style><header>"
           f"<h1>{e(meta.get('task', ''))}</h1><div class=meta>"
           f"<span class='status {e(status)}'>{e(status)}</span>"
           f"<span>model <b>{e(str(meta.get('profile_settings', {}).get('model', '')))}</b></span>"
           f"<span>coords <b>{e(str(meta.get('convention')))}</b> ({e(str(meta.get('convention_source')))})</span>"
           f"<span>steps <b>{summary.get('steps', len(steps))}</b></span>"
           f"<span>time <b>{summary.get('duration_s', '?')}s</b></span>"
           f"<span>tokens <b>{tot.get('prompt_tokens', 0)}</b> in / <b>{tot.get('completion_tokens', 0)}</b> out"
           f" ({tot.get('cached_tokens', 0)} cached)</span>"
           + (f"<span>cost <b>${tot.get('cost_usd', 0):.4f}</b></span>" if tot.get("cost_usd") else "")
           + f"<span>started {e(meta.get('started', ''))}</span></div>"]
    if summary.get("answer") or summary.get("reason"):
        out.append(f"<div class=txt style='margin-top:8px'>{e(summary.get('answer') or '')}"
                   f"{' — ' + e(summary['reason']) if summary.get('reason') else ''}</div>")
    out.append("</header><main>")
    for s in steps:
        if "screenshot" not in s:
            out.append(f"<section class=step><div><h2>Step {s.get('step')}</h2>"
                       f"<div class='act bad'>{e(str(s.get('error')))}</div></div></section>")
            continue
        fr = s.get("frame") or {}
        W, H = fr.get("image_w", 1), fr.get("image_h", 1)
        marks = []
        n = 0
        for a in s.get("actions") or []:
            xy = a.get("image_xy")
            if not xy:
                continue
            n += 1
            label = e(a["name"])
            if a["name"] == "zoom":
                x1, y1, x2, y2 = xy
                marks.append(f"<div class=zoomrect style='left:{_pct(min(x1, x2), W)};top:{_pct(min(y1, y2), H)};"
                             f"width:{_pct(abs(x2 - x1), W)};height:{_pct(abs(y2 - y1), H)}'></div>")
            elif len(xy) == 4:
                marks.append(f"<div class=mk style='left:{_pct(xy[0], W)};top:{_pct(xy[1], H)}'><span>{n} {label}</span></div>")
                marks.append(f"<div class='mk end' style='left:{_pct(xy[2], W)};top:{_pct(xy[3], H)}'><span>{n} end</span></div>")
            else:
                marks.append(f"<div class=mk style='left:{_pct(xy[0], W)};top:{_pct(xy[1], H)}'><span>{n} {label}</span></div>")
        acts = []
        for a in s.get("actions") or []:
            cls = "ok" if a.get("ok") else "bad"
            args = json.dumps(a.get("args"), ensure_ascii=False)
            tgt = a.get("target") or {}
            where = f" → {e(tgt.get('process') or '')}" if tgt else ""
            acts.append(f"<div class='act {cls}'>{e(a['name'])} {e(args)}{where}<br><span style='color:var(--mute)'>"
                        f"{e(str(a.get('text', '')))}</span></div>")
            if a.get("zoom_image"):
                acts.append(f"<div class=zoom><img loading=lazy src='{e(a['zoom_image'])}'></div>")
        u = s.get("usage") or {}
        cost = s.get("cost_usd")
        out.append(
            f"<section class=step><div class=shot><img loading=lazy src='{e(s['screenshot'])}'>{''.join(marks)}</div>"
            f"<div><h2>Step {s['step']} <small>{s.get('latency_s', '?')}s · {u.get('prompt_tokens', '?')}+"
            f"{u.get('completion_tokens', '?')} tok{f' · ${cost:.5f}' if cost else ''}"
            f"{' · ' + e(s['finish_reason']) if s.get('finish_reason') not in (None, '', 'stop', 'tool_calls') else ''}"
            f"</small></h2>"
            + (f"<div class=txt>{e(s.get('content') or '')}</div>" if (s.get('content') or '').strip() else "")
            + "".join(acts)
            + (f"<details><summary>reasoning ({len(s['reasoning'])} chars)</summary><pre>{e(s['reasoning'])}</pre></details>"
               if s.get("reasoning") else "")
            + f"<details><summary>details</summary><pre>{e(json.dumps({k: s.get(k) for k in ('foreground', 'cursor', 'frame', 'elements', 'capture_ms', 'attempts', 'tool_calls')}, indent=1, default=str))}</pre></details>"
            + "</div></section>")
    out.append("</main></html>")
    path = run_dir / "report.html"
    path.write_text("".join(out), encoding="utf-8")
    return path
