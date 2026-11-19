"""Monitors, screen capture and an offscreen GDI canvas.

Capture scales inside GDI (StretchBlt with HALFTONE), so a 4K screen is never
copied to Python at full size just to be shrunk. The mouse cursor is drawn
into the image so the model can see where it is.
"""
import ctypes
from ctypes import wintypes as wt
from dataclasses import dataclass

from . import api
from .api import gdi32, user32
from ..geometry import Rect, Frame, fit_size
from .. import png


@dataclass
class Monitor:
    index: int
    rect: Rect
    work: Rect
    primary: bool
    device: str
    dpi: int
    handle: int = 0

    @property
    def scale_percent(self):
        return round(self.dpi / 96 * 100)

    def to_dict(self):
        return {"index": self.index, "rect": self.rect.to_dict(), "work": self.work.to_dict(),
                "primary": self.primary, "device": self.device, "dpi": self.dpi,
                "scale_percent": self.scale_percent}


def _rect(r):
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def monitors():
    """All monitors, primary first, then left-to-right."""
    found = []

    def cb(hmon, _hdc, _lprc, _lp):
        info = api.MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        dpi = 96
        if api.shcore is not None:
            dx, dy = wt.UINT(), wt.UINT()
            if api.shcore.GetDpiForMonitor(hmon, api.MDT_EFFECTIVE_DPI, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                dpi = dx.value
        found.append((hmon, info.rcMonitor, info.rcWork, bool(info.dwFlags & api.MONITORINFOF_PRIMARY),
                      info.szDevice, dpi))
        return True

    user32.EnumDisplayMonitors(None, None, api.MONITORENUMPROC(cb), 0)
    found.sort(key=lambda m: (not m[3], m[1].left, m[1].top))
    return [Monitor(i, _rect(rc), _rect(wk), prim, dev, dpi, hmon or 0)
            for i, (hmon, rc, wk, prim, dev, dpi) in enumerate(found)]


def virtual_screen():
    return Rect(user32.GetSystemMetrics(api.SM_XVIRTUALSCREEN), user32.GetSystemMetrics(api.SM_YVIRTUALSCREEN),
                user32.GetSystemMetrics(api.SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(api.SM_CYVIRTUALSCREEN))


def cursor_pos():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _bitmap_info(width, height):
    bmi = api.BITMAPINFO()
    h = bmi.bmiHeader
    h.biSize = ctypes.sizeof(api.BITMAPINFOHEADER)
    h.biWidth, h.biHeight = width, -height  # negative = top-down rows
    h.biPlanes, h.biBitCount, h.biCompression = 1, 32, api.BI_RGB
    return bmi


def _read_bits(hdc, hbmp, width, height):
    buf = ctypes.create_string_buffer(width * height * 4)
    bmi = _bitmap_info(width, height)
    if gdi32.GetDIBits(hdc, hbmp, 0, height, buf, ctypes.byref(bmi), api.DIB_RGB_COLORS) != height:
        raise OSError("GetDIBits failed")
    return buf.raw


def _draw_cursor(hdc_mem, frame):
    ci = api.CURSORINFO()
    ci.cbSize = ctypes.sizeof(ci)
    if not user32.GetCursorInfo(ctypes.byref(ci)) or not (ci.flags & api.CURSOR_SHOWING):
        return None
    cx, cy = ci.ptScreenPos.x, ci.ptScreenPos.y
    if not frame.region.contains(cx, cy):
        return (cx, cy)
    ii = api.ICONINFO()
    hot_x = hot_y = 0
    if user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
        hot_x, hot_y = ii.xHotspot, ii.yHotspot
        for h in (ii.hbmMask, ii.hbmColor):
            if h:
                gdi32.DeleteObject(h)
    scale = 1 / max(frame.sx, frame.sy)
    size = max(12, round(32 * scale))
    ix, iy = frame.screen_to_image(cx, cy)
    user32.DrawIconEx(hdc_mem, round(ix - hot_x * scale), round(iy - hot_y * scale), ci.hCursor,
                      size, size, 0, None, api.DI_NORMAL)
    return (cx, cy)


@dataclass
class Capture:
    frame: Frame
    bgra: bytes
    cursor: tuple = None  # screen position, or None if hidden

    def png(self):
        return png.encode_bgra(self.bgra, self.frame.image_w, self.frame.image_h)

    def pixel(self, x, y):
        i = (y * self.frame.image_w + x) * 4
        b, g, r = self.bgra[i], self.bgra[i + 1], self.bgra[i + 2]
        return r, g, b


def capture(region, image_w=None, image_h=None, max_long_edge=None, draw_cursor=True, allow_upscale=False):
    """Capture a screen region (physical pixels) into an image of the given size.

    Either pass image_w/image_h, or max_long_edge (keeps the aspect ratio).
    """
    if image_w is None or image_h is None:
        image_w, image_h = fit_size(region.width, region.height, max_long_edge or max(region.width, region.height),
                                    allow_upscale=allow_upscale)
    frame = Frame(region, image_w, image_h)
    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, image_w, image_h)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        if (image_w, image_h) == (region.width, region.height):
            ok = gdi32.BitBlt(hdc_mem, 0, 0, image_w, image_h, hdc_screen, region.left, region.top,
                              api.SRCCOPY | api.CAPTUREBLT)
        else:
            gdi32.SetStretchBltMode(hdc_mem, api.HALFTONE)
            gdi32.SetBrushOrgEx(hdc_mem, 0, 0, None)
            ok = gdi32.StretchBlt(hdc_mem, 0, 0, image_w, image_h, hdc_screen, region.left, region.top,
                                  region.width, region.height, api.SRCCOPY | api.CAPTUREBLT)
        if not ok:
            raise OSError(f"screen copy failed (error {ctypes.get_last_error()})")
        cursor = _draw_cursor(hdc_mem, frame) if draw_cursor else None
        bits = _read_bits(hdc_mem, hbmp, image_w, image_h)
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)
    return Capture(frame, bits, cursor)


