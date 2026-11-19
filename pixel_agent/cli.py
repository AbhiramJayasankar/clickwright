"""Command line: python -m pixel_agent <command> ...

  check                         environment report (DPI, monitors, UIA, hotkey, profiles)
  models                        list the provider's models with prices
  capabilities -p PROFILE       probe text / image / tool calling (no screen use)
  calibrate -p PROFILE          measure the model's coordinate convention and aim (no screen use)
  sim -p PROFILE [--scene S]    run the agent in the offline simulator
  run -p PROFILE "task"         run on the real desktop (moves the mouse!)
  eval -p PROFILE [--which]     simulator and/or desktop eval with checks
  driver-test                   click-accuracy self-test of the Windows driver (moves the mouse!)
  screenshot out.png            save what the agent would see
  view RUN_DIR                  rebuild and open a run's HTML report
"""
import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .config import load_env, load_profile, list_profiles, RUNS_DIR


def _profile(args):
    return load_profile(args.profile)


def cmd_check(args):
    from .win import DPI_MODE, screen, uia
    from .win.hotkey import KillSwitch
    load_env()
    ks = KillSwitch(args.kill_key).start()
    ks.stop()
    info = {
        "version": __version__, "python": sys.version.split()[0], "dpi_mode": DPI_MODE,
        "monitors": [m.to_dict() for m in screen.monitors()],
        "virtual_screen": screen.virtual_screen().to_dict(),
        "ui_automation": "available" if uia.available() else f"unavailable ({uia.error()})",
        "kill_switch": f"{args.kill_key} registered" if ks.registered else ks.error,
        "profiles": {},
    }
    for name in list_profiles():
        try:
            p = load_profile(name)
            conv, src = p.resolved_coords()
            info["profiles"][name] = {"model": p.model, "key": bool(p.key), "base_url": p.base_url,
                                      "coords": f"{conv} ({src})", "output": p.output}
        except Exception as e:
            info["profiles"][name] = f"error: {e}"
    print(json.dumps(info, indent=1))


def cmd_models(args):
    from .llm import ChatClient
    p = _profile(args)
    c = ChatClient.from_profile(p)
    rows = []
    for m in c.models():
        if not isinstance(m, dict):
            continue
        pr = m.get("pricing") or {}
        rows.append((m.get("id"), pr.get("input_usd_per_1m"), pr.get("output_usd_per_1m"),
                     m.get("context_length"), m.get("tool_call")))
    for mid, pin, pout, ctx, tools in sorted(rows, key=lambda r: str(r[0])):
        if args.filter and args.filter.lower() not in str(mid).lower():
            continue
        price = "free" if str(mid).endswith(":free") else (f"${pin}/${pout} per 1M" if pin is not None else "?")
        print(f"{mid:40} {price:22} ctx={ctx or '?':<9} tools={tools}")


def cmd_capabilities(args):
    from .calibrate import capabilities
    from .llm import ChatClient
    p = _profile(args)
    print(json.dumps(capabilities(ChatClient.from_profile(p), max_tokens=p.max_tokens), indent=1))


def cmd_calibrate(args):
    from .calibrate import calibrate
    p = _profile(args)
    data = calibrate(p, trials=args.trials, workers=args.workers, verbose=True,
                     instruct=tuple(c for c in args.instruct.split(",") if c))
    print(f"\nsaved: {data.get('saved_to')}")
    b = data["best"]
    print(f"best convention: {b.get('convention')} ({b.get('basis')})  median error {b.get('median_px')} px, "
          f"hit rate {b.get('hit_rate')}; native answers consistent: {data['native']['consistent']} "
          f"{data['native']['best_fit_per_answer']}")


def _safety(args, desktop=True):
    from .safety import Safety
    from .win.hotkey import KillSwitch
    ks = None
    if desktop:
        ks = KillSwitch(args.kill_key).start()
        if not ks.registered:
            print(f"warning: {ks.error}", file=sys.stderr)
    s = Safety(allow_apps=args.allow, deny_apps=args.deny, deny_titles=args.deny_title,
               allow_passwords=args.allow_passwords, confirm=args.confirm, kill_switch=ks,
               corner_failsafe=desktop)
    if desktop:
        from .win import screen
        prim = screen.monitors()[0].rect
        s.set_primary_corner(prim.left, prim.top)
    return s


