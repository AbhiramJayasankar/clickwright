"""Run logs: one folder per run with every screenshot, the raw model output,
parsed actions, timings, tokens and cost. viewer.py turns it into HTML."""
import json
import re
import time
from datetime import datetime
from pathlib import Path

from .config import RUNS_DIR


def _slug(text, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s[:n].strip("-") or "run"


class RunLog:
    def __init__(self, task, profile_name, base_dir=None, meta=None, enabled=True):
        self.enabled = enabled
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = Path(base_dir) if base_dir else RUNS_DIR
        self.dir = base / f"{stamp}-{_slug(profile_name, 24)}-{_slug(task)}"
        n = 1
        while enabled and self.dir.exists():
            n += 1
            self.dir = base / f"{stamp}-{n}-{_slug(profile_name, 24)}-{_slug(task)}"
        self.t0 = time.time()
        self.meta = {"task": task, "profile": profile_name, "started": datetime.now().isoformat(timespec="seconds"),
                     **(meta or {})}
        self._steps = None
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "meta.json").write_text(json.dumps(self.meta, indent=2, default=str), encoding="utf-8")
            self._steps = open(self.dir / "steps.jsonl", "a", encoding="utf-8")

    def image(self, name, png_bytes):
        if not self.enabled:
            return None
        (self.dir / name).write_bytes(png_bytes)
        return name

    def step(self, record):
        record = {"t": round(time.time() - self.t0, 3), **record}
        if self._steps:
            self._steps.write(json.dumps(record, default=str) + "\n")
            self._steps.flush()

    def finish(self, summary):
        summary = {**self.meta, **summary, "duration_s": round(time.time() - self.t0, 2)}
        if self.enabled:
            if self._steps:
                self._steps.close()
                self._steps = None
            (self.dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
            try:
                from .viewer import write_report
                write_report(self.dir)
            except Exception as e:  # the log matters more than the report
                (self.dir / "report-error.txt").write_text(repr(e), encoding="utf-8")
        return summary


def read_run(run_dir):
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    steps = []
    f = run_dir / "steps.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                steps.append(json.loads(line))
    summary = None
    if (run_dir / "summary.json").exists():
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return meta, steps, summary
