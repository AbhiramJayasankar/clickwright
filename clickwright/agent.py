"""The agent loop: screenshot -> model -> actions -> screenshot ..."""
import json
import time
from dataclasses import dataclass, field

from . import actions as A
from .geometry import Rect, model_to_image
from .llm import ChatClient, ModelError, image_part
from .prompt import system_prompt, observation_text
from .runlog import RunLog
from .safety import Safety, StopRun

EVICTED = {"type": "text", "text": "[screenshot evicted]"}


@dataclass
class RunResult:
    status: str                 # done | failed | max_steps | budget | stopped | stuck | error
    answer: str = ""
    success: bool | None = None
    steps: int = 0
    reason: str = ""
    totals: dict = field(default_factory=dict)
    run_dir: str = ""
    duration_s: float = 0.0


class Agent:
    def __init__(self, profile, driver, client=None, safety=None, max_steps=40, budget_usd=None,
                 token_budget=None, element_hints=None, dry_run=False, log=True, runs_dir=None,
                 verbose=True, platform_name=None, extra_instructions=""):
        self.profile = profile
        self.driver = driver
        self.client = client or ChatClient.from_profile(profile)
        self.safety = safety or Safety(corner_failsafe=False)
        self.max_steps = max_steps
        self.budget_usd = budget_usd
        self.token_budget = token_budget
        self.element_hints = profile.element_hints if element_hints is None else element_hints
        self.dry_run = dry_run
        self.log_enabled = log
        self.runs_dir = runs_dir
        self.verbose = verbose
        self.platform_name = platform_name
        self.extra_instructions = extra_instructions
        self.convention, self.convention_source = profile.resolved_coords()
        self._image_parts = []   # every image part sent, oldest first (mutated in place on eviction)
        self._evicted = 0
        self.messages = []

    # --- helpers -----------------------------------------------------------
    def _say(self, text):
        if self.verbose:
            print(text, flush=True)

    def _observe(self):
        self.safety.check_kill(self.driver.cursor())
        obs = self.driver.observe(self.profile.max_long_edge, with_elements=self.element_hints,
                                  max_elements=self.profile.max_elements)
        obs.note = self.safety.check_foreground(obs.foreground)
        if obs.redacted:
            obs.note = (obs.note + " " if obs.note else "") +                 f"{obs.redacted} private area(s) are blacked out; ignore them."
        return obs

    def _add_image(self, png):
        part = image_part(png)
        self._image_parts.append(part)
        return part

    def _evict(self):
        keep = max(1, self.profile.keep_screenshots)
        batch = max(1, self.profile.evict_batch)
        live = len(self._image_parts) - self._evicted
        if live > keep + batch - 1:
            target = len(self._image_parts) - keep
            for part in self._image_parts[self._evicted:target]:
                part.clear()
                part.update(EVICTED)
            self._evicted = target
        if self.profile.max_request_mb:
            self._evict_to_size(self.profile.max_request_mb * 1_000_000)

    def _request_bytes(self):
        return len(json.dumps(self.messages, ensure_ascii=False).encode("utf-8"))

    def _evict_oldest(self):
        """Evict the oldest live screenshot, never the newest. False if nothing left to evict."""
        if len(self._image_parts) - self._evicted <= 1:
            return False
        part = self._image_parts[self._evicted]
        part.clear()
        part.update(EVICTED)
        self._evicted += 1
        return True

    def _evict_to_size(self, limit):
        # 5% headroom for tool schemas and headers.
        while self._request_bytes() > limit * 0.95 and self._evict_oldest():
            pass

    def _to_image(self, x, y, frame):
        ix, iy = model_to_image(x, y, self.convention, frame.image_w, frame.image_h)
        tol_x, tol_y = frame.image_w * 0.02, frame.image_h * 0.02
        if ix < -tol_x or iy < -tol_y or ix > frame.image_w - 1 + tol_x or iy > frame.image_h - 1 + tol_y:
            raise ValueError(f"({x}, {y}) is outside the screenshot "
                             f"({'%dx%d' % (frame.image_w, frame.image_h) if self.convention == 'pixels' else self.convention})")
        return frame.clamp_image(ix, iy)

    def _to_screen(self, x, y, frame):
        ix, iy = self._to_image(x, y, frame)
        return (ix, iy), frame.image_to_screen(ix, iy)

    # --- executing one action ------------------------------------------------
    def _execute(self, act, obs):
        rec = {"name": act.name, "args": act.args}
        if act.error:
            rec.update(ok=False, text=f"error: {act.error}")
            return rec
        frame, d, s = obs.frame, self.driver, self.safety
        a = act.args
        t0 = time.time()
        try:
            if act.name == "answer":
                rec.update(ok=True, text="finished", answer=a["text"], success=a["success"])
                return rec
            if act.name == "screenshot":
                rec.update(ok=True, text="ok, a new screenshot follows")
                return rec
            if act.name == "wait":
                d.wait(a["seconds"])
                rec.update(ok=True, text=f"waited {a['seconds']}s")
                return rec
            if act.name == "zoom":
                (ix1, iy1), (sx1, sy1) = self._to_screen(a["x1"], a["y1"], frame)
                (ix2, iy2), (sx2, sy2) = self._to_screen(a["x2"], a["y2"], frame)
                region = Rect(min(sx1, sx2), min(sy1, sy2), max(2, abs(sx2 - sx1)), max(2, abs(sy2 - sy1)))
                png, w, h = d.zoom(region, self.profile.max_long_edge)
                rec.update(ok=True, image_xy=[ix1, iy1, ix2, iy2], screen_rect=region.to_dict(),
                           text=f"zoomed view of ({a['x1']},{a['y1']})-({a['x2']},{a['y2']}) follows as an image "
                                f"({w}x{h}); keep using full-screenshot coordinates", zoom_png=png)
                return rec

            # input actions -------------------------------------------------
            if act.name in A.POINTER_ACTIONS:
                if act.name == "drag":
                    (ix1, iy1), (sx1, sy1) = self._to_screen(a["x1"], a["y1"], frame)
                    (ix2, iy2), (sx2, sy2) = self._to_screen(a["x2"], a["y2"], frame)
                    rec.update(image_xy=[ix1, iy1, ix2, iy2], screen_xy=[sx1, sy1, sx2, sy2])
                    target = d.window_at(sx1, sy1)
                else:
                    (ix, iy), (sx, sy) = self._to_screen(a["x"], a["y"], frame)
                    rec.update(image_xy=[ix, iy], screen_xy=[sx, sy])
                    target = d.window_at(sx, sy)
            else:
                target = d.foreground()
            if target:
                rec["target"] = {"title": (target.get("title") or "")[:80], "process": target.get("process")}
            # Hovering and window-switching shortcuts send nothing into the app under them.
            harmless = act.name == "move" or (act.name == "key" and s.is_navigation_key(a["keys"]))
            err = None if harmless else s.check_target(act.name, target)
            if err is None and act.name == "type":
                err = s.check_typing(d.focused_is_password())
            if err:
                rec.update(ok=False, text=err)
                return rec
            desc = _describe(act, rec, target)
            if not s.ask(desc):
                rec.update(ok=False, text="refused by the user at the confirmation prompt")
                return rec
            s.check_kill(d.cursor())
            if self.dry_run:
                rec.update(ok=True, text=f"(dry run, not executed) {desc}")
                return rec
            if act.name == "click":
                d.click(sx, sy, a["button"], a["clicks"], a["modifiers"])
                s.note_agent_cursor((sx, sy))
            elif act.name == "move":
                d.move(sx, sy)
                s.note_agent_cursor((sx, sy))
            elif act.name == "drag":
                d.drag(sx1, sy1, sx2, sy2)
                s.note_agent_cursor((sx2, sy2))
            elif act.name == "scroll":
                d.scroll(sx, sy, a["direction"], a["amount"])
                s.note_agent_cursor((sx, sy))
            elif act.name == "type":
                d.type_text(a["text"])
            elif act.name == "key":
                d.key(a["keys"], a["repeat"])
            rec.update(ok=True, text="ok")
            return rec
        except StopRun:
            raise
        except (ValueError, KeyError) as e:
            rec.update(ok=False, text=f"error: {e}")
            return rec
        except OSError as e:
            rec.update(ok=False, text=f"error: {e}")
            return rec
        finally:
            rec["ms"] = round((time.time() - t0) * 1000)

    # --- the loop --------------------------------------------------------------
    def run(self, task):
        p = self.profile
        tool_mode = p.output == "tool_calls"
        log = RunLog(task, p.name, self.runs_dir, enabled=self.log_enabled, meta={
            "profile_settings": p.public_dict(), "convention": self.convention,
            "convention_source": self.convention_source, "driver": self.driver.name,
            "target": getattr(self.driver, "target", ""), "max_steps": self.max_steps,
            "dry_run": self.dry_run, "element_hints": self.element_hints})
        t_start = time.time()
        result = RunResult("error")
        step = 0
        try:
            obs = self._observe()
            f = obs.frame
            sys_text = system_prompt(self.convention, f.image_w, f.image_h, p.output, self.platform_name,
                                     self.extra_instructions)
            log.image("step_001.png", obs.png)
            first = [{"type": "text", "text": f"Task: {task}\n\n" + observation_text(obs, self.convention, 1, self.max_steps, note=obs.note)},
                     self._add_image(obs.png)]
            self.messages = [{"role": "system", "content": sys_text}, {"role": "user", "content": first}]
            tools = A.tool_schemas(self.convention) if tool_mode else None
            empty_replies, failing_replies = 0, 0
            self._say(f"[clickwright] {p.name} ({p.model}) | coords={self.convention} ({self.convention_source}) | "
                      f"screenshot {f.image_w}x{f.image_h} | log: {log.dir if self.log_enabled else 'off'}")

            while True:
                step += 1
                if step > self.max_steps:
                    result = RunResult("max_steps", reason=f"reached {self.max_steps} steps")
                    break
                tot = self.client.total
                if self.budget_usd is not None and tot["cost_usd"] >= self.budget_usd:
                    result = RunResult("budget", reason=f"spent ${tot['cost_usd']:.4f} of ${self.budget_usd}")
                    break
                if self.token_budget is not None and tot["prompt_tokens"] + tot["completion_tokens"] >= self.token_budget:
                    result = RunResult("budget", reason=f"used {tot['prompt_tokens'] + tot['completion_tokens']} tokens")
                    break
                self.safety.check_kill(self.driver.cursor())

                self._evict()
                try:
                    comp = self.client.complete(self.messages, tools=tools, max_tokens=p.max_tokens,
                                                temperature=p.temperature)
                except ModelError as e:
                    comp = None
                    if e.status == 413:
                        # Request too large for the provider: keep only the newest screenshot and retry once.
                        while self._evict_oldest():
                            pass
                        self._say("  request too large (HTTP 413); dropped older screenshots and retrying")
                        try:
                            comp = self.client.complete(self.messages, tools=tools, max_tokens=p.max_tokens,
                                                        temperature=p.temperature)
                        except ModelError as e2:
                            e = e2
                    if comp is None:
                        log.step({"step": step, "error": str(e), "status": e.status})
                        result = RunResult("error", reason=f"model call failed: {e}")
                        break

                if tool_mode and comp.tool_calls:
                    acts = A.from_tool_calls(comp.tool_calls)
                else:
                    acts = A.from_text(comp.content)
                assistant = {"role": "assistant", "content": comp.content or ""}
                if tool_mode and comp.tool_calls:
                    assistant["tool_calls"] = [{"id": tc.get("id"), "type": "function",
                                                "function": {"name": (tc.get("function") or {}).get("name"),
                                                             "arguments": (tc.get("function") or {}).get("arguments") or "{}"}}
                                               for tc in comp.tool_calls]
                if p.carry_reasoning and comp.reasoning:
                    assistant["reasoning_content"] = comp.reasoning
                self.messages.append(assistant)

                record = {"step": step, "screenshot": f"step_{step:03d}.png", "frame": obs.frame.to_dict(),
                          "cursor": obs.cursor, "foreground": obs.foreground, "elements": len(obs.elements),
                          "capture_ms": round(obs.capture_ms), "content": comp.content, "reasoning": comp.reasoning,
                          "tool_calls": comp.tool_calls, "finish_reason": comp.finish_reason, "usage": comp.usage,
                          "cost_usd": comp.cost, "latency_s": round(comp.latency, 2), "attempts": comp.attempts}

                if not acts:
                    empty_replies += 1
                    record["actions"] = []
                    log.step(record)
                    if p.end_mode == "no_tool_call" and (comp.content or "").strip():
                        result = RunResult("done", answer=comp.content.strip(), success=True)
                        break
                    if comp.finish_reason == "length":
                        nudge = ("Your reply was cut off by the output limit before any action. "
                                 "Think less and reply with an action.")
                    else:
                        nudge = ("No action was found in your reply. Call one of the tools, or call answer if the "
                                 "task is finished." if tool_mode else
                                 "No action was found. Reply with the JSON object described in the instructions.")
                    self._say(f"  step {step}: no action ({comp.finish_reason or 'stop'}), nudging")
                    if empty_replies >= 3:
                        result = RunResult("stuck", reason="three replies in a row without an action")
                        break
                    self.messages.append({"role": "user", "content": nudge})
                    continue
                empty_replies = 0

                results, zooms, finished, broken = [], [], None, False
                for act in acts:
                    if broken:
                        rec = {"name": act.name, "args": act.args, "ok": False,
                               "text": "not run: an earlier action in this reply failed"}
                    else:
                        rec = self._execute(act, obs)
                        if not rec["ok"]:
                            broken = True
                    if rec.get("zoom_png"):
                        zooms.append((rec.pop("zoom_png"), rec))
                    if "answer" in rec:
                        finished = rec
                    rec["call_id"] = act.call_id
                    results.append(rec)
                    if finished:
                        break
                failing_replies = failing_replies + 1 if all(not r["ok"] for r in results) else 0
                record["actions"] = results
                for i, (png, rec) in enumerate(zooms, 1):
                    rec["zoom_image"] = log.image(f"step_{step:03d}_zoom{i}.png", png)
                log.step(record)
                self._say(f"  step {step}: " + "; ".join(_short(r) for r in results)
                          + f" | {comp.latency:.1f}s" + (f" | ${comp.cost:.5f}" if comp.cost else ""))

                if tool_mode and comp.tool_calls:
                    answered = set()
                    for r in results:
                        if r.get("call_id"):
                            self.messages.append({"role": "tool", "tool_call_id": r["call_id"], "content": r["text"]})
                            answered.add(r["call_id"])
                    for tc in comp.tool_calls:  # every call needs a result, even if skipped
                        if tc.get("id") and tc["id"] not in answered:
                            self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                                  "content": "not run: the task already finished"})
                    result_note = ""
                else:
                    result_note = "Results of your actions: " + "; ".join(
                        f"{r['name']}: {r['text']}" for r in results)

                if finished:
                    ok = finished.get("success", True)
                    result = RunResult("done" if ok else "failed", answer=finished.get("answer", ""), success=ok)
                    break
                if failing_replies >= 5:
                    result = RunResult("stuck", reason="five replies in a row where every action failed")
                    break

                if any(r["ok"] and r["name"] in A.INPUT_ACTIONS for r in results) and not self.dry_run:
                    self.driver.settle()
                obs = self._observe()
                log.image(f"step_{step + 1:03d}.png", obs.png)
                content = []
                text = observation_text(obs, self.convention, step + 1, self.max_steps,
                                        note=" ".join(x for x in (result_note, obs.note) if x))
                content.append({"type": "text", "text": text})
                for png, rec in zooms:
                    content.append({"type": "text", "text": f"Zoomed view ({rec['text'].split(' follows')[0]}):"})
                    content.append(self._add_image(png))
                content.append({"type": "text", "text": "Current screenshot:"})
                content.append(self._add_image(obs.png))
                self.messages.append({"role": "user", "content": content})
        except StopRun as e:
            result = RunResult("stopped", reason=str(e))
            self._say(f"[clickwright] stopped: {e}")
        except KeyboardInterrupt:
            result = RunResult("stopped", reason="interrupted (Ctrl+C)")
        except Exception as e:
            result = RunResult("error", reason=f"{type(e).__name__}: {e}")
            raise
        finally:
            result.steps = step if step <= self.max_steps else self.max_steps
            result.totals = dict(self.client.total)
            result.duration_s = round(time.time() - t_start, 2)
            result.run_dir = str(log.dir) if self.log_enabled else ""
            log.finish({"status": result.status, "answer": result.answer, "success": result.success,
                        "reason": result.reason, "steps": result.steps, "totals": result.totals})
        self._say(f"[clickwright] {result.status}" + (f": {result.answer}" if result.answer else "")
                  + (f" ({result.reason})" if result.reason else "")
                  + f" | {result.steps} steps, {result.duration_s}s, "
                  + f"{result.totals.get('prompt_tokens', 0)}+{result.totals.get('completion_tokens', 0)} tokens"
                  + (f", ${result.totals.get('cost_usd', 0):.4f}" if result.totals.get('cost_usd') else ""))
        return result


