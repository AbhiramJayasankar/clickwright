# pixel-agent

A small computer-use agent for Windows that works with **any model behind an
OpenAI-compatible endpoint**. The model sees screenshots and acts with **pixel
coordinates** (click, drag, scroll, type, keys). It never relies on element
references, so it works on anything visible: canvases, games, remote desktops,
apps with broken accessibility.

Model differences live in a small TOML profile per model. The one that matters
most, the coordinate convention, is **measured** (`calibrate`), not assumed.

No third-party packages are needed. `comtypes` is optional, for UI Automation
hints and the password-box check.

## Setup

```powershell
cd C:\Users\abhir\Desktop\projects\pixel-agent
python -m venv .venv
.venv\Scripts\python -m pip install comtypes
copy .env.example .env   # then paste your Token Harbor key into .env
```

Every command below is `.venv\Scripts\python -m pixel_agent <command>`.

## Commands

| command | touches your screen? | what it does |
|---|---|---|
| `check` | no | DPI mode, monitors and scaling, UI Automation, kill-switch hotkey, profiles |
| `models [--filter x]` | no | the provider's models with prices |
| `capabilities -p P` | no | does the endpoint take images, tool calls, JSON-in-text? |
| `calibrate -p P` | no | finds the model's coordinate convention and measures its aim on drawn test images |
| `sim -p P --scene form` | no | runs the agent in the offline simulator (form, targets, scroll, slider, menu) |
| `eval -p P --which sim` | no | all simulator scenes, with pass/fail checks |
| `driver-test` | **yes**, moves the mouse | click-accuracy self-test of capture → mapping → input |
| `run -p P "task"` | **yes** | the real thing |
| `eval -p P --which desktop` | **yes** | Notepad, Explorer, Calculator, Settings, Paint tasks with automatic checks |
| `screenshot out.png` | reads it | saves exactly what the model would see |
| `view [RUN_DIR]` | no | rebuilds and opens a run's HTML replay |

Examples:

```powershell
.venv\Scripts\python -m pixel_agent run -p deepseek-flash-free "Open Notepad and type hello"
.venv\Scripts\python -m pixel_agent run -p deepseek-flash-free --window "Notepad" --allow notepad.exe "Type hello"
.venv\Scripts\python -m pixel_agent run -p deepseek-flash-free --confirm --dry-run "Open Settings"
```

## Stopping it

* **Ctrl+Alt+Q** (change with `--kill-key`), or
* push the mouse into the **top-left corner** of the main screen, or
* Ctrl+C in the terminal.

The run stops before the next action.

## Safety defaults

* **Protected windows are never captured.** Password managers and any window
  titled `.env` stop the run instead of being screenshotted. Add more with
  `--deny app.exe` / `--deny-title REGEX`.
* `--allow app.exe` (repeatable) refuses any click or keypress whose target is
  another app. The check is per action, on the window under the pointer.
* Typing into a password box is refused (`--allow-passwords` to override).
* `--confirm` asks y/n before every input action. `--dry-run` logs actions
  without running them.
* `--window REGEX` sends only that window's area to the model.

Screenshots go to the model provider. Close anything private first, or use
`--window` and `--allow`.

## Profiles (`profiles/*.toml`)

See `profiles/_template.toml`. The main settings:

* `model`, `api_base`, `api_key`: any OpenAI-compatible endpoint. `${VAR}` reads `.env`.
* `coords`: `pixels`, `norm1000`, `norm999`, `norm1`, or `auto` (use the calibration result).
* `output`: `tool_calls` (function calling) or `json_text` (for models without it).
* `max_long_edge`: screenshot size sent to the model; the aspect ratio is kept.
* `keep_screenshots` / `evict_batch`: how much screenshot history stays in the
  prompt. Old ones are evicted in batches, so the prompt prefix stays cacheable.
* `carry_reasoning`, `end_mode`, `temperature`, `extra_body`: per-model quirks.
* `element_hints`: adds a text list of UI Automation elements with their boxes,
  to help weaker models aim. Actions stay pixel based.

## How it works

```
screenshot (GDI, scaled in-driver, cursor drawn) ──► model ──► tool calls / JSON
     ▲                                                          │
     │                     model coords ─(convention)─► image px ─(frame)─► screen px
     └──────── new screenshot ◄── SendInput / SetCursorPos ◄────┘
```

* The process is **per-monitor DPI aware**, so capture, SetCursorPos and
  SendInput all use physical pixels at any display scaling.
* There is one conversion path (`geometry.py`): model → image → screen, using
  the frame of the screenshot the model actually saw.
* Every run writes `runs/<time>-<profile>-<task>/`: every screenshot, the raw
  model reply and reasoning, parsed actions, screen coordinates, the window
  that was hit, timings, tokens and cost. `report.html` replays it with click
  markers.

## Layout

```
pixel_agent/
  win/          Windows backend: api (ctypes), dpi, screen (capture + GDI canvas),
                input (SendInput), keys, windows, uia (optional), hotkey
  geometry.py   coordinate spaces and conventions
  actions.py    action set, tool schemas, tolerant parsing of replies
  llm.py        OpenAI-compatible client: retries, usage, cost from /models
  prompt.py     system prompt and per-step observation text
  agent.py      the loop
  safety.py     kill switch, app rules, protected windows, password check
  driver.py     Driver interface + WindowsDriver
  sim.py        offline simulator scenes (self-checking)
  calibrate.py  capability probe + coordinate calibration
  evaluate.py   sim + desktop evals with checks
  drivertest.py real-screen click-accuracy test
  runlog.py, viewer.py
tasks/desktop.toml   desktop eval tasks
tests/               offline unit tests: python -m unittest discover -s tests
```

## Limits

* Windows Sandbox is not available on Windows Home, so runs use the real
  desktop. Use `--allow`, `--window` and `--confirm` to fence it in.
* Input can't reach windows running as administrator (UAC), or the secure
  desktop, from a normal process.
* `--window` capture copies the screen area of that window, so anything on top
  of it is visible too.
