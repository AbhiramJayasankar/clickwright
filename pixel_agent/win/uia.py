"""Optional UI Automation reader (needs the `comtypes` package).

Used only for hints: a text list of named elements with their boxes, so a
model can aim better. Actions stay pixel based. Also answers "is the focused
control a password box?" for the safety check.
"""
import time

_UIA = None
_ERR = None

CONTROL_TYPES = {
    50000: "button", 50001: "calendar", 50002: "checkbox", 50003: "combobox", 50004: "edit",
    50005: "link", 50006: "image", 50007: "listitem", 50008: "list", 50009: "menu", 50010: "menubar",
    50011: "menuitem", 50012: "progressbar", 50013: "radio", 50014: "scrollbar", 50015: "slider",
    50016: "spinner", 50017: "statusbar", 50018: "tab", 50019: "tabitem", 50020: "text",
    50021: "toolbar", 50022: "tooltip", 50023: "tree", 50024: "treeitem", 50025: "custom",
    50026: "group", 50027: "thumb", 50028: "datagrid", 50029: "dataitem", 50030: "document",
    50031: "splitbutton", 50032: "window", 50033: "pane", 50034: "header", 50035: "headeritem",
    50036: "table", 50037: "titlebar", 50038: "separator", 50039: "semanticzoom", 50040: "appbar",
}
INTERACTIVE = {"button", "checkbox", "combobox", "edit", "link", "listitem", "menuitem", "radio",
               "slider", "spinner", "tabitem", "treeitem", "splitbutton", "dataitem", "headeritem",
               "document", "scrollbar", "thumb"}

_PROPS = {"BoundingRectangle": 30001, "ControlType": 30003, "Name": 30005, "IsKeyboardFocusable": 30009,
          "IsEnabled": 30010, "IsOffscreen": 30022, "IsPassword": 30019}


def available():
    return _load() is not None


def error():
    _load()
    return _ERR


def _load():
    global _UIA, _ERR
    if _UIA is not None or _ERR is not None:
        return _UIA
    try:
        import comtypes
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        auto = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        cache = auto.CreateCacheRequest()
        for pid in _PROPS.values():
            cache.AddProperty(pid)
        _UIA = (U, auto, cache)
    except Exception as e:  # comtypes missing, or UIA unavailable
        _ERR = f"{type(e).__name__}: {e}"
        _UIA = None
    return _UIA


def elements(hwnd, region, max_elements=150, time_budget=1.5):
    """Named/interactive, on-screen elements of a window intersecting region.

    Returns dicts with role, name, rect (screen px), enabled, password.
    """
    loaded = _load()
    if loaded is None or not hwnd:
        return []
    U, auto, cache = loaded
    t0 = time.time()
    try:
        root = auto.ElementFromHandle(hwnd)
        found = root.FindAllBuildCache(4, auto.ControlViewCondition, cache)  # TreeScope_Descendants
    except Exception:
        return []
    out = []
    n = found.Length
    for i in range(n):
        if time.time() - t0 > time_budget:
            break
        try:
            el = found.GetElement(i)
            if el.CachedIsOffscreen:
                continue
            r = el.CachedBoundingRectangle
            w, h = r.right - r.left, r.bottom - r.top
            if w < 3 or h < 3:
                continue
            if r.right <= region.left or r.left >= region.right or r.bottom <= region.top or r.top >= region.bottom:
                continue
            role = CONTROL_TYPES.get(el.CachedControlType, "other")
            name = (el.CachedName or "").strip()
            if not name and role not in INTERACTIVE:
                continue
            if role in ("pane", "group", "window", "custom") and not name:
                continue
            out.append({"role": role, "name": name[:80], "rect": (r.left, r.top, r.right, r.bottom),
                        "enabled": bool(el.CachedIsEnabled), "password": bool(el.CachedIsPassword)})
        except Exception:
            continue
    # Prefer interactive controls, then reading order.
    out.sort(key=lambda e: (e["role"] not in INTERACTIVE, e["rect"][1] // 8, e["rect"][0]))
    out = out[:max_elements]
    out.sort(key=lambda e: (e["rect"][1] // 8, e["rect"][0]))
    return out


def focused_is_password():
    loaded = _load()
    if loaded is None:
        return None
    _, auto, _ = loaded
    try:
        return bool(auto.GetFocusedElement().CurrentIsPassword)
    except Exception:
        return None
