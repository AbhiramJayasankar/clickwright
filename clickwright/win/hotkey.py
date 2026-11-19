"""Global kill-switch hotkey (default Ctrl+Alt+Q) on a background thread."""
import ctypes
import threading

from . import api
from .api import user32, kernel32
from .keys import parse_combo

_MOD_FOR_VK = {0x11: api.MOD_CONTROL, 0xA2: api.MOD_CONTROL, 0xA3: api.MOD_CONTROL,
               0x10: api.MOD_SHIFT, 0xA0: api.MOD_SHIFT, 0xA1: api.MOD_SHIFT,
               0x12: api.MOD_ALT, 0xA4: api.MOD_ALT, 0xA5: api.MOD_ALT,
               0x5B: api.MOD_WIN, 0x5C: api.MOD_WIN}


class KillSwitch:
    def __init__(self, combo="ctrl+alt+q"):
        self.combo = combo
        self.event = threading.Event()
        self.registered = False
        self.error = None
        self._thread = None
        self._tid = None
        self._ready = threading.Event()

    def start(self):
        vks = parse_combo(self.combo)
        mods = 0
        key = None
        for vk in vks:
            if vk in _MOD_FOR_VK:
                mods |= _MOD_FOR_VK[vk]
            else:
                key = vk
        if key is None:
            raise ValueError("kill-switch hotkey needs a non-modifier key")

        def run():
            self._tid = kernel32.GetCurrentThreadId()
            if not user32.RegisterHotKey(None, 0xB00B, mods | api.MOD_NOREPEAT, key):
                self.error = f"RegisterHotKey failed for {self.combo} (error {ctypes.get_last_error()}); " \
                             "another app may own it"
                self._ready.set()
                return
            self.registered = True
            self._ready.set()
            msg = api.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == api.WM_HOTKEY:
                    self.event.set()
            user32.UnregisterHotKey(None, 0xB00B)

        self._thread = threading.Thread(target=run, name="kill-switch", daemon=True)
        self._thread.start()
        self._ready.wait(2)
        return self

    @property
    def fired(self):
        return self.event.is_set()

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, api.WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(1)