def cmd_run(args):
    from .agent import Agent
    from .driver import WindowsDriver
    p = _profile(args)
    target = f"window:{args.window}" if args.window else ("screen" if args.all_screens else f"monitor:{args.monitor}")
    driver = WindowsDriver(target)
    safety = _safety(args)
    print(f"Stop at any time: {args.kill_key}, or push the mouse into the top-left corner of the main screen.")
    agent = Agent(p, driver, safety=safety, max_steps=args.max_steps, budget_usd=args.budget,
                  token_budget=args.token_budget, element_hints=args.hints or None, dry_run=args.dry_run)
    try:
        res = agent.run(args.task)
    finally:
        if safety.kill_switch:
            safety.kill_switch.stop()
    _finish(res, args)


def cmd_sim(args):
    from .agent import Agent
    from .safety import Safety
    from .sim import SimDriver
    p = _profile(args)
    driver = SimDriver(args.scene, seed=args.seed)
    agent = Agent(p, driver, safety=Safety(corner_failsafe=False), max_steps=args.max_steps,
                  budget_usd=args.budget, element_hints=args.hints or None, platform_name="a simulated desktop")
    res = agent.run(args.task or driver.scene.task)
    passed, details = driver.scene.check(res.answer)
    print(f"check: {'PASS' if passed else 'FAIL'} {json.dumps(details)}")
    _finish(res, args)
    return 0 if passed else 1


def cmd_eval(args):
    from .evaluate import run_eval
    p = _profile(args)
    factory = (lambda: _safety(args)) if args.which in ("desktop", "all") else None
    out, rows, summary = run_eval(p, which=args.which, only=args.only, repeats=args.repeats,
                                  max_steps=args.max_steps, seed=args.seed, budget_usd=args.budget,
                                  safety_factory=factory, element_hints=args.hints or None)
    print((out / "results.md").read_text(encoding="utf-8"))
    print(f"results: {out}")


def cmd_driver_test(args):
    from .drivertest import run
    print("Moving the mouse in 2 seconds. Do not touch it until the white window closes.")
    import time
    time.sleep(2)
    rep = run(args.monitor, args.max_long_edge)
    print(json.dumps(rep, indent=1))
    print("PASS" if rep["passed"] else "FAIL")
    return 0 if rep["passed"] else 1


def cmd_screenshot(args):
    from .driver import WindowsDriver
    d = WindowsDriver(f"window:{args.window}" if args.window else f"monitor:{args.monitor}")
    obs = d.observe(args.max_long_edge, with_elements=args.hints)
    Path(args.out).write_bytes(obs.png)
    print(json.dumps({"saved": args.out, "frame": obs.frame.to_dict(), "cursor": obs.cursor,
                      "foreground": obs.foreground, "elements": len(obs.elements),
                      "capture_ms": round(obs.capture_ms)}, indent=1))


def cmd_view(args):
    from .viewer import write_report
    d = Path(args.run_dir) if args.run_dir else max((x for x in RUNS_DIR.iterdir() if (x / "meta.json").exists()),
                                                     key=lambda x: x.stat().st_mtime)
    path = write_report(d)
    print(path)
    if not args.no_open:
        webbrowser.open(path.as_uri())


def _finish(res, args):
    print(json.dumps({"status": res.status, "answer": res.answer, "reason": res.reason, "steps": res.steps,
                      "seconds": res.duration_s, "totals": res.totals, "run_dir": res.run_dir}, indent=1))
    if res.run_dir:
        print(f"report: {Path(res.run_dir) / 'report.html'}")
        if getattr(args, "open", False):
            webbrowser.open((Path(res.run_dir) / "report.html").as_uri())


