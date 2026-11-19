"""Offline tests: no network, no real mouse or keyboard, no screen capture."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from clickwright import actions as A
from clickwright import png
from clickwright.geometry import Frame, Rect, model_to_image, image_to_model, fit_size
from clickwright.config import Profile
from clickwright.safety import Safety, StopRun


class Geometry(unittest.TestCase):
    def test_fit_size_keeps_aspect(self):
        self.assertEqual(fit_size(1920, 1080, 1280), (1280, 720))
        self.assertEqual(fit_size(2560, 1600, 1280), (1280, 800))
        self.assertEqual(fit_size(1000, 600, 1280), (1000, 600))
        self.assertEqual(fit_size(100, 50, 400, allow_upscale=True), (400, 200))

    def test_frame_roundtrip_identity(self):
        f = Frame(Rect(0, 0, 1280, 800), 1280, 800)
        for x, y in [(0, 0), (1279, 799), (640, 400)]:
            self.assertEqual(f.image_to_screen(x, y), (x, y))

    def test_frame_scaled_and_offset(self):
        # second monitor left of primary at 150%: 2880x1620 physical shown as 1280x720
        f = Frame(Rect(-2880, 0, 2880, 1620), 1280, 720)
        self.assertEqual(f.image_to_screen(0, 0)[0], -2880 + 1)  # centre of first image px covers 2.25 px
        sx, sy = f.image_to_screen(640, 360)
        self.assertAlmostEqual(sx, -2880 + 1441, delta=1)
        self.assertAlmostEqual(sy, 811, delta=1)
        ix, iy = f.screen_to_image(sx, sy)
        self.assertAlmostEqual(ix, 640, delta=0.5)
        self.assertAlmostEqual(iy, 360, delta=0.5)

    def test_conventions(self):
        self.assertEqual(model_to_image(500, 500, "norm1000", 1280, 800), (640.0, 400.0))
        self.assertEqual(model_to_image(0.5, 0.25, "norm1", 1280, 800), (640.0, 200.0))
        self.assertEqual(model_to_image(999, 999, "norm999", 1280, 800), (1279.0, 799.0))
        for conv in ("pixels", "norm1000", "norm999", "norm1"):
            mx, my = image_to_model(320, 200, conv, 1280, 800)
            ix, iy = model_to_image(mx, my, conv, 1280, 800)
            self.assertAlmostEqual(ix, 320, delta=1.5)
            self.assertAlmostEqual(iy, 200, delta=1.5)


class Png(unittest.TestCase):
    def test_sub_filter_roundtrip(self):
        import random
        rnd = random.Random(7)
        w, h = 33, 9
        rgb = bytes(rnd.getrandbits(8) for _ in range(w * h * 3))
        old = png.TRY_FILTER_ABOVE
        try:
            png.TRY_FILTER_ABOVE = 0   # force the filtered attempt
            data = png.encode_rgb(rgb, w, h)
        finally:
            png.TRY_FILTER_ABOVE = old
        self.assertEqual(png.decode(data)[3], rgb)
        filtered = png._sub_filtered(rgb, w * 3, h)
        self.assertEqual(filtered[0], 1)
        # byte 3 of row 0 is rgb[3] - rgb[0] mod 256
        self.assertEqual(filtered[1 + 3], (rgb[3] - rgb[0]) % 256)

    def test_roundtrip(self):
        w, h = 7, 5
        bgra = bytearray()
        for y in range(h):
            for x in range(w):
                bgra += bytes([x * 30 % 256, y * 50 % 256, (x + y) * 20 % 256, 255])
        data = png.encode_bgra(bytes(bgra), w, h)
        self.assertEqual(png.size_of(data), (w, h))
        W, H, ch, px = png.decode(data)
        self.assertEqual((W, H, ch), (w, h, 3))
        # pixel (3, 2): B=90 G=100 R=100
        i = (2 * w + 3) * 3
        self.assertEqual(tuple(px[i:i + 3]), (100, 100, 90))


class Parsing(unittest.TestCase):
    def test_tool_calls_and_aliases(self):
        tcs = [
            {"id": "a", "function": {"name": "click", "arguments": '{"x": 10, "y": "20"}'}},
            {"id": "b", "function": {"name": "left_click", "arguments": '{"coordinate": [5, 6]}'}},
            {"id": "c", "function": {"name": "double_click", "arguments": '{"x": 1, "y": 2}'}},
            {"id": "d", "function": {"name": "computer", "arguments": '{"action": "key", "text": "ctrl+s"}'}},
            {"id": "e", "function": {"name": "left_click_drag",
                                     "arguments": '{"start_coordinate": [1, 2], "coordinate": [3, 4]}'}},
            {"id": "f", "function": {"name": "click", "arguments": '{"y": 3}'}},
            {"id": "g", "function": {"name": "answer", "arguments": '{"text": "done", "success": "false"}'}},
        ]
        acts = A.from_tool_calls(tcs)
        self.assertEqual((acts[0].name, acts[0].args["x"], acts[0].args["y"]), ("click", 10, 20))
        self.assertEqual((acts[1].args["x"], acts[1].args["y"]), (5, 6))
        self.assertEqual(acts[2].args["clicks"], 2)
        self.assertEqual((acts[3].name, acts[3].args["keys"]), ("key", "ctrl+s"))
        self.assertEqual(acts[4].args, {"x1": 1, "y1": 2, "x2": 3, "y2": 4})
        self.assertIsNotNone(acts[5].error)
        self.assertEqual((acts[6].name, acts[6].args["success"]), ("answer", False))

    def test_json_text(self):
        t = 'I will click the field.\n```json\n{"actions": [{"action": "click", "x": 412, "y": 230}, ' \
            '{"action": "type", "text": "hi {there}"}]}\n```'
        acts = A.from_text(t)
        self.assertEqual([a.name for a in acts], ["click", "type"])
        self.assertEqual(acts[1].args["text"], "hi {there}")

    def test_function_style(self):
        acts = A.from_text("I'll click (the blue one) now: click(x=100, y=200) then type('abc')")
        self.assertEqual([(a.name, a.args.get("x"), a.args.get("text")) for a in acts],
                         [("click", 100, None), ("type", None, "abc")])

    def test_prose_only(self):
        self.assertEqual(A.from_text("I think the task is done because the page loaded."), [])

    def test_schemas_valid_json(self):
        for conv in ("pixels", "norm1000", "norm1"):
            json.dumps(A.tool_schemas(conv))


class SafetyTests(unittest.TestCase):
    def test_deny_and_allow(self):
        s = Safety(allow_apps=["notepad"], corner_failsafe=False)
        self.assertIsNone(s.check_target("click", {"process": "notepad.exe", "title": "x"}))
        self.assertIn("not in the allowed", s.check_target("click", {"process": "chrome.exe", "title": "x"}))
        self.assertIn("deny", s.check_target("click", {"process": "keepassxc.exe", "title": "x"}))
        self.assertIn("password", s.check_typing(True))
        self.assertIn("private", s.check_foreground({"process": "notepad.exe", "title": ".env - Notepad"}))
        self.assertEqual(s.check_foreground({"process": "notepad.exe", "title": "environment notes - Notepad"}), "")
        self.assertTrue(s.is_protected({"process": "notepad.exe", "title": "C:/x/.env - Notepad"}))

    def test_corner(self):
        s = Safety()
        s.set_primary_corner(0, 0)
        s.check_kill((500, 500))
        with self.assertRaises(StopRun):
            s.check_kill((0, 0))
        s.note_agent_cursor((0, 0))
        s.check_kill((0, 0))  # the agent put it there itself


class FakeClient:
    """Scripted model: returns queued replies and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.total = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cost_usd": 0.0,
                      "calls": 0, "latency": 0.0}

    def complete(self, messages, tools=None, **kw):
        from clickwright.llm import Completion
        self.sent.append(json.loads(json.dumps(messages)))
        self.total["calls"] += 1
        content, calls = self.replies.pop(0)
        tcs = [{"id": f"call{i}", "type": "function",
                "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
        return Completion(content, tcs, "", "tool_calls" if tcs else "stop", {}, 0.01, 1)


def image_part_for(driver):
    from clickwright.llm import image_part
    return image_part(driver.observe(1280).png)


class AgentLoop(unittest.TestCase):
    def _profile(self, **kw):
        return Profile(name="test", model="fake", coords=kw.pop("coords", "pixels"), keep_screenshots=2,
                       evict_batch=1, **kw)

    def test_form_in_simulator(self):
        from clickwright.agent import Agent
        from clickwright.sim import SimDriver
        d = SimDriver("form", seed=3)
        sc = d.scene
        # Image is 1280x800 for the 1920x1200 sim screen: divide screen coords by 1.5.
        def c(w):
            r = w.rect
            return {"x": round((r.left + r.width / 2) / 1.5), "y": round((r.top + r.height / 2) / 1.5)}
        replies = [
            ("", [("click", c(sc.name)), ("type", {"text": "Ada Lovelace"})]),
            ("", [("click", c(sc.email)), ("type", {"text": "ada@example.com"})]),
            ("", [("click", {"x": round((sc.agree.rect.left + 12) / 1.5), "y": round((sc.agree.rect.top + 12) / 1.5)})]),
            ("", [("click", c(sc.submit))]),
            ("", [("answer", {"text": f"The code is {sc.code}"})]),
        ]
        client = FakeClient(replies)
        agent = Agent(self._profile(), d, client, max_steps=10, log=False, verbose=False)
        res = agent.run(sc.task)
        self.assertEqual(res.status, "done")
        ok, details = sc.check(res.answer)
        self.assertTrue(ok, details)
        # tool results must follow each assistant tool call
        last = client.sent[-1]
        roles = [m["role"] for m in last]
        self.assertIn("tool", roles)
        for i, m in enumerate(last):
            if m["role"] == "assistant" and m.get("tool_calls"):
                ids = [tc["id"] for tc in m["tool_calls"]]
                following = [x["tool_call_id"] for x in last[i + 1:i + 1 + len(ids)]]
                self.assertEqual(ids, following)
        # eviction: never more than keep + batch - 1 = 2 live images
        live = sum(1 for m in last if isinstance(m["content"], list)
                   for p in m["content"] if p.get("type") == "image_url")
        self.assertLessEqual(live, 2)

    def test_request_size_limit_and_413_retry(self):
        from clickwright.agent import Agent
        from clickwright.llm import ModelError
        from clickwright.sim import SimDriver

        class TooBigOnce(FakeClient):
            def __init__(self, replies):
                super().__init__(replies)
                self.failed = False

            def complete(self, messages, tools=None, **kw):
                if len(self.sent) == 2 and not self.failed:
                    self.failed = True
                    raise ModelError("HTTP 413: too large", 413)
                return super().complete(messages, tools, **kw)

        def live(msgs):
            return sum(1 for m in msgs if isinstance(m["content"], list)
                       for p in m["content"] if p.get("type") == "image_url")

        d = SimDriver("targets", seed=1)
        replies = [("", [("screenshot", {})])] * 4 + [("", [("answer", {"text": "done"})])]
        # keep_screenshots would allow 2 live images, but the size budget only fits one.
        one_image = len(json.dumps(image_part_for(d)))
        prof = self._profile(max_request_mb=(one_image * 1.6) / 1_000_000)
        client = TooBigOnce(replies)
        res = Agent(prof, d, client, max_steps=10, log=False, verbose=False).run("look")
        self.assertEqual(res.status, "done")
        self.assertTrue(client.failed)
        for msgs in client.sent:
            self.assertEqual(live(msgs), 1)

    def test_norm1000_and_errors(self):
        from clickwright.agent import Agent
        from clickwright.sim import SimDriver
        d = SimDriver("targets", seed=1)
        sc = d.scene
        replies = []
        for w in sorted(sc.widgets, key=lambda w: w.state["n"]):
            r = w.rect
            replies.append(("", [("click", {"x": round((r.left + r.width / 2) / 1920 * 1000),
                                            "y": round((r.top + r.height / 2) / 1200 * 1000)})]))
        replies.insert(1, ("", [("click", {"x": 5000, "y": 10})]))   # outside -> error, run continues
        replies.insert(2, ("just thinking", []))                      # no action -> nudge
        replies.append(("", [("answer", {"text": "done"})]))
        agent = Agent(self._profile(coords="norm1000"), d, FakeClient(replies), max_steps=20, log=False,
                      verbose=False)
        res = agent.run(sc.task)
        ok, details = sc.check(res.answer)
        self.assertEqual(res.status, "done")
        self.assertGreaterEqual(details["hit"], 5, details)  # smallest target may be missed by rounding

    def test_json_text_mode(self):
        from clickwright.agent import Agent
        from clickwright.sim import SimDriver
        d = SimDriver("menu", seed=2)
        sc = d.scene

        def at(w):
            r = w.rect
            return round((r.left + r.width / 2) / 1.5), round((r.top + r.height / 2) / 1.5)
        export = next(i for i in sc.items if i.label.startswith("Export"))
        steps = [[("click",) + at(sc.menu_file)], [("click",) + at(export)],
                 [("click",) + at(sc.radio_pdf)], [("click",) + at(sc.btn_export)]]
        replies = [(json.dumps({"actions": [{"action": a, "x": x, "y": y} for a, x, y in s]}), []) for s in steps]
        replies.append(('{"action": "answer", "text": "%s"}' % sc.fname, []))
        agent = Agent(self._profile(output="json_text"), d, FakeClient(replies), max_steps=10, log=False,
                      verbose=False)
        res = agent.run(sc.task)
        self.assertTrue(sc.check(res.answer)[0], sc.check(res.answer)[1])


class MockServer(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "m", "pricing": {"input_usd_per_1m": 1, "output_usd_per_1m": 2}}]})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self):
        MockServer.calls += 1
        n = int(self.headers["Content-Length"])
        json.loads(self.rfile.read(n))
        if MockServer.calls == 1:  # first call: transient error to exercise retries
            self.send_response(503)
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "busy"}}')
            return
        assert self.headers["Authorization"] == "Bearer k"
        body = {"choices": [{"message": {"content": "ok", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "click", "arguments": '{"x":1,"y":2}'}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


class Client(unittest.TestCase):
    def test_retry_and_cost(self):
        from clickwright.llm import ChatClient
        srv = HTTPServer(("127.0.0.1", 0), MockServer)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = ChatClient(f"http://127.0.0.1:{srv.server_port}/v1", "k", "m")
            r = c.complete([{"role": "user", "content": "hi"}])
            self.assertEqual(r.attempts, 2)
            self.assertEqual(r.tool_calls[0]["function"]["name"], "click")
            self.assertAlmostEqual(r.cost, (1000 * 1 + 500 * 2) / 1e6)
        finally:
            srv.shutdown()


class Keys(unittest.TestCase):
    def test_parse(self):
        from clickwright.win.keys import parse_combo
        self.assertEqual(parse_combo("ctrl+s"), [0x11, ord("S")])
        self.assertEqual(parse_combo("Control_L+Shift+t"), [0xA2, 0x10, ord("T")])
        self.assertEqual(parse_combo("Return"), [0x0D])
        self.assertIn(0x11, parse_combo("ctrl++"))  # "+" may add shift on this layout
        self.assertEqual(parse_combo("alt f4"), [0x12, 0x73])


if __name__ == "__main__":
    unittest.main()


class Reports(unittest.TestCase):
    def test_run_log_and_html(self):
        import tempfile
        from pathlib import Path
        from clickwright.agent import Agent
        from clickwright.sim import SimDriver
        d = SimDriver("slider", seed=0)
        sc = d.scene
        tx = round(sc._thumb_x() / 1.5)
        ty = round((sc.track.top + 4) / 1.5)
        x2 = round((sc.track.left + sc.track.width * sc.target / 100) / 1.5)
        replies = [("dragging", [("drag", {"x1": tx, "y1": ty, "x2": x2, "y2": ty}),
                                 ("zoom", {"x1": 300, "y1": 330, "x2": 900, "y2": 450})]),
                   ("", [("answer", {"text": f"volume is {sc.target} → done"})])]
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(Profile(name="t", model="fake", coords="pixels"), d, FakeClient(replies),
                          max_steps=5, runs_dir=tmp, verbose=False)
            res = agent.run(sc.task)
            self.assertEqual(res.status, "done", res.reason)
            run = Path(res.run_dir)
            html = (run / "report.html").read_text(encoding="utf-8")
            self.assertIn("step_001.png", html)
            self.assertIn("zoom", html)
            self.assertTrue((run / "step_001_zoom1.png").exists())
            self.assertFalse((run / "report-error.txt").exists())
            ok, details = sc.check(res.answer)
            self.assertTrue(ok, details)


class Redaction(unittest.TestCase):
    def test_subtract_and_stack(self):
        from clickwright.geometry import subtract, redaction_rects
        r = Rect(0, 0, 100, 100)
        parts = subtract(r, [Rect(25, 25, 50, 50)])
        self.assertEqual(sum(p.width * p.height for p in parts), 100 * 100 - 50 * 50)
        self.assertEqual(subtract(r, [Rect(-10, -10, 200, 200)]), [])
        stack = [  # front to back
            {"rect": Rect(0, 0, 2880, 1800), "process": "overlay.exe", "occluder": False},
            {"rect": Rect(100, 100, 800, 600), "process": "notepad.exe", "occluder": True},
            {"rect": Rect(0, 0, 2880, 1800), "process": "chrome.exe", "occluder": True},
        ]
        rects = redaction_rects(stack, lambda w: w["process"] == "chrome.exe")
        area = sum(p.width * p.height for p in rects)
        self.assertEqual(area, 2880 * 1800 - 800 * 600)  # overlay does not hide chrome; notepad does
        for p in rects:
            self.assertIsNone(__import__("clickwright.geometry", fromlist=["intersect"]).intersect(
                p, Rect(100, 100, 800, 600)))


class NavigationKeys(unittest.TestCase):
    def test_nav(self):
        nav = Safety.is_navigation_key
        for k in ("win", "cmd", "win+r", "win+d", "alt+tab", "alt+shift+tab", "ctrl+esc", "alt+esc", "super"):
            self.assertTrue(nav(k), k)
        for k in ("win+l", "ctrl+s", "enter", "esc", "alt+f4", "ctrl+alt+tab", "a"):
            self.assertFalse(nav(k), k)
