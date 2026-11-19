"""Drivers: what the agent observes and where its input goes.

The agent only talks to this interface, so the same loop runs on the real
desktop (WindowsDriver) or in the offline simulator (sim.SimDriver).
Drivers take and return *screen* coordinates; the agent converts the model's
numbers to screen pixels with the frame of the screenshot the model saw.
"""
import time
from dataclasses import dataclass, field

from .geometry import Frame, Rect, fit_size


@dataclass
class Observation:
    png: bytes
    frame: Frame
    cursor: tuple | None = None          # screen coords, None if hidden/outside
    foreground: dict | None = None       # {"title", "process", ...}
    elements: list = field(default_factory=list)  # [{"role","name","rect"(screen)}]
    target: str = ""
    taken_at: float = field(default_factory=time.time)
    capture_ms: float = 0.0
    redacted: int = 0                    # number of blacked-out areas
    note: str = ""                       # extra text for the model about this observation


class Driver:
    """Interface. Coordinates are screen pixels."""
    name = "abstract"

    def observe(self, max_long_edge, with_elements=False, max_elements=120) -> Observation:
        raise NotImplementedError

    def zoom(self, region: Rect, max_long_edge) -> tuple:
        """Return (png bytes, width, height) of a screen region, enlarged to fit max_long_edge."""
        raise NotImplementedError

    def click(self, x, y, button="left", clicks=1, modifiers=()):
        raise NotImplementedError

    def move(self, x, y):
        raise NotImplementedError

    def drag(self, x1, y1, x2, y2):
        raise NotImplementedError

    def scroll(self, x, y, direction, amount):
        raise NotImplementedError

    def type_text(self, text):
        raise NotImplementedError

    def key(self, keys, repeat=1):
        raise NotImplementedError

    def wait(self, seconds):
        time.sleep(seconds)

    def window_at(self, x, y) -> dict | None:
        return None

    def foreground(self) -> dict | None:
        return None

    def focused_is_password(self):
        return None

    def cursor(self):
        return None

    def settle(self):
        """Give the UI a moment to react after an action."""
        time.sleep(0.35)

    def close(self):
        pass


class WindowsDriver(Driver):
    """Real desktop. target: 'monitor:N' (default primary), 'screen' (all
    monitors) or 'window:<regex>' (only that window's area)."""
    name = "windows"

    def __init__(self, target="monitor:0", draw_cursor=True, settle_seconds=0.35, protect=None):
        from .win.dpi import ensure_dpi_aware
        ensure_dpi_aware()  # physical pixels everywhere
        from .win import screen, input as winput, windows, uia
        self._screen, self._input, self._windows, self._uia = screen, winput, windows, uia
        self.target = target
        self.draw_cursor = draw_cursor
        self.settle_seconds = settle_seconds
        # protect(window_dict) -> True blacks that window out of every screenshot
        self.protect = protect
        self._hwnd = None
        self._last_redactions = []
        if target.startswith("window:"):
            w = windows.find(target.split(":", 1)[1])
            if not w:
                raise RuntimeError(f"no window matches {target.split(':', 1)[1]!r}")
            self._hwnd = w.hwnd
            windows.focus(w.hwnd)
        elif target.startswith("monitor:"):
            idx = int(target.split(":", 1)[1] or 0)
            mons = screen.monitors()
            if idx >= len(mons):
                raise RuntimeError(f"monitor {idx} not found ({len(mons)} connected)")
        elif target != "screen":
            raise ValueError("target must be monitor:N, screen or window:<regex>")

    def region(self):
        if self._hwnd:
            r = self._windows.bounds(self._hwnd)
            if r is None or r.width < 2:
                raise RuntimeError("target window is gone or minimised")
            return r
        if self.target == "screen":
            return self._screen.virtual_screen()
        idx = int(self.target.split(":", 1)[1] or 0)
        return self._screen.monitors()[idx].rect

    def observe(self, max_long_edge, with_elements=False, max_elements=120):
        t0 = time.time()
        region = self.region()
        cap = self._screen.capture(region, max_long_edge=max_long_edge, draw_cursor=self.draw_cursor)
        n_redacted = self._redact(cap)
        fg = self._windows.foreground()
        png = cap.png()
        elements = []
        if with_elements:
            hwnd = self._hwnd or (fg.hwnd if fg else None)
            elements = self._uia.elements(hwnd, region, max_elements=max_elements)
        cursor = cap.cursor if cap.cursor and region.contains(*cap.cursor) else None
        if elements and self.protect:
            elements = [e for e in elements if not self._in_redacted(e["rect"])]
        return Observation(png, cap.frame, cursor, fg.to_dict() if fg else None, elements, self.target,
                           redacted=n_redacted, capture_ms=(time.time() - t0) * 1000)

    def redaction_rects(self):
        if not self.protect:
            return []
        from .geometry import redaction_rects
        return redaction_rects(self._windows.visible_stack(), self.protect)

    def _in_redacted(self, rect):
        l, t, r, b = rect
        cx, cy = (l + r) / 2, (t + b) / 2
        return any(x.contains(cx, cy) for x in self._last_redactions)

    def _redact(self, cap):
        """Black out protected windows in a capture (in place). Returns the count."""
        self._last_redactions = rects = self.redaction_rects()
        if not rects:
            return 0
        f = cap.frame
        buf = bytearray(cap.bgra)
        n = 0
        for r in rects:
            # screen -> image, rounded outwards so no protected pixel survives scaling
            x0 = max(0, int((r.left - f.region.left) / f.sx) - 1)
            y0 = max(0, int((r.top - f.region.top) / f.sy) - 1)
            x1 = min(f.image_w, int((r.right - f.region.left) / f.sx + 0.999) + 1)
            y1 = min(f.image_h, int((r.bottom - f.region.top) / f.sy + 0.999) + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            n += 1
            row = bytes(4 * (x1 - x0))
            for y in range(y0, y1):
                i = (y * f.image_w + x0) * 4
                buf[i:i + len(row)] = row
        cap.bgra = bytes(buf)
        return n

    def zoom(self, region, max_long_edge):
        w, h = fit_size(region.width, region.height, max_long_edge, allow_upscale=True)
        # Do not blow tiny regions up beyond 4x; it only adds blur and tokens.
        f = min(1.0, 4 * region.width / w)
        w, h = max(1, round(w * f)), max(1, round(h * f))
        cap = self._screen.capture(region, image_w=w, image_h=h, draw_cursor=self.draw_cursor)
        self._redact(cap)
        return cap.png(), w, h

    def click(self, x, y, button="left", clicks=1, modifiers=()):
        self._input.click(x, y, button, clicks, modifiers)

    def move(self, x, y):
        self._input.move_to(x, y)

    def drag(self, x1, y1, x2, y2):
        self._input.drag(x1, y1, x2, y2)

    def scroll(self, x, y, direction, amount):
        self._input.scroll(x, y, direction, amount)

    def type_text(self, text):
        self._input.type_text(text)

    def key(self, keys, repeat=1):
        self._input.key(keys, repeat)

    def window_at(self, x, y):
        w = self._windows.root_at(x, y)
        return w.to_dict() if w else None

    def foreground(self):
        w = self._windows.foreground()
        return w.to_dict() if w else None

    def focused_is_password(self):
        return self._uia.focused_is_password()

    def cursor(self):
        return self._screen.cursor_pos()

    def settle(self):
        time.sleep(self.settle_seconds)
