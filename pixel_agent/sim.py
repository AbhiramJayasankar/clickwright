"""Offline simulator: a fake 1920x1200 desktop drawn with GDI.

It lets the whole loop (model, parsing, coordinate conversion, scaling,
history, logging) run against a real model without touching the real mouse,
keyboard or screen. Scenes check themselves, so they double as eval tasks.
"""
import random
import time

from .driver import Driver, Observation
from .geometry import Rect, Frame, fit_size
from . import png as pngmod

SCREEN_W, SCREEN_H = 1920, 1200
UI_BG = (243, 243, 243)
INK = (32, 32, 32)
MUTED = (110, 110, 110)
ACCENT = (0, 95, 184)
BORDER = (160, 160, 160)


class Widget:
    def __init__(self, kind, x, y, w, h, label="", **state):
        self.kind, self.rect, self.label = kind, Rect(int(x), int(y), int(w), int(h)), label
        self.state = state
        self.visible = True

    def hit(self, x, y):
        return self.visible and self.rect.contains(x, y)

    @property
    def role(self):
        return {"button": "button", "field": "edit", "checkbox": "checkbox", "item": "listitem",
                "menu": "menuitem", "radio": "radio", "slider": "slider", "label": "text",
                "target": "button"}.get(self.kind, "custom")


