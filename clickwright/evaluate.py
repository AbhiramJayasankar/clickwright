"""Eval runner: simulator scenes and real-desktop tasks, with automatic checks.

Results go to runs/eval-<time>/results.json and results.md (one row per task:
pass/fail, steps, time, tokens, cost, and the run folder for the replay).
"""
import csv
import io
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
from datetime import datetime
from pathlib import Path

from .agent import Agent
from .config import ROOT, RUNS_DIR
from .llm import ChatClient
from .safety import Safety
from .sim import SCENES, SimDriver

TASKS_FILE = ROOT / "tasks" / "desktop.toml"


def load_desktop_tasks():
    return tomllib.loads(TASKS_FILE.read_text(encoding="utf-8"))["task"]


def _fmt(value, work):
    if isinstance(value, str):
        return value.replace("{work}", str(work))
    if isinstance(value, list):
        return [_fmt(v, work) for v in value]
    if isinstance(value, dict):
        return {k: _fmt(v, work) for k, v in value.items()}
    return value


def _pids(names):
    """PIDs of running processes with these image names."""
    out = {}
    try:
        text = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return out
    wanted = {n.lower() for n in names}
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[0].lower() in wanted:
            out.setdefault(row[0].lower(), set()).add(int(row[1]))
    return out


def check_desktop(task, answer, work):
    c = _fmt(task["check"], work)
    kind = c["kind"]
    answer = answer or ""
    if kind == "file_contains":
        p = Path(c["path"])
        if not p.exists():
            return False, f"{p} does not exist"
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        ok = c["text"] in text and not (c.get("also_absent") and c["also_absent"] in text)
        return ok, f"content: {text[:120]!r}"
    if kind == "dir_exists":
        return Path(c["path"]).is_dir(), c["path"]
    if kind == "file_exists_min_bytes":
        p = Path(c["path"])
        return p.exists() and p.stat().st_size >= c["bytes"], f"{p} size {p.stat().st_size if p.exists() else 'missing'}"
    if kind == "answer_contains":
        return any(x.lower() in answer.lower() for x in c["any"]), f"answer {answer[:80]!r}"
    if kind == "answer_regex":
        return bool(re.search(c["pattern"], answer, re.I)), f"answer {answer[:80]!r}"
    if kind == "answer_equals_cmd":
        out = subprocess.run(c["cmd"], capture_output=True, text=True, timeout=20).stdout
        m = re.search(c["pattern"], out)
        truth = m.group(1) if m else None
        return bool(truth) and truth.lower() in answer.lower(), f"truth {truth!r}, answer {answer[:80]!r}"
    raise ValueError(f"unknown check kind {kind}")


def run_eval(profile, which="sim", only=None, repeats=1, max_steps=None, seed=0, budget_usd=None,
             safety_factory=None, driver_target="monitor:0", verbose=True, element_hints=None):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = RUNS_DIR / f"eval-{stamp}-{profile.name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    jobs = []
    if which in ("sim", "all"):
        for name in SCENES:
            if not only or name in only:
                jobs.append(("sim", name, None))
    if which in ("desktop", "all"):
        for t in load_desktop_tasks():
            if not only or t["id"] in only:
                jobs.append(("desktop", t["id"], t))
    for rep in range(repeats):
        for kind, name, task in jobs:
            client = ChatClient.from_profile(profile)
            t0 = time.time()
            if kind == "sim":
                driver = SimDriver(name, seed=seed + rep)
                agent = Agent(profile, driver, client, Safety(corner_failsafe=False), max_steps=max_steps or 30,
                              budget_usd=budget_usd, runs_dir=out_dir, verbose=verbose,
                              platform_name="a simulated desktop", element_hints=element_hints)
                res = agent.run(driver.scene.task)
                passed, details = driver.scene.check(res.answer)
            else:
                from .driver import WindowsDriver
                work = out_dir / "work" / f"{name}-{rep}"
                work.mkdir(parents=True, exist_ok=True)
                for fname, content in (task.get("write_files") or {}).items():
                    (work / fname).write_text(content, encoding="utf-8")
                from .win import windows as W
                before = _pids(task.get("cleanup") or [])
                # Safety first: --protect-existing must snapshot windows before the task opens its own.
                safety = safety_factory() if safety_factory else Safety()
                existing = {w["hwnd"] for w in W.all_top_level()}
                busy = _protected_single_instance(task, safety, W)
                if busy:
                    if safety.kill_switch:
                        safety.kill_switch.stop()
                    rows.append(_skipped_row(kind, name, rep, busy))
                    if verbose:
                        print(f"== {kind}:{name} #{rep}: SKIP ({busy})", flush=True)
                    continue
                for cmd in task.get("setup") or []:
                    subprocess.Popen(_fmt(cmd, work), close_fds=True)
                time.sleep(2.5 if task.get("setup") else 0.5)
                driver = WindowsDriver(driver_target, protect=safety.is_protected)
                agent = Agent(profile, driver, client, safety, max_steps=max_steps or task.get("max_steps", 25),
                              budget_usd=budget_usd, runs_dir=out_dir, verbose=verbose, element_hints=element_hints)
                try:
                    res = agent.run(_fmt(task["prompt"], work))
                finally:
                    if safety.kill_switch:
                        safety.kill_switch.stop()
                passed, details = check_desktop(task, res.answer, work)
                # Close the windows this task opened (so the next task's snapshot doesn't
                # protect them), then any leftover processes it started.
                W.close_windows([w["hwnd"] for w in W.all_top_level()
                                 if w["hwnd"] not in existing and not W.is_shell(w)])
                time.sleep(1.0)
                after = _pids(task.get("cleanup") or [])
                for proc, pids in after.items():
                    for pid in pids - before.get(proc, set()):
                        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
                if res.status == "stopped":
                    rows.append(_row(kind, name, rep, res, passed, details, t0))
                    break
            rows.append(_row(kind, name, rep, res, passed, details, t0))
            if verbose:
                print(f"== {kind}:{name} #{rep}: {'PASS' if passed else 'FAIL'} ({res.status}, {res.steps} steps)",
                      flush=True)
    summary = _summary(rows)
    (out_dir / "results.json").write_text(json.dumps({"profile": profile.name, "model": profile.model,
                                                      "rows": rows, "summary": summary}, indent=2, default=str),
                                          encoding="utf-8")
    (out_dir / "results.md").write_text(_markdown(profile, rows, summary), encoding="utf-8")
    shutil.rmtree(out_dir / "work", ignore_errors=True)
    return out_dir, rows, summary


