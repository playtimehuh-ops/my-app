# AI Computer Agent

A Windows desktop app that lets an AI (via OpenRouter, using a free model)
operate your computer through a visible, controllable agent: it looks at
screenshots, moves a distinct on-screen "AI cursor," clicks, types, and
talks to you about what it's doing — with a hard local emergency-stop
(F8) that works even if the AI or the network is unresponsive.

This was built from a detailed feature spec, then upgraded in place from
a v1 pass. Everything below maps to the spec section by section,
including the parts where the honest answer is "here's what's actually
possible" rather than an overclaim.

## v2 changes (production upgrade)

- **Model routing switched to `openrouter/free`** (OpenRouter's own Free
  Models Router), replacing the app's old custom "query `/models` and
  pick one" logic — see "OpenRouter model routing" below for why.
- **~1 request/second pacing** with `time.monotonic()`, never overlapping,
  never blindly `sleep(1)`.
- **Explicit task state machine** (IDLE/STARTING/THINKING/WORKING/
  WAITING/PAUSED/STOPPING/STOPPED/COMPLETED/ERROR) that makes duplicate
  START presses structurally impossible, not just UI-button-disabled.
- **Fixed the recursive screen-preview bug** by excluding this app's own
  windows from screen capture at the Windows API level.
- **Complete dark-theme UI redesign**: header status bar, task card, live
  desktop + current-action panels, an AI Cycle panel, a Request
  Statistics panel, and a categorized, color-coded activity log.
- **Network timeout tightened** (60s → 30s) so an in-flight request can't
  block emergency STOP for too long — see the honest limitation on this
  below.

## What's implemented

- **OpenRouter backend**, with a setup screen for the API key.
- **Key security**: stored only via `keyring` (Windows Credential
  Manager on Windows), shown only masked (`sk-or-••••••••••••1234`),
  never put in a prompt, never logged (a redaction filter scrubs any
  `sk-or-...` pattern from logs/crash reports before they're written),
  a "Remove API Key" button, and it's sent only in the `Authorization`
  header of requests straight to `api.openrouter.ai`.
- **OpenRouter model routing**: every request uses the model string
  `openrouter/free` — OpenRouter's own "Free Models Router," which picks
  a free model on OpenRouter's side that supports what the request needs
  (image input, tool calling), and can pick a different one on retry.
  The app never hand-selects a specific vendor's model. (v1 used to query
  `GET /models` and filter for free+vision models itself; that's how it
  could end up on a model like `thinkingmachines/inkling-small:free`,
  which turned out to be usable only inside approved agentic harnesses.
  Delegating to OpenRouter's router avoids that class of problem instead
  of working around it.)
- **~1 request/second pacing, never overlapping**: each cycle (request +
  action + re-screenshot) is timed with `time.monotonic()`; the loop
  waits only the leftover time before starting the next request, and
  never starts a new one while one is in flight. See
  `AgentLoop._pace_cycle()` in `core/agent_loop.py`.
- **One worker, guaranteed**: a `TaskStateMachine` (`core/task_state.py`)
  makes "already running" an atomic, race-free check — `AgentLoop.run()`
  itself refuses to start a second time, not just the Start button.
- **Computer control tools**: screenshot, mouse move, a *separate*
  AI-only cursor move, left/right/double click, drag, scroll, type text,
  press key, keyboard shortcuts, open application, switch window, wait —
  implemented as an iterative screenshot → decide → act → re-screenshot
  loop, exactly as specified.
- **Second AI cursor**: a borderless, click-through, always-on-top
  overlay window with its own icon + "AI" badge that animates to each
  target. See "Honest limitation" below for what this can and can't do
  relative to the single physical Windows mouse pointer.
- **Two-way voice**: 🎤 mic button using `speech_recognition`, and
  spoken status updates using `pyttsx3` (offline, via Windows SAPI5).
  Only short, user-facing status strings are ever spoken — never hidden
  model reasoning.
- **Main UI (redesigned, dark theme)**: header card (connection/model/
  voice/status pill + big STOP), task card (entry/mic/Start/Pause/STOP),
  a live-desktop preview card, a current-action card, an AI Cycle card
  (cycle count, last/next request, target rate), a Request Statistics
  card (requests/successful/failed/average time — all real, measured
  numbers, nothing invented), and a categorized, color-coded activity
  log (SYSTEM/AI/OBSERVE/ACTION/WAIT/WARNING/ERROR/STOP).
