"""Capability check and coordinate calibration for a model endpoint.

Nothing here touches the real screen: test images are drawn offscreen and
sent to the model, and its answers are scored against the known truth.

Calibration asks for the centre of a described shape on several images of
different sizes, then tries every coordinate convention on the answers and
keeps the one with the smallest error. Using two aspect ratios separates
"pixels" from the normalised grids even when a model's answers are noisy.
"""
import concurrent.futures as cf
import json
import random
import statistics
import time
from datetime import datetime

from . import actions as A
from .config import save_calibration
from .geometry import CONVENTIONS, model_to_image
from .llm import ChatClient, ModelError, image_part

COLORS = {"red": (220, 38, 38), "blue": (37, 99, 235), "green": (22, 163, 74), "orange": (234, 88, 12),
          "purple": (147, 51, 234), "black": (20, 20, 20)}
SHAPES = ("square", "circle")
SIZES = [(1280, 800), (1024, 768), (800, 1200), (1366, 768)]


def _draw_trial(rng, size_px, img_w, img_h):
    from .win.screen import Canvas
    c = Canvas(img_w, img_h, (255, 255, 255))
    # light UI-ish clutter so the image is not trivially empty
    for _ in range(10):
        x, y = rng.randint(0, img_w - 120), rng.randint(0, img_h - 40)
        c.rect(x, y, rng.randint(60, 220), rng.randint(20, 60), fill=(rng.randint(225, 245),) * 3)
    for _ in range(6):
        c.text(rng.randint(0, img_w - 200), rng.randint(0, img_h - 30), rng.choice(
            ["Settings", "File", "Open", "Search", "Cancel", "Help", "Account", "Downloads"]), 16, (90, 90, 90))
    used = set()
    shapes = []
    # one target plus three distractors of other colour/shape combinations
    combos = [(col, sh) for col in COLORS for sh in SHAPES]
    rng.shuffle(combos)
    for i, (col, sh) in enumerate(combos[:4]):
        s = size_px if i == 0 else rng.choice([16, 24, 36, 48])
        for _ in range(100):
            margin = 4 if i == 0 and rng.random() < 0.35 else 30  # sometimes hug an edge
            x = rng.randint(margin, img_w - s - margin)
            y = rng.randint(margin, img_h - s - margin)
            if all(abs(x - ux) > 70 or abs(y - uy) > 70 for ux, uy in used):
                break
        used.add((x, y))
        if sh == "square":
            c.rect(x, y, s - 1, s - 1, fill=COLORS[col])
        else:
            c.ellipse(x, y, s - 1, s - 1, fill=COLORS[col])
        shapes.append({"color": col, "shape": sh, "x": x, "y": y, "size": s,
                       "cx": x + (s - 1) / 2, "cy": y + (s - 1) / 2})
    png = c.png()
    c.close()
    return png, shapes[0]


def _ask_point(client, png, w, h, target, mode, max_tokens):
    q = (f"Find the {target['color']} {target['shape']} in this image and click its centre. "
         "Do not explain, just act.")
    if mode == "tool_calls":
        tools = [t for t in A.tool_schemas("pixels") if t["function"]["name"] == "click"]
        # Leave the coordinate system unstated on purpose: we want the model's own convention.
        tools[0]["function"]["parameters"]["properties"]["x"]["description"] = "x coordinate of the click"
        tools[0]["function"]["parameters"]["properties"]["y"]["description"] = "y coordinate of the click"
        msgs = [{"role": "user", "content": [{"type": "text", "text": q}, image_part(png)]}]
        comp = client.complete(msgs, tools=tools, max_tokens=max_tokens)
        acts = A.from_tool_calls(comp.tool_calls) or A.from_text(comp.content)
    else:
        q += ' Reply with JSON only: {"action": "click", "x": <x>, "y": <y>}'
        msgs = [{"role": "user", "content": [{"type": "text", "text": q}, image_part(png)]}]
        comp = client.complete(msgs, max_tokens=max_tokens)
        acts = A.from_text(comp.content)
    pt = next(((a.args["x"], a.args["y"]) for a in acts if a.name in ("click", "move") and not a.error), None)
    return pt, comp