def build_parser():
    ap = argparse.ArgumentParser(prog="pixel-agent", description="Model-agnostic pixel computer-use agent")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def prof(sp):
        sp.add_argument("-p", "--profile", default=os.environ.get("PIXEL_AGENT_PROFILE", "deepseek-flash-free"))

    def runopts(sp):
        sp.add_argument("--max-steps", type=int, default=40)
        sp.add_argument("--budget", type=float, help="stop when the run has cost this many USD")
        sp.add_argument("--token-budget", type=int, help="stop after this many tokens")
        sp.add_argument("--hints", action="store_true", help="send UI Automation element boxes as text hints")
        sp.add_argument("--open", action="store_true", help="open the HTML report when done")

    def safetyopts(sp):
        sp.add_argument("--allow", action="append", default=[], metavar="APP.exe",
                        help="only act on these apps (repeatable)")
        sp.add_argument("--deny", action="append", default=[], metavar="APP.exe", help="never act on these apps")
        sp.add_argument("--deny-title", action="append", default=[], metavar="REGEX")
        sp.add_argument("--allow-passwords", action="store_true")
        sp.add_argument("--confirm", action="store_true", help="ask y/n before every input action")
        sp.add_argument("--kill-key", default="ctrl+alt+q")

    sp = sub.add_parser("check")
    sp.add_argument("--kill-key", default="ctrl+alt+q")
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("models")
    prof(sp)
    sp.add_argument("--filter")
    sp.set_defaults(fn=cmd_models)

    sp = sub.add_parser("capabilities")
    prof(sp)
    sp.set_defaults(fn=cmd_capabilities)

    sp = sub.add_parser("calibrate")
    prof(sp)
    sp.add_argument("--trials", type=int, default=16)
    sp.add_argument("--workers", type=int, default=4)
    sp.add_argument("--instruct", default="pixels,norm1000",
                    help="conventions to test when stated in the prompt (comma list; empty = native only)")
    sp.set_defaults(fn=cmd_calibrate)

    sp = sub.add_parser("sim")
    prof(sp)
    runopts(sp)
    sp.add_argument("--scene", default="form", choices=["form", "targets", "scroll", "slider", "menu"])
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--task", help="override the scene's task text")
    sp.set_defaults(fn=cmd_sim)

    sp = sub.add_parser("run")
    prof(sp)
    runopts(sp)
    safetyopts(sp)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--monitor", type=int, default=0, help="monitor index (0 = primary)")
    g.add_argument("--window", metavar="REGEX", help="only show and act inside this window")
    g.add_argument("--all-screens", action="store_true")
    sp.add_argument("--dry-run", action="store_true", help="log actions without executing them")
    sp.add_argument("task")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("eval")
    prof(sp)
    safetyopts(sp)
    sp.add_argument("--which", choices=["sim", "desktop", "all"], default="sim")
    sp.add_argument("--only", nargs="*", help="task ids / scene names")
    sp.add_argument("--repeats", type=int, default=1)
    sp.add_argument("--max-steps", type=int)
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--budget", type=float)
    sp.add_argument("--hints", action="store_true")
    sp.set_defaults(fn=cmd_eval)

    sp = sub.add_parser("driver-test")
    sp.add_argument("--monitor", type=int, default=0)
    sp.add_argument("--max-long-edge", type=int, default=1280)
    sp.set_defaults(fn=cmd_driver_test)

    sp = sub.add_parser("screenshot")
    sp.add_argument("out")
    sp.add_argument("--monitor", type=int, default=0)
    sp.add_argument("--window")
    sp.add_argument("--max-long-edge", type=int, default=1280)
    sp.add_argument("--hints", action="store_true")
    sp.set_defaults(fn=cmd_screenshot)

    sp = sub.add_parser("view")
    sp.add_argument("run_dir", nargs="?")
    sp.add_argument("--no-open", action="store_true")
    sp.set_defaults(fn=cmd_view)
    return ap


def main(argv=None):
    # The Windows console defaults to cp1252; model text often has arrows, emoji, CJK.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    load_env()
    args = build_parser().parse_args(argv)
    rc = args.fn(args)
    return rc or 0