class Scene:
    title = "Simulator"
    task = ""

    def __init__(self, seed=0):
        self.rng = random.Random(seed)
        self.widgets = []
        self.focus = None
        self.events = []
        self.select_all = False

    # events --------------------------------------------------------------
    def widget_at(self, x, y):
        for w in reversed(self.widgets):
            if w.hit(x, y):
                return w
        return None

    def on_click(self, x, y, button, clicks):
        w = self.widget_at(x, y)
        self.events.append(("click", x, y, w.label if w else None))
        if w is None:
            return
        if w.kind == "field":
            self.focus = w
            self.select_all = clicks >= 2
        elif w.kind == "checkbox":
            w.state["checked"] = not w.state.get("checked", False)
        self.activate(w)

    def activate(self, w):
        pass

    def on_type(self, text):
        if self.focus is not None and self.focus.kind == "field":
            if self.select_all:
                self.focus.state["value"] = ""
                self.select_all = False
            for ch in text:
                if ch == "\n":
                    self.on_enter()
                else:
                    self.focus.state["value"] = self.focus.state.get("value", "") + ch

    def on_key(self, keys):
        k = keys.lower().replace(" ", "")
        if k in ("ctrl+a", "control+a", "control_l+a"):
            self.select_all = True
        elif k in ("backspace", "back_space", "delete"):
            if self.focus is not None and self.focus.kind == "field":
                if self.select_all:
                    self.focus.state["value"] = ""
                    self.select_all = False
                else:
                    self.focus.state["value"] = self.focus.state.get("value", "")[:-1]
        elif k in ("enter", "return"):
            self.on_enter()
        elif k == "tab":
            fields = [w for w in self.widgets if w.kind == "field" and w.visible]
            if fields:
                i = fields.index(self.focus) + 1 if self.focus in fields else 0
                self.focus = fields[i % len(fields)]

    def on_enter(self):
        pass

    def on_scroll(self, x, y, direction, amount):
        pass

    def on_drag(self, x1, y1, x2, y2):
        pass

    # drawing --------------------------------------------------------------
    def draw_window(self, c, rect, title):
        c.rect(rect.left + 6, rect.top + 6, rect.width, rect.height, fill=(200, 200, 200))
        c.rect(rect.left, rect.top, rect.width, rect.height, fill=(255, 255, 255), outline=BORDER)
        c.rect(rect.left, rect.top, rect.width, 44, fill=(230, 230, 230))
        c.text(rect.left + 16, rect.top + 11, title, 19, INK)
        c.text(rect.right - 40, rect.top + 8, "✕", 20, MUTED)

    def draw_widget(self, c, w):
        r = w.rect
        if w.kind in ("button", "target"):
            primary = w.state.get("primary")
            c.rect(r.left, r.top, r.width, r.height, fill=ACCENT if primary else (250, 250, 250), outline=BORDER)
            tw, th = c.text_size(w.label, 18)
            c.text(r.left + (r.width - tw) // 2, r.top + (r.height - th) // 2, w.label, 18,
                   (255, 255, 255) if primary else INK)
        elif w.kind == "field":
            focused = self.focus is w
            c.rect(r.left, r.top, r.width, r.height, fill=(255, 255, 255), outline=ACCENT if focused else BORDER,
                   width=2 if focused else 1)
            c.text(r.left, r.top - 26, w.label, 17, MUTED)
            val = w.state.get("value", "")
            if self.select_all and focused and val:
                tw, _ = c.text_size(val, 19)
                c.rect(r.left + 8, r.top + 8, tw, r.height - 16, fill=(173, 214, 255))
            c.text(r.left + 8, r.top + (r.height - 24) // 2, val, 19, INK)
            if focused:
                tw, _ = c.text_size(val, 19) if val else (0, 0)
                c.line(r.left + 9 + tw, r.top + 8, r.left + 9 + tw, r.bottom - 8, INK, 1)
        elif w.kind == "checkbox":
            c.rect(r.left, r.top, 24, 24, fill=(255, 255, 255), outline=INK)
            if w.state.get("checked"):
                c.line(r.left + 5, r.top + 12, r.left + 10, r.top + 18, INK, 3)
                c.line(r.left + 10, r.top + 18, r.left + 20, r.top + 5, INK, 3)
            c.text(r.left + 34, r.top, w.label, 18, INK)
        elif w.kind == "label":
            c.text(r.left, r.top, w.label, w.state.get("size", 18), w.state.get("color", INK),
                   bold=w.state.get("bold", False))

    def draw(self, c):
        for w in self.widgets:
            if w.visible:
                self.draw_widget(c, w)

    def check(self, answer):
        """(passed, details)."""
        raise NotImplementedError


class FormScene(Scene):
    title = "Sign up"

    def __init__(self, seed=0):
        super().__init__(seed)
        r = self.rng
        self.code = str(r.randint(1000, 9999))
        self.win = Rect(r.randint(200, 900), r.randint(120, 380), 640, 520)
        x, y = self.win.left + 40, self.win.top + 100
        self.name = Widget("field", x, y, 560, 48, "Full name", value="")
        self.email = Widget("field", x, y + 100, 560, 48, "Email address", value="")
        self.agree = Widget("checkbox", x, y + 180, 400, 26, "I agree to the terms", checked=False)
        self.cancel = Widget("button", x + 300, y + 260, 120, 44, "Cancel")
        self.submit = Widget("button", x + 440, y + 260, 120, 44, "Submit", primary=True)
        self.msg = Widget("label", x, y + 330, 560, 30, "", size=19)
        self.msg.visible = False
        self.widgets = [self.name, self.email, self.agree, self.cancel, self.submit, self.msg]
        self.submitted = False
        self.task = ("Fill in the sign-up form with the name 'Ada Lovelace' and the email 'ada@example.com', "
                     "accept the terms, submit it, and answer with the confirmation code that appears.")

    def _try_submit(self):
        ok = (self.name.state["value"].strip() == "Ada Lovelace"
              and self.email.state["value"].strip() == "ada@example.com" and self.agree.state["checked"])
        self.msg.visible = True
        if ok:
            self.submitted = True
            self.msg.label = f"Thank you! Your confirmation code is {self.code}."
            self.msg.state["color"] = (16, 124, 16)
        else:
            self.msg.label = "Please enter the name and email exactly, and accept the terms."
            self.msg.state["color"] = (196, 43, 28)

    def activate(self, w):
        if w is self.submit:
            self._try_submit()

    def on_enter(self):
        self._try_submit()

    def draw(self, c):
        self.draw_window(c, self.win, "Create your account")
        super().draw(c)

    def check(self, answer):
        ok = self.submitted and self.code in (answer or "")
        return ok, {"submitted": self.submitted, "code": self.code, "name": self.name.state["value"],
                    "email": self.email.state["value"], "agreed": self.agree.state["checked"]}


class TargetsScene(Scene):
    """Numbered circles of shrinking size: measures aim in the loop."""
    title = "Targets"

    def __init__(self, seed=0):
        super().__init__(seed)
        r = self.rng
        self.next = 1
        self.misses = []
        sizes = [60, 44, 32, 24, 18, 14]
        placed = []
        for i, s in enumerate(sizes, 1):
            for _ in range(200):
                x, y = r.randint(80, SCREEN_W - 80 - s), r.randint(120, SCREEN_H - 80 - s)
                if all(abs(x - px) > 140 or abs(y - py) > 140 for px, py in placed):
                    break
            placed.append((x, y))
            self.widgets.append(Widget("target", x, y, s, s, str(i), n=i, done=False))
        self.task = ("Click the numbered circles in order, from 1 to 6. A circle turns green when it was hit. "
                     "When all six are green, answer 'done'.")

    def on_click(self, x, y, button, clicks):
        self.events.append(("click", x, y))
        want = next((w for w in self.widgets if w.state["n"] == self.next), None)
        if want is None:
            return
        cx, cy = want.rect.left + want.rect.width / 2, want.rect.top + want.rect.height / 2
        dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
        if dist <= want.rect.width / 2 + 1:
            want.state["done"] = True
            want.state["dist"] = dist
            self.next += 1
        else:
            self.misses.append({"target": self.next, "dist_px": round(dist, 1), "radius": want.rect.width / 2})

    def draw(self, c):
        c.text(60, 40, "Click the circles in order: 1 → 6", 26, INK, bold=True)
        for w in self.widgets:
            r = w.rect
            done = w.state["done"]
            c.ellipse(r.left, r.top, r.width, r.height, fill=(52, 168, 83) if done else (234, 67, 53))
            fs = max(11, int(r.width * 0.55))
            tw, th = c.text_size(w.label, fs, bold=True)
            c.text(r.left + (r.width - tw) // 2, r.top + (r.height - th) // 2, w.label, fs, (255, 255, 255), bold=True)

    def check(self, answer):
        hits = [round(w.state.get("dist", -1), 1) for w in self.widgets]
        return self.next == 7, {"hit": self.next - 1, "misses": self.misses, "hit_dist_px": hits}


class ScrollScene(Scene):
    title = "Inventory"

    def __init__(self, seed=0):
        super().__init__(seed)
        r = self.rng
        self.items = [(f"Item {i}", r.randint(100, 999)) for i in range(1, 61)]
        self.want = r.randint(35, 58)
        self.box = Rect(560, 200, 800, 12 * 60)
        self.offset = 0
        self.selected = None
        self.task = (f"In the inventory list, find 'Item {self.want}', click it to select it, and answer with the "
                     f"stock number shown next to it.")

    def _rows(self):
        out = []
        for i, (name, val) in enumerate(self.items):
            y = self.box.top + i * 60 - self.offset
            if self.box.top - 60 < y < self.box.bottom:
                out.append((i, name, val, y))
        return out

    def on_scroll(self, x, y, direction, amount):
        if self.box.contains(x, y) and direction in ("up", "down"):
            step = 3 * 30 * amount  # ~3 rows of 30px per notch like a real list
            self.offset += step if direction == "down" else -step
            self.offset = max(0, min(self.offset, len(self.items) * 60 - self.box.height))
        self.events.append(("scroll", direction, amount, self.offset))

    def on_click(self, x, y, button, clicks):
        self.events.append(("click", x, y))
        if not self.box.contains(x, y):
            return
        for i, name, val, ry in self._rows():
            if ry <= y < ry + 60 and ry >= self.box.top - 1 and ry + 60 <= self.box.bottom + 1:
                self.selected = i + 1

    def draw(self, c):
        self.draw_window(c, Rect(self.box.left - 40, self.box.top - 110, self.box.width + 80, self.box.height + 150),
                         "Inventory")
        c.text(self.box.left, self.box.top - 50, "Scroll the list with the mouse wheel.", 18, MUTED)
        for i, name, val, y in self._rows():
            if y < self.box.top or y + 60 > self.box.bottom:
                continue  # rows partly outside are clipped
            sel = self.selected == i + 1
            c.rect(self.box.left, y, self.box.width, 59, fill=(204, 228, 247) if sel else (255, 255, 255))
            c.line(self.box.left, y + 59, self.box.right, y + 59, (225, 225, 225))
            c.text(self.box.left + 20, y + 17, name, 20, INK)
            c.text(self.box.right - 200, y + 17, f"stock {val}", 20, MUTED)
        c.rect(self.box.left, self.box.top, self.box.width, self.box.height, outline=BORDER)

    def widgets_for_hints(self):
        out = []
        for i, name, val, y in self._rows():
            if y >= self.box.top and y + 60 <= self.box.bottom:
                out.append(Widget("item", self.box.left, y, self.box.width, 59, name))
        return out

    def check(self, answer):
        val = str(self.items[self.want - 1][1])
        ok = self.selected == self.want and val in (answer or "")
        return ok, {"want": self.want, "selected": self.selected, "value": val, "offset": self.offset}


class SliderScene(Scene):
    title = "Sound settings"

    def __init__(self, seed=0):
        super().__init__(seed)
        r = self.rng
        self.target = r.choice([30, 40, 60, 70, 80])
        self.value = r.choice([v for v in (10, 20, 50, 90) if abs(v - self.target) > 15])
        self.track = Rect(560, 600, 800, 8)
        self.task = (f"Set the Volume slider to {self.target} (within 3) by dragging its handle, then answer with the "
                     f"value it shows.")

    def _thumb_x(self):
        return self.track.left + self.track.width * self.value / 100

    def on_drag(self, x1, y1, x2, y2):
        tx = self._thumb_x()
        self.events.append(("drag", x1, y1, x2, y2))
        if abs(x1 - tx) <= 22 and abs(y1 - (self.track.top + 4)) <= 22:
            self.value = round(max(0, min(100, (x2 - self.track.left) / self.track.width * 100)))

    def on_click(self, x, y, button, clicks):
        self.events.append(("click", x, y))
        if self.track.left <= x <= self.track.right and abs(y - (self.track.top + 4)) <= 14:
            # Clicking the track jumps 10 towards the click, like many sliders.
            v = (x - self.track.left) / self.track.width * 100
            self.value = max(0, min(100, self.value + (10 if v > self.value else -10)))

    def draw(self, c):
        self.draw_window(c, Rect(460, 420, 1000, 360), "Sound")
        c.text(560, 520, "Volume", 22, INK, bold=True)
        c.text(1290, 520, str(self.value), 22, INK, bold=True)
        c.rect(self.track.left, self.track.top, self.track.width, self.track.height, fill=(200, 200, 200))
        tx = self._thumb_x()
        c.rect(self.track.left, self.track.top, int(tx - self.track.left), self.track.height, fill=ACCENT)
        c.ellipse(int(tx) - 14, self.track.top - 10, 28, 28, fill=ACCENT, outline=(255, 255, 255), width=3)
        c.text(self.track.left, 640, "0", 16, MUTED)
        c.text(self.track.right - 24, 640, "100", 16, MUTED)

    def widgets_for_hints(self):
        tx = self._thumb_x()
        return [Widget("slider", self.track.left, self.track.top - 10, self.track.width, 28, f"Volume {self.value}"),
                Widget("button", int(tx) - 14, self.track.top - 10, 28, 28, "Volume handle")]

    def check(self, answer):
        ok = abs(self.value - self.target) <= 3 and str(self.value) in (answer or "")
        return ok, {"target": self.target, "value": self.value}


class MenuScene(Scene):
    title = "Notes - report.txt"

    def __init__(self, seed=0):
        super().__init__(seed)
        r = self.rng
        self.fname = f"report-{r.randint(100, 999)}.pdf"
        self.stage = "closed"  # closed | menu | dialog | done
        self.format = "PNG"
        self.exported = None
        self.menu_file = Widget("menu", 20, 52, 60, 32, "File")
        self.menu_edit = Widget("menu", 84, 52, 60, 32, "Edit")
        self.menu_view = Widget("menu", 148, 52, 64, 32, "View")
        self.items = [Widget("menu", 20, 86 + i * 36, 220, 36, t) for i, t in
                      enumerate(["New", "Open…", "Save", "Export…", "Print…", "Exit"])]
        self.radio_png = Widget("radio", 780, 520, 120, 28, "PNG")
        self.radio_pdf = Widget("radio", 940, 520, 120, 28, "PDF")
        self.btn_export = Widget("button", 1020, 620, 140, 44, "Export", primary=True)
        self.btn_cancel = Widget("button", 860, 620, 140, 44, "Cancel")
        self.task = ("Export this document as a PDF using the File menu (choose PDF in the export dialog), then "
                     "answer with the file name shown in the confirmation message.")

    def _visible(self):
        ws = [self.menu_file, self.menu_edit, self.menu_view]
        if self.stage == "menu":
            ws += self.items
        if self.stage == "dialog":
            ws += [self.radio_png, self.radio_pdf, self.btn_export, self.btn_cancel]
        return ws

    def widgets_for_hints(self):
        return self._visible()

    def on_click(self, x, y, button, clicks):
        hit = next((w for w in reversed(self._visible()) if w.rect.contains(x, y)), None)
        self.events.append(("click", x, y, hit.label if hit else None))
        if self.stage == "dialog":
            if hit is self.radio_png:
                self.format = "PNG"
            elif hit is self.radio_pdf:
                self.format = "PDF"
            elif hit is self.btn_cancel:
                self.stage = "closed"
            elif hit is self.btn_export:
                self.exported = self.format
                self.stage = "done"
            return
        if hit is self.menu_file:
            self.stage = "closed" if self.stage == "menu" else "menu"
        elif self.stage == "menu" and hit is not None and hit.label == "Export…":
            self.stage = "dialog"
        else:
            self.stage = "closed" if self.stage == "menu" else self.stage

    def on_key(self, keys):
        if keys.lower() in ("esc", "escape"):
            self.stage = "closed" if self.stage in ("menu", "dialog") else self.stage

    def draw(self, c):
        c.rect(0, 0, SCREEN_W, 44, fill=(230, 230, 230))
        c.text(16, 11, "Notes — report.txt", 19, INK)
        c.rect(0, 44, SCREEN_W, 48, fill=(250, 250, 250))
        for m in (self.menu_file, self.menu_edit, self.menu_view):
            if m is self.menu_file and self.stage == "menu":
                c.rect(m.rect.left, m.rect.top, m.rect.width, m.rect.height, fill=(220, 220, 220))
            c.text(m.rect.left + 10, m.rect.top + 5, m.label, 18, INK)
        c.rect(0, 92, SCREEN_W, SCREEN_H - 92, fill=(255, 255, 255))
        for i, line in enumerate(["Quarterly report", "", "Revenue grew in every region this quarter.",
                                  "Costs were flat. See the attached tables for details."]):
            c.text(40, 130 + i * 34, line, 20 if i else 26, INK, bold=(i == 0))
        if self.stage == "menu":
            c.rect(18, 84, 226, 6 * 36 + 6, fill=(252, 252, 252), outline=BORDER)
            for it in self.items:
                c.text(it.rect.left + 14, it.rect.top + 7, it.label, 18, INK)
        if self.stage in ("dialog",):
            c.rect(0, 0, SCREEN_W, SCREEN_H, fill=None)
            self.draw_window(c, Rect(700, 400, 520, 300), "Export as")
            c.text(740, 470, "Format:", 18, INK)
            for rw in (self.radio_png, self.radio_pdf):
                sel = self.format == rw.label
                c.ellipse(rw.rect.left, rw.rect.top + 2, 22, 22, fill=(255, 255, 255), outline=INK)
                if sel:
                    c.ellipse(rw.rect.left + 6, rw.rect.top + 8, 10, 10, fill=INK)
                c.text(rw.rect.left + 32, rw.rect.top, rw.label, 18, INK)
            self.draw_widget(c, self.btn_cancel)
            self.draw_widget(c, self.btn_export)
        if self.stage == "done":
            ext = self.exported.lower()
            name = self.fname if ext == "pdf" else self.fname.replace(".pdf", "." + ext)
            c.rect(660, 1060, 600, 60, fill=(32, 32, 32))
            c.text(690, 1076, f"Exported as {name}", 20, (255, 255, 255))

    def check(self, answer):
        ok = self.exported == "PDF" and self.fname in (answer or "")
        return ok, {"exported": self.exported, "fname": self.fname}


SCENES = {"form": FormScene, "targets": TargetsScene, "scroll": ScrollScene, "slider": SliderScene,
          "menu": MenuScene}


class SimDriver(Driver):
    name = "sim"

    def __init__(self, scene="form", seed=0):
        self.scene = SCENES[scene](seed) if isinstance(scene, str) else scene
        self.target = f"sim:{type(self.scene).__name__}"
        self._cursor = (SCREEN_W // 2, SCREEN_H // 2)
        self.inputs = []

    def _render(self):
        from .win.screen import Canvas
        c = Canvas(SCREEN_W, SCREEN_H, UI_BG)
        self.scene.draw(c)
        x, y = self._cursor
        pts = [(0, 0), (0, 26), (7, 20), (12, 31), (16, 29), (11, 18), (19, 18), (0, 0)]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            c.line(x + ax, y + ay, x + bx, y + by, (0, 0, 0), 3)
        return c

    def observe(self, max_long_edge, with_elements=False, max_elements=120):
        t0 = time.time()
        c = self._render()
        w, h = fit_size(SCREEN_W, SCREEN_H, max_long_edge)
        try:
            bgra = c.scaled_bgra(w, h) if (w, h) != (SCREEN_W, SCREEN_H) else c.bgra()
        finally:
            c.close()
        elements = []
        if with_elements:
            ws = self.scene.widgets_for_hints() if hasattr(self.scene, "widgets_for_hints") else \
                [x for x in self.scene.widgets if x.visible]
            for wd in ws[:max_elements]:
                if wd.kind == "label" and not wd.label:
                    continue
                r = wd.rect
                elements.append({"role": wd.role, "name": wd.label, "rect": (r.left, r.top, r.right, r.bottom),
                                 "enabled": True, "password": False})
        return Observation(pngmod.encode_bgra(bgra, w, h), Frame(Rect(0, 0, SCREEN_W, SCREEN_H), w, h),
                           self._cursor, {"title": self.scene.title, "process": "simulator.exe"}, elements,
                           self.target, capture_ms=(time.time() - t0) * 1000)

    def zoom(self, region, max_long_edge):
        c = self._render()
        try:
            w, h = fit_size(region.width, region.height, max_long_edge, allow_upscale=True)
            f = min(1.0, 4 * region.width / w)
            w, h = max(1, round(w * f)), max(1, round(h * f))
            bgra = c.scaled_bgra(w, h, region.left, region.top, region.width, region.height)
        finally:
            c.close()
        return pngmod.encode_bgra(bgra, w, h), w, h

    def click(self, x, y, button="left", clicks=1, modifiers=()):
        self._cursor = (x, y)
        self.inputs.append(("click", x, y, button, clicks))
        self.scene.on_click(x, y, button, clicks)

    def move(self, x, y):
        self._cursor = (x, y)
        self.inputs.append(("move", x, y))

    def drag(self, x1, y1, x2, y2):
        self._cursor = (x2, y2)
        self.inputs.append(("drag", x1, y1, x2, y2))
        self.scene.on_drag(x1, y1, x2, y2)

    def scroll(self, x, y, direction, amount):
        self._cursor = (x, y)
        self.inputs.append(("scroll", x, y, direction, amount))
        self.scene.on_scroll(x, y, direction, amount)

    def type_text(self, text):
        self.inputs.append(("type", text))
        self.scene.on_type(text)

    def key(self, keys, repeat=1):
        self.inputs.append(("key", keys, repeat))
        for _ in range(repeat):
            self.scene.on_key(keys)

    def wait(self, seconds):
        time.sleep(min(seconds, 0.2))

    def window_at(self, x, y):
        return {"title": self.scene.title, "process": "simulator.exe"}

    def foreground(self):
        return {"title": self.scene.title, "process": "simulator.exe"}

    def focused_is_password(self):
        return False

    def cursor(self):
        return self._cursor

    def settle(self):
        pass