def _describe(act, rec, target):
    a = act.args
    where = f" on {target.get('process')}" if target and target.get("process") else ""
    if act.name == "click":
        n = {1: "", 2: "double ", 3: "triple "}[a["clicks"]]
        mods = "+".join(a["modifiers"]) + "+" if a["modifiers"] else ""
        return f"{mods}{n}{a['button']} click at screen {tuple(rec['screen_xy'])}{where}"
    if act.name == "move":
        return f"move mouse to {tuple(rec['screen_xy'])}"
    if act.name == "drag":
        return f"drag {tuple(rec['screen_xy'][:2])} -> {tuple(rec['screen_xy'][2:])}{where}"
    if act.name == "scroll":
        return f"scroll {a['direction']} x{a['amount']} at {tuple(rec['screen_xy'])}{where}"
    if act.name == "type":
        t = a["text"]
        return f"type {t[:60]!r}{'...' if len(t) > 60 else ''}{where}"
    if act.name == "key":
        return f"press {a['keys']}" + (f" x{a['repeat']}" if a["repeat"] > 1 else "") + where
    return act.name


def _short(r):
    a = r.get("args") or {}
    if r["name"] in ("click", "move", "scroll") and "x" in a:
        s = f"{r['name']}({a['x']},{a['y']})"
    elif r["name"] in ("drag", "zoom") and "x1" in a:
        s = f"{r['name']}({a['x1']},{a['y1']}->{a['x2']},{a['y2']})"
    elif r["name"] == "type":
        s = f"type({a.get('text', '')[:24]!r})"
    elif r["name"] == "key":
        s = f"key({a.get('keys')})"
    elif r["name"] == "answer":
        s = f"answer({str(a.get('text', ''))[:40]!r})"
    else:
        s = r["name"]
    return s + ("" if r["ok"] else f" FAILED[{r['text'][:60]}]")
