"""Top-level windows: titles, owning process, bounds, focus."""
import ctypes
import os
import re
import time
from ctypes import wintypes as wt
from dataclasses import dataclass

from . import api
from .api import user32, kernel32
from ..geometry import Rect


# Shell windows that are never "existing user windows" for --protect-existing.
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                 "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland"}
SHELL_PROCESSES = {"searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
                   "textinputhost.exe", "searchapp.exe", "nvidia overlay.exe"}


def is_shell(win):
    return (win.get("class") in SHELL_CLASSES) or ((win.get("process") or "").lower() in SHELL_PROCESSES)


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process: str
    pid: int
    rect: Rect
    class_name: str = ""

    def to_dict(self):
        return {"hwnd": self.hwnd, "title": self.title, "process": self.process, "pid": self.pid,
                "rect": self.rect.to_dict() if self.rect else None, "class": self.class_name}


def _title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def process_of(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = ""
    h = kernel32.OpenProcess(api.PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(h)
    return name.lower(), pid.value


def bounds(hwnd):
    """Visible bounds (DWM extended frame, without the invisible resize border)."""
    r = wt.RECT()
    if api.dwmapi is not None and api.dwmapi.DwmGetWindowAttribute(
            hwnd, api.DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) == 0:
        return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
    return None


def _cloaked(hwnd):
    if api.dwmapi is None:
        return False
    v = wt.DWORD()
    api.dwmapi.DwmGetWindowAttribute(hwnd, api.DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return bool(v.value)


def info(hwnd):
    if not hwnd:
        return None
    proc, pid = process_of(hwnd)
    return WindowInfo(int(hwnd), _title(hwnd), proc, pid, bounds(hwnd), _class(hwnd))


def foreground():
    return info(user32.GetForegroundWindow())


def root_at(x, y):
    """Top-level window under a screen point (what a click there would hit)."""
    hwnd = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
    if not hwnd:
        return None
    return info(user32.GetAncestor(hwnd, api.GA_ROOT) or hwnd)


def list_windows():
    """Visible, titled, non-cloaked top-level windows in z-order (front first)."""
    out = []

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd) and not _cloaked(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            wi = info(hwnd)
            if wi and wi.rect and wi.rect.width > 1 and wi.rect.height > 1:
                out.append(wi)
        return True

    user32.EnumWindows(api.WNDENUMPROC(cb), 0)
    return out


def find(pattern):
    """First window whose title or process name matches the regex."""
    rx = re.compile(pattern, re.I)
    for w in list_windows():
        if rx.search(w.title) or rx.search(w.process):
            return w
    return None


def focus(hwnd):
    """Bring a window to the front. Windows blocks SetForegroundWindow from
    background processes unless an input event just happened, so tap F24 first
    (an unused key: Alt would also work but opens menu bars in some apps)."""
    from .input import press_keys
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, api.SW_RESTORE)
    press_keys([0x87])
    ok = bool(user32.SetForegroundWindow(hwnd))
    time.sleep(0.15)
    return ok and user32.GetForegroundWindow() == hwnd


def visible_stack():
    """Every visible, non-minimised, non-cloaked top-level window, front to back,
    titled or not (popups and menus included). Used for redaction.

    occluder=False for layered/transparent/tool windows: they may be see-through
    (overlays, shadows), so they never count as hiding what is behind them.
    """
    out = []

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        r = bounds(hwnd)
        if r is None or r.width <= 0 or r.height <= 0:
            return True
        ex = user32.GetWindowLongPtrW(hwnd, api.GWL_EXSTYLE)
        see_through = bool(ex & (api.WS_EX_LAYERED | api.WS_EX_TRANSPARENT | api.WS_EX_TOOLWINDOW
                                 | api.WS_EX_NOACTIVATE))
        cls = _class(hwnd)
        if cls in SHELL_CLASSES and cls in ("Progman", "WorkerW"):
            see_through = False  # the desktop itself is opaque
        proc, pid = process_of(hwnd)
        out.append({"hwnd": int(hwnd), "rect": r, "process": proc, "title": _title(hwnd),
                    "class": cls, "occluder": not see_through})
        return True

    user32.EnumWindows(api.WNDENUMPROC(cb), 0)
    return out


def all_top_level():
    """Every top-level window that has WS_VISIBLE set, including minimised and
    cloaked ones (other virtual desktops). Used to snapshot 'existing' windows."""
    out = []

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            proc, _ = process_of(hwnd)
            out.append({"hwnd": int(hwnd), "process": proc, "class": _class(hwnd), "title": _title(hwnd)})
        return True

    user32.EnumWindows(api.WNDENUMPROC(cb), 0)
    return out


def close_windows(hwnds):
    """Ask windows to close (WM_CLOSE, as the title-bar X does). Apps may still prompt to save."""
    for h in hwnds:
        user32.PostMessageW(h, api.WM_CLOSE, 0, 0)
