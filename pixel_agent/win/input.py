"""Mouse and keyboard input through SendInput.

Positioning uses SetCursorPos after an absolute move event: the event puts a
real move into the input stream (some apps only react to that) and
SetCursorPos then corrects the 1-pixel rounding of the 0..65535 scale, so the
cursor ends exactly on the requested physical pixel.
"""
import ctypes
import time

from . import api
from .api import user32
from .keys import parse_combo, EXTENDED
from .screen import virtual_screen, cursor_pos

_BUTTONS = {
    "left": (api.MOUSEEVENTF_LEFTDOWN, api.MOUSEEVENTF_LEFTUP),
    "right": (api.MOUSEEVENTF_RIGHTDOWN, api.MOUSEEVENTF_RIGHTUP),
    "middle": (api.MOUSEEVENTF_MIDDLEDOWN, api.MOUSEEVENTF_MIDDLEUP),
}

# Pause between low-level events; apps drop clicks that arrive too fast.
EVENT_GAP = 0.012


def _send(*inputs):
    arr = (api.INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(api.INPUT))
    if sent != len(inputs):
        raise OSError(f"SendInput sent {sent}/{len(inputs)} events (error {ctypes.get_last_error()}); "
                      "input may be blocked by a higher-integrity window (e.g. an admin app or UAC prompt)")


def _mouse(flags, dx=0, dy=0, data=0):
    i = api.INPUT(type=api.INPUT_MOUSE)
    i.mi = api.MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    return i


def _key(vk=0, scan=0, flags=0):
    i = api.INPUT(type=api.INPUT_KEYBOARD)
    i.ki = api.KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


def move_to(x, y):
    vs = virtual_screen()
    nx = round((x - vs.left) * 65535 / max(1, vs.width - 1))
    ny = round((y - vs.top) * 65535 / max(1, vs.height - 1))
    _send(_mouse(api.MOUSEEVENTF_MOVE | api.MOUSEEVENTF_ABSOLUTE | api.MOUSEEVENTF_VIRTUALDESK, nx, ny))
    user32.SetCursorPos(int(x), int(y))
    time.sleep(EVENT_GAP)


def button(name="left", down=True):
    d, u = _BUTTONS[name]
    _send(_mouse(d if down else u))


def press_keys(vks):
    """Hold vks in order, release in reverse."""
    downs = [_key(vk, user32.MapVirtualKeyW(vk, 0), api.KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
             for vk in vks]
    ups = [_key(vk, user32.MapVirtualKeyW(vk, 0),
                api.KEYEVENTF_KEYUP | (api.KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0))
           for vk in reversed(vks)]
    for ev in downs:
        _send(ev)
        time.sleep(EVENT_GAP / 2)
    for ev in ups:
        _send(ev)
        time.sleep(EVENT_GAP / 2)


def click(x, y, button_name="left", count=1, modifiers=()):
    mods = []
    for m in modifiers or ():
        mods.extend(parse_combo(m))
    move_to(x, y)
    for vk in mods:
        _send(_key(vk, user32.MapVirtualKeyW(vk, 0), api.KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0))
    try:
        gap = min(0.06, user32.GetDoubleClickTime() / 1000 / 4)
        for n in range(count):
            button(button_name, True)
            time.sleep(EVENT_GAP)
            button(button_name, False)
            if n + 1 < count:
                time.sleep(gap)
    finally:
        for vk in reversed(mods):
            _send(_key(vk, user32.MapVirtualKeyW(vk, 0),
                       api.KEYEVENTF_KEYUP | (api.KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)))
    time.sleep(EVENT_GAP)


def drag(x1, y1, x2, y2, button_name="left", steps=None, duration=0.4):
    move_to(x1, y1)
    button(button_name, True)
    try:
        dist = max(abs(x2 - x1), abs(y2 - y1))
        steps = steps or max(8, min(60, dist // 12))
        time.sleep(0.05)
        for i in range(1, steps + 1):
            t = i / steps
            move_to(round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t))
            time.sleep(duration / steps)
        time.sleep(0.05)
    finally:
        button(button_name, False)


def scroll(x, y, direction="down", amount=3):
    move_to(x, y)
    amount = max(1, int(amount))
    if direction in ("up", "down"):
        data = api.WHEEL_DELTA * (1 if direction == "up" else -1)
        flag = api.MOUSEEVENTF_WHEEL
    elif direction in ("left", "right"):
        data = api.WHEEL_DELTA * (1 if direction == "right" else -1)
        flag = api.MOUSEEVENTF_HWHEEL
    else:
        raise ValueError(f"scroll direction must be up/down/left/right, not {direction!r}")
    for _ in range(amount):
        _send(_mouse(flag, data=data))
        time.sleep(0.03)


def key(combo, repeat=1):
    vks = parse_combo(combo)
    for _ in range(max(1, int(repeat))):
        press_keys(vks)
        time.sleep(EVENT_GAP)


def type_text(text, interval=0.004):
    """Type text independent of the keyboard layout (KEYEVENTF_UNICODE).
    Newlines become Enter and tabs Tab, because many apps ignore unicode CR."""
    for ch in text:
        if ch == "\r":
            continue
        if ch == "\n":
            press_keys([0x0D])
            continue
        if ch == "\t":
            press_keys([0x09])
            continue
        units = ch.encode("utf-16-le")
        codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
        evs = []
        for c in codes:
            evs.append(_key(0, c, api.KEYEVENTF_UNICODE))
        for c in codes:
            evs.append(_key(0, c, api.KEYEVENTF_UNICODE | api.KEYEVENTF_KEYUP))
        _send(*evs)
        if interval:
            time.sleep(interval)


def current_cursor():
    return cursor_pos()