- **Fixed recursive screen-preview bug**: this app's own windows are
  excluded from screen capture via the Windows
  `SetWindowDisplayAffinity(..., WDA_EXCLUDEFROMCAPTURE)` API
  (`utils/capture_protection.py`), so the screenshot the AI sees — which
  is the same screenshot rendered into the "Live Desktop" panel — never
  contains this app's own window, eliminating the infinite-mirror effect
  at the source rather than papering over it.
- **Emergency STOP (F8)**: a global OS-level hotkey, independent of the
  agent loop and the model, that immediately flips a `threading.Event`
  the loop checks constantly, releases any held mouse buttons/keys,
  stops speech, hides the AI cursor, and returns control. The on-screen
  STOP button does the same thing. `pyautogui`'s own corner fail-safe is
  also left on as a second, independent kill-switch.
- **Pause/Resume**, preserving task state.
- **Confirmation system** for actions whose *stated* purpose looks risky
  (delete/purchase/install/send/run a command/etc.) — see the honesty
  note below on how this actually works.
- **Configurable limits**: max actions, max duration, max consecutive
  retries — the agent stops itself automatically, it doesn't retry
  forever.
- **Error handling** for invalid keys, network failures, rate limits,
  model unavailability, and tool-execution failures, always surfaced to
  the user with the option to STOP rather than looping silently.
- **Nothing implemented that was explicitly excluded**: no keylogging,
  no credential harvesting, no stealth/hidden operation, no UAC or
  antivirus bypassing. The agent is visible whenever it's active
  (overlay cursor + status bar) and the user can always take back
  control instantly.

## Honest limitations (please read)

- **Emergency STOP can't literally interrupt a request already in flight**
  on the socket — Python's `requests` call blocks until it returns or
  times out. What the app actually guarantees: the timeout is bounded
  (30s), the loop checks the stop flag the instant that call returns
  (before touching the response), and every *other* wait (cycle pacing,
  pause, rate-limit backoff) is interruptible in ~50ms slices. In
  practice STOP is near-instant except in the rare case it lands exactly
  during network I/O, where the worst case is bounded by the 30s timeout
  rather than unbounded.
- **Screen-capture exclusion needs Windows 10 version 2004+** (mid-2020
  or later). On an older Windows 10 build the OS silently falls back to
  the older `WDA_MONITOR` behavior (blacks the window out in captures
  instead of hiding it) — either way the recursion is gone, but on very
  old builds you'd see a black rectangle in that spot instead of a clean
  hole. On non-Windows platforms (e.g. reviewing this code on Linux/Mac)
  the exclusion call is a harmless no-op.
- **The Free Models Router picks at random each time**, so which
  underlying model answers a given request can change cycle to cycle —
  that's OpenRouter's design, not a bug here. The header shows the
  actual model OpenRouter used (from the response's `model` field) next
  to the fixed `openrouter/free` routing target.
- **API key extraction**: this is a local desktop app running under your
  own Windows account. The Windows Credential Manager protects the key
  from *other user accounts* and casual disk snooping, not from the
  machine's own administrator or from someone with debugging tools
  running as you. There's no way to make a client-side secret
  unextractable from a machine the user fully controls, and this app
  doesn't claim otherwise.
- **"Second cursor"**: Windows only has one hardware pointer position.
  The AI cursor is a real, separate, always-on-top overlay that can move
  around *without* touching your pointer (used for previewing an
  intended target), but to actually click or drag, Windows requires the
  real pointer to be at that location — so for click/drag actions the
  overlay animates there first and the real pointer follows to perform
  the click. That's what the code does; it does not pretend to run two
  independent hardware cursors at once.
- **Confirmation is a heuristic, not a guarantee.** The agent only ever
  knows screen coordinates and its own one-line description of an
  action. The confirmation system scans that description (and the tool
  name) for risk keywords ("delete", "buy", "install", "run command",
  ...). It's a real, useful safety net, but it cannot semantically know
  what a given click will do. Leave "Confirm important actions" ON
  unless you have a specific reason not to.
- **Voice recognition** uses SpeechRecognition's free Google Web Speech
  API by default, which needs internet and is a best-effort/rate-limited
  demo endpoint, not an SLA'd service. Swap `voice/speech_input.py`'s
  recognizer for an offline engine (e.g. Vosk) if you need that.
- **The global F8 hook and window-switching (`pywin32`) can be limited by
  Windows itself**: a lower-privileged process generally cannot send
  input to, or read the title of, a window running elevated
  (Administrator). If you need the agent to operate an elevated app, run
  this app elevated too — this is a Windows security boundary, not a bug
  here.
- **This code has not been run on a real Windows machine by me** (it was
  written and syntax-checked in a Linux sandbox that can't host a
  Windows GUI, a working mouse/keyboard driver, a microphone, or
  speakers). The architecture, control flow, and OpenRouter integration
  are complete and internally consistent, but plan on a debugging pass —
  especially around `pyautogui`/`pywin32`/`pyttsx3` install quirks — the
  first time you run it for real.

## Project layout

```
AIComputerAgent/
  main.py                     # entry point
  requirements.txt
  config/
    settings.py              # limits, pacing, endpoints, keywords (no secrets)
    theme.py                 # dark-theme colors/fonts/spacing tokens
  core/
    api_key_manager.py        # secure key storage + masking + redaction
    openrouter_client.py      # openrouter/free chat calls + connection check
    tools_schema.py           # function-calling schema sent to the model
    tools_executor.py         # actually moves the mouse/keyboard, opens apps
    agent_loop.py             # the see -> decide -> act -> observe loop, paced
    task_state.py             # IDLE/.../ERROR state machine (no duplicate workers)
    stats.py                  # request statistics (real numbers only)
    safety.py                 # emergency stop, pause, limits
    confirmation.py           # risky-action heuristic
  ui/
    main_window.py            # the whole Tkinter UI (v2, dark theme)
    widgets.py                # RoundedCard / ToggleSwitch / StatusPill helpers
    ai_cursor_overlay.py      # the visible AI cursor (also capture-excluded)
  voice/
    speech_input.py           # microphone -> text
    speech_output.py          # text -> speech
  utils/
    logger.py                 # categorized activity log with key redaction
    hotkey_listener.py        # global F8 listener
    capture_protection.py     # excludes this app's windows from screen capture
  build/
    AIComputerAgent.spec      # PyInstaller build spec
    build.bat                 # one-command Windows build script
  .github/workflows/
    build-windows-exe.yml     # optional: build the exe on GitHub's CI instead
```

## Getting an OpenRouter API key

1. Create an account at https://openrouter.ai
2. Go to https://openrouter.ai/keys and create a new key (starts with
   `sk-or-`).
