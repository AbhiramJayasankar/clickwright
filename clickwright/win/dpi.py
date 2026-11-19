"""Make this process per-monitor DPI aware.

Without this, Windows virtualises coordinates for scaled displays (125%,
150%...): screenshots come back at the logical size while SetCursorPos uses
another, and every click lands a little off. With per-monitor-v2 awareness all
APIs here agree on physical pixels.
"""
import ctypes

from .api import user32, shcore

_MODE = None

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def ensure_dpi_aware():
    """Idempotent. Returns the mode that ended up active."""
    global _MODE
    if _MODE is not None:
        return _MODE
    try:
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            _MODE = "per-monitor-v2"
            return _MODE
        # ERROR_ACCESS_DENIED (5) means awareness was already set (e.g. by a
        # manifest or an earlier call); check what it is rather than guessing.
    except AttributeError:
        pass
    try:
        if shcore is not None and shcore.SetProcessDpiAwareness(2) == 0:
            _MODE = "per-monitor"
            return _MODE
    except (AttributeError, OSError):
        pass
    try:
        get_ctx = user32.GetThreadDpiAwarenessContext
        get_ctx.restype = ctypes.c_void_p
        awareness = user32.GetAwarenessFromDpiAwarenessContext
        awareness.argtypes = [ctypes.c_void_p]
        value = awareness(get_ctx())
        _MODE = {0: "unaware", 1: "system", 2: "per-monitor"}.get(value, f"unknown({value})")
        return _MODE
    except AttributeError:
        pass
    if user32.SetProcessDPIAware():
        _MODE = "system"
    else:
        _MODE = "unknown"
    return _MODE
