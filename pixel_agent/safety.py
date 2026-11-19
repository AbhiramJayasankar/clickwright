"""Guards around a model that is driving a real mouse and keyboard.

* kill switch: global hotkey (default Ctrl+Alt+Q) or flinging the mouse into
  the top-left corner of the primary monitor stops the run before the next action
* app rules: actions are refused when the window under the pointer (or the
  focused window, for typing) belongs to a denied app, or is not on the
  allow list when one is given
* protected windows: if a denied app is in front, nothing is captured or sent
* password boxes: typing into one is refused unless allowed
* confirm mode: every input action waits for y/n in the terminal
"""
import re

DEFAULT_DENY_APPS = ["1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "dashlane.exe",
                     "lastpass.exe", "enpass.exe", "nordpass.exe", "protonpass.exe", "roboform.exe",
                     "credentialuibroker.exe", "consent.exe"]
# Windows whose content must never be screenshotted and sent to an API by default.
DEFAULT_DENY_TITLES = [r"(^|[\s\\/*])\.env(\b|$)", r"\bpassword(s)?\b.*\b(manager|vault)\b", r"\bkeepass"]


class StopRun(Exception):
    """Raised when the run must end now (kill switch, protected window)."""


def _norm_app(name):
    name = name.strip().lower()
    return name if name.endswith(".exe") else name + ".exe"


class Safety:
    def __init__(self, allow_apps=None, deny_apps=None, deny_titles=None, allow_passwords=False,
                 confirm=False, kill_switch=None, corner_failsafe=True, use_default_deny=True,
                 confirm_fn=None):
        self.allow_apps = {_norm_app(a) for a in (allow_apps or [])}
        deny = list(DEFAULT_DENY_APPS) if use_default_deny else []
        self.deny_apps = {_norm_app(a) for a in deny + list(deny_apps or [])}
        titles = (list(DEFAULT_DENY_TITLES) if use_default_deny else []) + list(deny_titles or [])
        self.deny_titles = [re.compile(t, re.I) for t in titles]
        self.allow_passwords = allow_passwords
        self.confirm = confirm
        self.kill_switch = kill_switch
        self.corner_failsafe = corner_failsafe
        self._confirm_fn = confirm_fn or _ask
        self._last_agent_cursor = None
        self._corner = None

    # --- run-level checks ------------------------------------------------
    def set_primary_corner(self, x, y):
        self._corner = (x, y)

    def note_agent_cursor(self, pos):
        self._last_agent_cursor = pos

    def check_kill(self, cursor=None):
        if self.kill_switch is not None and self.kill_switch.fired:
            raise StopRun(f"kill switch pressed ({self.kill_switch.combo})")
        if self.corner_failsafe and cursor is not None and self._corner is not None:
            cx, cy = self._corner
            at_corner = abs(cursor[0] - cx) <= 1 and abs(cursor[1] - cy) <= 1
            agent_put_it_there = self._last_agent_cursor is not None and \
                abs(self._last_agent_cursor[0] - cx) <= 1 and abs(self._last_agent_cursor[1] - cy) <= 1
            if at_corner and not agent_put_it_there:
                raise StopRun("mouse moved to the top-left corner (fail-safe)")

    def _denied(self, win):
        if not win:
            return None
        proc = (win.get("process") or "").lower()
        if proc in self.deny_apps:
            return f"{proc} is on the deny list"
        title = win.get("title") or ""
        for rx in self.deny_titles:
            if rx.search(title):
                return f"window title {title!r} matches a denied pattern"
        return None

    def check_foreground(self, fg):
        """Before capturing: never screenshot a protected window."""
        reason = self._denied(fg)
        if reason:
            raise StopRun(f"protected window in front, not capturing: {reason}")

    # --- per-action checks ----------------------------------------------
    def check_target(self, action_name, win):
        """Return an error string if the action must not run on this window."""
        reason = self._denied(win)
        if reason:
            return f"refused: {reason}"
        if self.allow_apps and win is not None:
            proc = (win.get("process") or "").lower()
            if proc not in self.allow_apps:
                allowed = ", ".join(sorted(self.allow_apps))
                return f"refused: {proc or 'this window'} is not in the allowed apps ({allowed})"
        return None

    def check_typing(self, password_focused):
        if password_focused and not self.allow_passwords:
            return "refused: the focused field is a password box; the user must type passwords themselves"
        return None

    def ask(self, description):
        if not self.confirm:
            return True
        return self._confirm_fn(description)


def _ask(description):
    try:
        reply = input(f"\n  allow: {description} ? [y/N/q] ").strip().lower()
    except EOFError:
        return False
    if reply == "q":
        raise StopRun("stopped at confirmation prompt")
    return reply in ("y", "yes")