def capabilities(client, max_tokens=2048):
    """Quick probes: text, image reading, tool calling, JSON-in-text."""
    from .win.screen import Canvas
    out = {}

    def timed(fn):
        t = time.time()
        try:
            v = fn()
            return {"ok": True, **v, "s": round(time.time() - t, 2)}
        except ModelError as e:
            return {"ok": False, "error": str(e)[:300], "s": round(time.time() - t, 2)}

    out["text"] = timed(lambda: {"reply": client.complete(
        [{"role": "user", "content": "Reply with exactly: pong"}], max_tokens=max_tokens).content.strip()[:40]})

    code = str(random.randint(1000, 9999))
    c = Canvas(640, 400, (255, 255, 255))
    c.text(200, 170, f"CODE {code}", 36, (0, 0, 0), bold=True)
    png = c.png()
    c.close()

    def img():
        r = client.complete([{"role": "user", "content": [
            {"type": "text", "text": "What text is written in this image? Reply with the text only."},
            image_part(png)]}], max_tokens=max_tokens)
        return {"reply": r.content.strip()[:60], "read_correctly": code in r.content}

    out["image"] = timed(img)

    def tools():
        r = client.complete([{"role": "user", "content": "Click at x=100, y=200 using the click tool."}],
                            tools=[t for t in A.tool_schemas("pixels") if t["function"]["name"] == "click"],
                            max_tokens=max_tokens)
        acts = A.from_tool_calls(r.tool_calls)
        good = bool(acts) and not acts[0].error and (acts[0].args.get("x"), acts[0].args.get("y")) == (100, 200)
        return {"native_tool_call": bool(r.tool_calls), "correct": good,
                "reasoning_returned": bool(r.reasoning)}

    out["tools"] = timed(tools)

    def jtext():
        r = client.complete([{"role": "user", "content": 'Reply with JSON only: {"action": "click", "x": 100, "y": 200}'}],
                            max_tokens=max_tokens)
        acts = A.from_text(r.content)
        return {"parsed": bool(acts) and not acts[0].error}

    out["json_text"] = timed(jtext)
    out["suggested_output"] = "tool_calls" if out["tools"].get("correct") else "json_text"
    return out


def calibrate(profile, trials=16, workers=4, seed=1234, max_tokens=None, verbose=True, save=True):
    client = ChatClient.from_profile(profile)
    rng = random.Random(seed)
    mode = profile.output
    sizes_px = [14, 20, 28, 40, 56, 80]
    jobs = []
    for i in range(trials):
        w, h = SIZES[i % len(SIZES)]
        jobs.append((i, w, h, sizes_px[i % len(sizes_px)], random.Random(rng.random())))
    caps = capabilities(client, max_tokens=max_tokens or profile.max_tokens)
    if verbose:
        print(json.dumps({"capabilities": caps}, indent=1))
    if not caps["image"].get("ok"):
        raise ModelError(f"image input failed: {caps['image'].get('error')}")

    def run(job):
        i, w, h, s, r = job
        png, target = _draw_trial(r, s, w, h)
        try:
            pt, comp = _ask_point(client, png, w, h, target, mode, max_tokens or profile.max_tokens)
        except ModelError as e:
            return {"i": i, "w": w, "h": h, "size": s, "target": target, "error": str(e)[:200]}
        return {"i": i, "w": w, "h": h, "size": s, "target": target, "answer": pt,
                "latency": round(comp.latency, 2), "tokens": [comp.prompt_tokens, comp.completion_tokens]}

    t0 = time.time()
    with cf.ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(run, jobs))
    answered = [r for r in results if r.get("answer")]
    scores = {}
    for conv in CONVENTIONS:
        errs, hits = [], 0
        for r in answered:
            ix, iy = model_to_image(r["answer"][0], r["answer"][1], conv, r["w"], r["h"])
            t = r["target"]
            d = ((ix - t["cx"]) ** 2 + (iy - t["cy"]) ** 2) ** 0.5
            errs.append(d)
            hits += d <= t["size"] / 2
        if errs:
            scores[conv] = {"median_px": round(statistics.median(errs), 2), "mean_px": round(statistics.mean(errs), 2),
                            "p90_px": round(sorted(errs)[int(0.9 * (len(errs) - 1))], 2),
                            "hit_rate": round(hits / len(errs), 3)}
    best = min(scores.items(), key=lambda kv: kv[1]["median_px"]) if scores else (None, {})
    by_size = {}
    if best[0]:
        for r in answered:
            ix, iy = model_to_image(r["answer"][0], r["answer"][1], best[0], r["w"], r["h"])
            t = r["target"]
            by_size.setdefault(t["size"], []).append(((ix - t["cx"]) ** 2 + (iy - t["cy"]) ** 2) ** 0.5)
    data = {
        "profile": profile.name, "model": profile.model, "output": mode,
        "date": datetime.now().isoformat(timespec="seconds"), "trials": trials,
        "answered": len(answered), "errors": [r for r in results if r.get("error")][:5],
        "capabilities": caps,
        "best": {"convention": best[0], **best[1]},
        "scores": scores,
        "median_error_by_target_size_px": {str(k): round(statistics.median(v), 1) for k, v in sorted(by_size.items())},
        "duration_s": round(time.time() - t0, 1),
        "trials_detail": [{k: v for k, v in r.items()} for r in results],
    }
    if save:
        data["saved_to"] = str(save_calibration(profile.name, data))
    if verbose:
        print(json.dumps({k: data[k] for k in ("best", "scores", "median_error_by_target_size_px",
                                                  "answered", "duration_s")}, indent=1))
    return data