class Canvas:
    """Offscreen GDI drawing surface with real fonts. Used for calibration
    images, the simulator and click markers; it never touches the screen."""

    def __init__(self, width, height, background=(255, 255, 255)):
        self.width, self.height = width, height
        hdc_screen = user32.GetDC(None)
        self.hdc = gdi32.CreateCompatibleDC(hdc_screen)
        self.hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        user32.ReleaseDC(None, hdc_screen)
        self._old = gdi32.SelectObject(self.hdc, self.hbmp)
        gdi32.SetBkMode(self.hdc, 1)  # TRANSPARENT
        self._objects = []
        self.rect(0, 0, width, height, fill=background)

    def _brush(self, color):
        b = gdi32.CreateSolidBrush(api.rgb(*color))
        self._objects.append(b)
        return b

    def _pen(self, color, width=1):
        p = gdi32.CreatePen(0, width, api.rgb(*color))
        self._objects.append(p)
        return p

    def rect(self, x, y, w, h, fill=None, outline=None, width=1):
        gdi32.SelectObject(self.hdc, self._brush(fill) if fill else gdi32.GetStockObject(5))  # NULL_BRUSH
        gdi32.SelectObject(self.hdc, self._pen(outline, width) if outline else gdi32.GetStockObject(8))  # NULL_PEN
        # Rectangle excludes the bottom/right edge when there is no pen, so add 1.
        extra = 0 if outline else 1
        gdi32.Rectangle(self.hdc, x, y, x + w + extra, y + h + extra)

    def ellipse(self, x, y, w, h, fill=None, outline=None, width=1):
        gdi32.SelectObject(self.hdc, self._brush(fill) if fill else gdi32.GetStockObject(5))
        gdi32.SelectObject(self.hdc, self._pen(outline, width) if outline else gdi32.GetStockObject(8))
        extra = 0 if outline else 1
        gdi32.Ellipse(self.hdc, x, y, x + w + extra, y + h + extra)

    def line(self, x1, y1, x2, y2, color=(0, 0, 0), width=1):
        gdi32.SelectObject(self.hdc, self._pen(color, width))
        gdi32.MoveToEx(self.hdc, x1, y1, None)
        gdi32.LineTo(self.hdc, x2, y2)

    def text(self, x, y, s, size=20, color=(0, 0, 0), bold=False, face="Segoe UI"):
        font = gdi32.CreateFontW(-size, 0, 0, 0, 700 if bold else 400, 0, 0, 0, 1, 0, 0, 5, 0, face)
        self._objects.append(font)
        gdi32.SelectObject(self.hdc, font)
        gdi32.SetTextColor(self.hdc, api.rgb(*color))
        gdi32.TextOutW(self.hdc, x, y, s, len(s))
        sz = wt.SIZE()
        gdi32.GetTextExtentPoint32W(self.hdc, s, len(s), ctypes.byref(sz))
        return sz.cx, sz.cy

    def text_size(self, s, size=20, bold=False, face="Segoe UI"):
        font = gdi32.CreateFontW(-size, 0, 0, 0, 700 if bold else 400, 0, 0, 0, 1, 0, 0, 5, 0, face)
        self._objects.append(font)
        gdi32.SelectObject(self.hdc, font)
        sz = wt.SIZE()
        gdi32.GetTextExtentPoint32W(self.hdc, s, len(s), ctypes.byref(sz))
        return sz.cx, sz.cy

    def paste_bgra(self, bgra, width, height, x=0, y=0):
        bmi = _bitmap_info(width, height)
        gdi32.SetDIBitsToDevice(self.hdc, x, y, width, height, 0, 0, 0, height, bgra, ctypes.byref(bmi),
                                api.DIB_RGB_COLORS)

    def bgra(self):
        gdi32.SelectObject(self.hdc, gdi32.GetStockObject(5))
        gdi32.SelectObject(self.hdc, gdi32.GetStockObject(8))
        return _read_bits(self.hdc, self.hbmp, self.width, self.height)

    def png(self):
        return png.encode_bgra(self.bgra(), self.width, self.height)

    def scaled_bgra(self, width, height, sx=0, sy=0, sw=None, sh=None):
        """Scale (a region of) the canvas with the same HALFTONE filter as capture()."""
        sw, sh = sw or self.width, sh or self.height
        hdc2 = gdi32.CreateCompatibleDC(self.hdc)
        hbmp2 = gdi32.CreateCompatibleBitmap(self.hdc, width, height)
        old = gdi32.SelectObject(hdc2, hbmp2)
        try:
            gdi32.SetStretchBltMode(hdc2, api.HALFTONE)
            gdi32.SetBrushOrgEx(hdc2, 0, 0, None)
            gdi32.StretchBlt(hdc2, 0, 0, width, height, self.hdc, sx, sy, sw, sh, api.SRCCOPY)
            return _read_bits(hdc2, hbmp2, width, height)
        finally:
            gdi32.SelectObject(hdc2, old)
            gdi32.DeleteObject(hbmp2)
            gdi32.DeleteDC(hdc2)

    def close(self):
        if self.hdc:
            gdi32.SelectObject(self.hdc, self._old)
            for stock in (5, 8, 13):  # NULL_BRUSH, NULL_PEN, SYSTEM_FONT: deselect ours first
                gdi32.SelectObject(self.hdc, gdi32.GetStockObject(stock))
            for o in self._objects:
                gdi32.DeleteObject(o)
            self._objects.clear()
            gdi32.DeleteObject(self.hbmp)
            gdi32.DeleteDC(self.hdc)
            self.hdc = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