3. Paste it into this app's Settings dialog and click "Save key."

## Running from source (Windows, Python 3.10+)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Notes:
- If `pip install pyaudio` fails (a common Windows issue because it needs
  a compiled extension), try:
  `pip install pipwin && pipwin install pyaudio`
  or install a prebuilt wheel from
  https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio
  Voice *input* just won't be available until this is resolved; nothing
  else in the app depends on it.
- `pywin32` enables precise window-switching and the active-window
  readout; without it, `switch_window` falls back to a generic Alt+Tab
  and the active-window label just says so.
- The `keyboard` package's global hotkey hook occasionally wants to run
  from an Administrator prompt depending on what else is running on your
  machine; if F8 doesn't register, try running `python main.py` (or the
  built `.exe`) as Administrator. The on-screen STOP button always works
  regardless.

## Building AIComputerAgent.exe

This sandbox that produced this code runs Linux and cannot compile a
native Windows binary — PyInstaller has to run **on Windows** (or on
GitHub's Windows CI runner) to produce a `.exe`. Two ways to get one:

**Option A — build locally on Windows:**
```bat
build\build.bat
```
This creates a virtual environment, installs dependencies, and runs
PyInstaller. The result is `dist\AIComputerAgent\AIComputerAgent.exe`.
(For a single-file exe instead of a folder, open
`build/AIComputerAgent.spec` and pass `onefile=True` to `EXE(...)`
instead of using `COLLECT(...)`; single-file exes start a bit slower.)

**Option B — build via GitHub Actions (no local Windows machine needed):**
1. Push this project to a GitHub repo.
2. Open the "Actions" tab → "Build Windows exe" → "Run workflow" (or just
   push to `main`, since the workflow also runs automatically then).
3. Download the `AIComputerAgent-windows` artifact when it finishes —
   that's your built app.

## Safety controls at a glance

| Control | Where | What it does |
|---|---|---|
| F8 | anywhere, global | Immediate emergency stop |
| STOP button | top bar & task area | Same emergency stop |
| Pause / Resume | task area | Freezes/unfreezes the loop, keeps task state |
| Confirm important actions | Settings | Heuristic pause-and-ask before risky-sounding actions |
| Max actions / duration / retries | Settings | Auto-stops a runaway or stuck task |
| pyautogui corner fail-safe | always on | Slam the real mouse into a screen corner to abort |
| Task state machine | always on | Makes a second concurrent worker structurally impossible |

## License

No license file is included; add one appropriate for your use before
distributing this further.
