# Changelog

## 0.1.0 — 2026-11-20

First public release.

### Added
- Windows driver: per-monitor DPI awareness, GDI capture scaled in the driver with the mouse pointer
  drawn in, SendInput mouse and keyboard (key combos, drag, wheel), window info, optional UI
  Automation hints.
- Typing sends real key presses for characters the keyboard layout has (Shift and Caps Lock aware)
  and Unicode input only for the rest. Windows 11 Notepad garbles fast all-Unicode input.
- Agent loop for any OpenAI-compatible endpoint, with tool calling or JSON in the reply, screenshot
  eviction in cache-friendly batches, zoom, step/budget/token limits and an `answer` tool to finish.
- Model profiles (TOML) with measured coordinate conventions (`calibrate`) and a capability probe.
  Calibration compares the model's unstated habit with how well it follows each stated convention,
  and flags models that switch conventions between answers.
- Redaction: windows of denied apps and titles are blacked out of every screenshot and zoom before
  encoding, using the real window stack (only opaque windows count as covering what's behind them).
- `--protect-existing`: every window open when the run starts, minimised ones included, is hidden and
  locked; the agent works only in windows it opens.
- Safety: Ctrl+Alt+Q kill switch, top-left-corner fail-safe, allow/deny apps and titles, password-box
  check, confirm and dry-run modes. Window-switching shortcuts (Win, Alt+Tab, Ctrl+Esc) are allowed
  even when a protected window has focus; Win+L is not.
- Offline simulator with five self-checking scenes, simulator and desktop evals, real-screen driver test.
- `max_request_mb` profile setting: older screenshots are evicted to keep each request under a
  provider's size cap, and an HTTP 413 is retried once with only the newest screenshot.
- PNG encoding tries the Sub row filter on large, photo-heavy screenshots and keeps the smaller result.
- Desktop eval closes the windows each task opened, and skips a task whose single-instance app
  (Settings) is already open in a protected window instead of failing it.
- Run logs with every screenshot and model reply, plus an HTML replay with click markers.
