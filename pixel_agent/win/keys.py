"""Key names -> virtual-key codes.

Accepts the spellings models actually produce: "ctrl+s", "Control_L+s"
(xdotool style), "cmd+c", "Return", "Page_Down", "F5", single characters.
"""
from .api import user32

_NAMED = {
    "ctrl": 0x11, "control": 0x11, "control_l": 0xA2, "control_r": 0xA3, "lctrl": 0xA2, "rctrl": 0xA3,
    "shift": 0x10, "shift_l": 0xA0, "shift_r": 0xA1, "lshift": 0xA0, "rshift": 0xA1,
    "alt": 0x12, "menu": 0x12, "option": 0x12, "alt_l": 0xA4, "alt_r": 0xA5, "lalt": 0xA4, "ralt": 0xA5,
    "altgr": 0xA5,
    "win": 0x5B, "windows": 0x5B, "super": 0x5B, "super_l": 0x5B, "super_r": 0x5C, "meta": 0x5B,
    "cmd": 0x5B, "command": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "enter": 0x0D, "return": 0x0D, "kp_enter": 0x0D,
    "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "space": 0x20, "spacebar": 0x20,
    "backspace": 0x08, "back_space": 0x08, "bksp": 0x08,
    "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "page_up": 0x21, "pgup": 0x21, "prior": 0x21,
    "pagedown": 0x22, "page_down": 0x22, "pgdn": 0x22, "next": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "arrowup": 0x26, "arrowdown": 0x28, "arrowleft": 0x25, "arrowright": 0x27,
    "capslock": 0x14, "caps_lock": 0x14, "numlock": 0x90, "num_lock": 0x90, "scrolllock": 0x91,
    "printscreen": 0x2C, "print": 0x2C, "prtsc": 0x2C, "pause": 0x13, "break": 0x13,
    "apps": 0x5D, "contextmenu": 0x5D, "context_menu": 0x5D,
    "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD, "mute": 0xAD,
    "medianext": 0xB0, "mediaprev": 0xB1, "mediaplaypause": 0xB3, "playpause": 0xB3,
    "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
    "kp_add": 0x6B, "kp_subtract": 0x6D, "kp_multiply": 0x6A, "kp_divide": 0x6F, "kp_decimal": 0x6E,
}
for _i in range(1, 25):
    _NAMED[f"f{_i}"] = 0x6F + _i
for _i in range(10):
    _NAMED[f"kp_{_i}"] = 0x60 + _i
    _NAMED[f"numpad{_i}"] = 0x60 + _i

MODIFIERS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}

# Keys that need KEYEVENTF_EXTENDEDKEY or apps see the numpad variant.
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D,
            0xA3, 0xA5, 0x6F, 0x90, 0x2C}


class KeyComboError(ValueError):
    pass


def vk_for(name):
    """Return (vk, needs_shift) for one key name."""
    raw = name.strip()
    low = raw.lower()
    if low in _NAMED:
        return _NAMED[low], False
    if len(raw) == 1:
        ch = raw
        if "a" <= ch.lower() <= "z":
            return ord(ch.upper()), False
        if "0" <= ch <= "9":
            return ord(ch), False
        res = user32.VkKeyScanW(ch)
        if res == -1:
            raise KeyComboError(f"no key for character {ch!r} on this keyboard layout")
        return res & 0xFF, bool(res & 0x100)
    raise KeyComboError(f"unknown key name {name!r}")


def parse_combo(combo):
    """'ctrl+shift+s' -> list of vk codes in press order (modifiers first)."""
    text = combo.strip()
    if not text:
        raise KeyComboError("empty key combination")
    # Allow a literal '+' key: "ctrl++" or "+".
    parts = []
    token = ""
    for i, ch in enumerate(text):
        if ch in "+-" and token and not (ch == "-" and i == len(text) - 1):
            parts.append(token)
            token = ""
        elif ch == " " and token:
            parts.append(token)
            token = ""
        elif ch != " ":
            token += ch
    if token:
        parts.append(token)
    codes = []
    for p in parts:
        vk, shift = vk_for(p)
        if shift and 0x10 not in codes:
            codes.insert(0, 0x10)
        codes.append(vk)
    mods = [c for c in codes if c in MODIFIERS]
    rest = [c for c in codes if c not in MODIFIERS]
    return mods + rest