def _protected_single_instance(task, safety, W):
    """Single-instance apps (Settings) reuse their open window. If that window is
    protected, the agent could never see it, so the task is skipped, not failed.
    `single_instance` lists window-title regexes (UWP windows all belong to
    ApplicationFrameHost.exe, so the process name doesn't say which app it is)."""
    pats = [re.compile(t) for t in task.get("single_instance") or []]
    if not pats:
        return None
    for w in W.all_top_level():
        if any(p.search(w.get("title") or "") for p in pats) and safety.is_protected(w):
            return "the app is already open in a protected window; close it and rerun"
    return None


def _skipped_row(kind, name, rep, reason):
    return {"kind": kind, "task": name, "repeat": rep, "passed": False, "status": "skipped", "answer": "",
            "reason": reason, "steps": 0, "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
            "cost_usd": 0.0, "run_dir": "", "details": reason}


def _row(kind, name, rep, res, passed, details, t0):
    tot = res.totals or {}
    return {"kind": kind, "task": name, "repeat": rep, "passed": bool(passed), "status": res.status,
            "answer": res.answer, "reason": res.reason, "steps": res.steps, "seconds": round(time.time() - t0, 1),
            "prompt_tokens": tot.get("prompt_tokens", 0), "completion_tokens": tot.get("completion_tokens", 0),
            "cost_usd": round(tot.get("cost_usd", 0.0), 5), "run_dir": res.run_dir, "details": details}


def _summary(rows):
    skipped = sum(r["status"] == "skipped" for r in rows)
    rows = [r for r in rows if r["status"] != "skipped"]
    if not rows:
        return {"tasks": 0, "passed": 0, "skipped": skipped}
    n = len(rows)
    return {"tasks": n, "passed": sum(r["passed"] for r in rows), "skipped": skipped,
            "pass_rate": round(sum(r["passed"] for r in rows) / n, 3),
            "avg_steps": round(sum(r["steps"] for r in rows) / n, 1),
            "avg_seconds": round(sum(r["seconds"] for r in rows) / n, 1),
            "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "total_tokens": sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows)}


def _markdown(profile, rows, s):
    lines = [f"# Eval: {profile.name} ({profile.model})", "",
             f"Passed {s.get('passed', 0)}/{s.get('tasks', 0)}"
             + (f" ({s['skipped']} skipped)" if s.get("skipped") else "") + f" · avg {s.get('avg_steps')} steps · "
             f"avg {s.get('avg_seconds')}s · ${s.get('total_cost_usd', 0)} · {s.get('total_tokens', 0)} tokens", "",
             "| task | result | status | steps | seconds | tokens | cost | run |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['kind']}:{r['task']} | {'PASS' if r['passed'] else ('SKIP' if r['status'] == 'skipped' else 'FAIL')} | {r['status']} | {r['steps']} | "
                     f"{r['seconds']} | {r['prompt_tokens'] + r['completion_tokens']} | ${r['cost_usd']} | "
                     f"{os.path.basename(r['run_dir'] or '')} |")
    return "\n".join(lines) + "\n"
