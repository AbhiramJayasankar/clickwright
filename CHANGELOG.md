# Changelog

## 0.1.0 — 2026-11-19

### Added
- Windows driver: per-monitor DPI awareness, GDI capture scaled in-driver with the cursor drawn,
  SendInput mouse/keyboard (Unicode typing, key combos, drag, wheel), window info, optional UI Automation hints.
- Agent loop for any OpenAI-compatible endpoint, with tool calling or JSON-in-text, screenshot
  eviction in cache-friendly batches, zoom, step/budget/token limits and an `answer` tool to finish.
- Model profiles (TOML) with measured coordinate conventions (`calibrate`) and a capability probe.
- Safety: Ctrl+Alt+Q kill switch, top-left-corner fail-safe, allow/deny apps, protected windows
  (password managers, `.env`), password-box check, confirm and dry-run modes.
- Offline simulator with five self-checking scenes, sim and desktop evals, real-screen driver test.
- Run logs with every screenshot and model reply, plus an HTML replay with click markers.
- Calibration compares the model's unstated habit with how well it follows each stated convention,
  and flags models that switch conventions between answers.
