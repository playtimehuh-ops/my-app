"""
Main application window - redesigned dark UI (v2).

This module is the only place that talks to Tkinter directly on behalf of
the rest of the app; the agent loop, OpenRouter client, executor, safety
controller, and voice modules are all UI-agnostic. Every callback that can
fire from a background thread (the agent loop thread, the hotkey-hook
thread, the speech-recognition thread) is marshalled onto the Tk main
thread via `root.after(...)` before it touches any widget.

Duplicate-worker prevention is layered:
  1. The Start button disables itself immediately on click.
  2. `TaskStateMachine.try_begin()` is an atomic compare-and-swap checked
     both here (for instant UI feedback) and again inside AgentLoop.run()
     itself (the real, race-free guard - see core/agent_loop.py).
"""

import platform
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, messagebox

from PIL import ImageTk

from config import theme
from config.settings import APP_NAME, EMERGENCY_STOP_HOTKEY, MIN_CYCLE_SECONDS, FREE_MODELS_ROUTER_MODEL
from core.agent_loop import AgentCallbacks, AgentLoop, DuplicateWorkerError
from core.api_key_manager import ApiKeyManager
from core.openrouter_client import OpenRouterClient, OpenRouterError
from core.safety import SafetyController
from core.stats import RequestStats
from core.task_state import TaskState, TaskStateMachine
from core.tools_executor import ToolExecutor
from ui.ai_cursor_overlay import AICursorOverlay
from ui.widgets import RoundedCard, SectionLabel, StatusPill, ToggleSwitch, stat_block
from utils.capture_protection import exclude_from_capture, IS_WINDOWS
from utils.hotkey_listener import HotkeyListener
from utils.logger import ActivityLogger
from voice.speech_input import SpeechInput
from voice.speech_output import SpeechOutput

try:
    import win32gui
except Exception:  # pragma: no cover
    win32gui = None


STATE_COLORS = {
    TaskState.IDLE: theme.TEXT_SECONDARY,
    TaskState.STARTING: theme.ACCENT,
    TaskState.THINKING: theme.ACCENT,
    TaskState.WORKING: theme.SUCCESS,
    TaskState.WAITING: theme.WARNING,
    TaskState.PAUSED: theme.WARNING,
    TaskState.STOPPING: theme.WARNING,
    TaskState.STOPPED: theme.TEXT_SECONDARY,
    TaskState.COMPLETED: theme.SUCCESS,
    TaskState.ERROR: theme.DANGER,
}

AT_REST_STATES = {TaskState.IDLE, TaskState.STOPPED, TaskState.COMPLETED, TaskState.ERROR}


class MainWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.configure(bg=theme.BG)
        self.root.geometry("1180x820")
        self.root.minsize(980, 680)

        # --- core services -------------------------------------------------
        self.key_manager = ApiKeyManager()
        self.client = OpenRouterClient(self.key_manager)
        self.logger = ActivityLogger(
            on_line=lambda line: self.root.after(0, lambda l=line: self._append_log_line(l))
        )
        self.safety = SafetyController()
        self.state = TaskStateMachine()
        self.stats = RequestStats()
        self.speech_out = SpeechOutput()
        self.speech_in = SpeechInput()
        self.cursor_overlay = AICursorOverlay(root)
        self.hotkey = HotkeyListener(self._emergency_stop_from_hotkey)

        self._executor = None
        self._executor_error = None
        try:
            self._executor = ToolExecutor(cursor_hook=self._cursor_hook_threadsafe)
        except Exception as e:  # e.g. not running on Windows / pyautogui missing
            self._executor_error = str(e)

        self._agent_thread = None
        self._active_confirm_dialog = None
        self._actual_model_seen = None
        self._next_request_deadline = None  # monotonic seconds, or None
        self._last_screen_photo = None  # keep a reference so Tk doesn't GC it

        self._build_ui()
        self._refresh_key_status()

        hotkey_ok = self.hotkey.start()
        if not hotkey_ok:
            self.logger.log(
                f"Could not register the global {EMERGENCY_STOP_HOTKEY.upper()} hotkey "
                "(the 'keyboard' package may be missing, or this isn't Windows). The "
                "on-screen STOP button still works.",
                "WARNING",
            )
        if self._executor_error:
            self.logger.log(f"Computer control unavailable: {self._executor_error}", "WARNING")
        if not IS_WINDOWS:
            self.logger.log(
                "Screen-capture self-exclusion (fixes the recursive preview) requires "
                "Windows 10 2004+ - skipped on this platform.", "WARNING",
            )

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._apply_capture_protection_when_ready()
        self._poll_active_window()
        self._poll_stats_and_countdown()
        self._set_idle_ui()

    # ================================================================== #
    # UI construction
    # ================================================================== #
    def _build_ui(self):
        outer = tk.Frame(self.root, bg=theme.BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=theme.PAD, pady=theme.PAD)

        self._build_header(outer)
        self._build_task_card(outer)

        content = tk.Frame(outer, bg=theme.BG)
        content.pack(fill=tk.BOTH, expand=True, pady=(theme.PAD, theme.PAD))
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(0, weight=1)

        left = tk.Frame(content, bg=theme.BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, theme.PAD))
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        right = tk.Frame(content, bg=theme.BG)
        right.grid(row=0, column=1, sticky="nsew")

        self._build_screen_card(left)
        self._build_current_action_card(left)
        self._build_cycle_card(right)
        self._build_stats_card(right)

        self._build_log_card(outer)

    def _build_header(self, parent):
        card = RoundedCard(parent, bg=theme.BG_PANEL)
        card.pack(fill=tk.X, pady=(0, theme.PAD))
        bar = tk.Frame(card.body, bg=theme.BG_PANEL)
        bar.pack(fill=tk.X, padx=theme.PAD, pady=theme.PAD_SM)

        tk.Label(bar, text=APP_NAME, bg=theme.BG_PANEL, fg=theme.TEXT_PRIMARY,
                 font=theme.FONT_TITLE).pack(side=tk.LEFT)

        info = tk.Frame(bar, bg=theme.BG_PANEL)
        info.pack(side=tk.LEFT, padx=(theme.PAD * 2, 0))

        self.conn_pill = StatusPill(info, "Checking OpenRouter...", theme.TEXT_SECONDARY)
        self.conn_pill.pack(anchor="w")

        model_row = tk.Frame(info, bg=theme.BG_PANEL)
        model_row.pack(anchor="w")
        tk.Label(model_row, text=f"Model: {FREE_MODELS_ROUTER_MODEL}", bg=theme.BG_PANEL,
                 fg=theme.TEXT_SECONDARY, font=theme.FONT_SMALL).pack(side=tk.LEFT)
        self.actual_model_label = tk.Label(model_row, text="", bg=theme.BG_PANEL,
                                            fg=theme.TEXT_MUTED, font=theme.FONT_SMALL)
        self.actual_model_label.pack(side=tk.LEFT, padx=(6, 0))

        self.voice_status_label = tk.Label(info, text="Voice: ON", bg=theme.BG_PANEL,
                                            fg=theme.TEXT_SECONDARY, font=theme.FONT_SMALL)
        self.voice_status_label.pack(anchor="w")

        right_box = tk.Frame(bar, bg=theme.BG_PANEL)
        right_box.pack(side=tk.RIGHT)

        self.stop_button_top = tk.Button(
            right_box, text="\u25A0  STOP", command=self._emergency_stop_from_ui,
            bg=theme.DANGER, fg="white", activebackground=theme.DANGER_DARK,
            activeforeground="white", font=(theme.FONT_FAMILY, 12, "bold"),
            relief=tk.FLAT, bd=0, padx=18, pady=8, cursor="hand2",
        )
        self.stop_button_top.pack(side=tk.RIGHT)

        self.status_pill = StatusPill(right_box, "IDLE", STATE_COLORS[TaskState.IDLE])
        self.status_pill.configure(font=(theme.FONT_FAMILY, 11, "bold"))
        self.status_pill.pack(side=tk.RIGHT, padx=(0, theme.PAD * 2))

        settings_btn = tk.Button(
            right_box, text="Settings", command=self._open_settings,
            bg=theme.BG_INPUT, fg=theme.TEXT_PRIMARY, activebackground=theme.BORDER,
            activeforeground=theme.TEXT_PRIMARY, relief=tk.FLAT, bd=0,
            font=theme.FONT_BODY, padx=12, pady=6, cursor="hand2",
        )
        settings_btn.pack(side=tk.RIGHT, padx=(0, theme.PAD))

    def _build_task_card(self, parent):
        card = RoundedCard(parent, bg=theme.BG_PANEL)
        card.pack(fill=tk.X)
        pad = tk.Frame(card.body, bg=theme.BG_PANEL)
        pad.pack(fill=tk.X, padx=theme.PAD, pady=theme.PAD)

        SectionLabel(pad, "Task").pack(anchor="w", pady=(0, 6))

        row = tk.Frame(pad, bg=theme.BG_PANEL)
        row.pack(fill=tk.X)

        self.task_entry = tk.Entry(
            row, bg=theme.BG_INPUT, fg=theme.TEXT_PRIMARY, insertbackground=theme.TEXT_PRIMARY,
            relief=tk.FLAT, font=theme.FONT_BODY, highlightthickness=1,
            highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
        )
        self.task_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, ipadx=6)
        self.task_entry.bind("<Return>", lambda e: self._on_start())

        self.mic_button = self._make_button(row, "\U0001F3A4", self._on_mic_clicked, width=3)
        self.mic_button.pack(side=tk.LEFT, padx=(8, 0))

        btn_row = tk.Frame(pad, bg=theme.BG_PANEL)
        btn_row.pack(fill=tk.X, pady=(10, 0))

        self.start_button = self._make_button(btn_row, "START", self._on_start, accent=True)
        self.start_button.pack(side=tk.LEFT)

        self.pause_button = self._make_button(btn_row, "PAUSE", self._on_pause_resume)
        self.pause_button.pack(side=tk.LEFT, padx=(8, 0))
        self.pause_button.configure(state=tk.DISABLED)

        self.stop_button_task = tk.Button(
            btn_row, text="STOP", command=self._emergency_stop_from_ui,
            bg=theme.DANGER, fg="white", activebackground=theme.DANGER_DARK,
            activeforeground="white", font=theme.FONT_SMALL_BOLD, relief=tk.FLAT,
            bd=0, padx=14, pady=6, cursor="hand2",
        )
        self.stop_button_task.pack(side=tk.LEFT, padx=(8, 0))

    def _build_screen_card(self, parent):
        card = RoundedCard(parent, bg=theme.BG_PANEL)
        card.grid(row=0, column=0, sticky="nsew", pady=(0, theme.PAD))
        pad = tk.Frame(card.body, bg=theme.BG_PANEL)
        pad.pack(fill=tk.BOTH, expand=True, padx=theme.PAD, pady=theme.PAD)

        header = tk.Frame(pad, bg=theme.BG_PANEL)
        header.pack(fill=tk.X)
        SectionLabel(header, "Live Desktop").pack(side=tk.LEFT)
        self.live_indicator = StatusPill(header, "IDLE", theme.TEXT_MUTED)
        self.live_indicator.configure(font=theme.FONT_SMALL_BOLD)
        self.live_indicator.pack(side=tk.RIGHT)

        self.screen_label = tk.Label(pad, bg=theme.BG_PANEL_ALT, text="No screenshot yet",
                                      fg=theme.TEXT_MUTED, font=theme.FONT_BODY)
        self.screen_label.pack(fill=tk.BOTH, expand=True, pady=(8, 4))

        self.last_capture_label = tk.Label(pad, text="Last capture: -", bg=theme.BG_PANEL,
                                            fg=theme.TEXT_MUTED, font=theme.FONT_SMALL)
        self.last_capture_label.pack(anchor="w")

    def _build_current_action_card(self, parent):
        card = RoundedCard(parent, bg=theme.BG_PANEL)
        card.grid(row=1, column=0, sticky="nsew")
        pad = tk.Frame(card.body, bg=theme.BG_PANEL)
        pad.pack(fill=tk.BOTH, expand=True, padx=theme.PAD, pady=theme.PAD)

        SectionLabel(pad, "Current Action").pack(anchor="w")
        self.current_action_label = tk.Label(
            pad, text="READY", bg=theme.BG_PANEL, fg=theme.ACCENT_TEXT,
            font=(theme.FONT_FAMILY, 16, "bold"), anchor="w",
        )
        self.current_action_label.pack(fill=tk.X, pady=(6, 2))
        self.current_action_detail = tk.Label(
            pad, text="Type a task above and press Start.", bg=theme.BG_PANEL,
            fg=theme.TEXT_SECONDARY, font=theme.FONT_BODY, anchor="w", justify=tk.LEFT,
            wraplength=520,
        )
        self.current_action_detail.pack(fill=tk.X)

    def _build_cycle_card(self, parent):
        card = RoundedCard(parent, bg=theme.BG_PANEL)
        card.pack(fill=tk.X, pady=(0, theme.PAD))
        pad = tk.Frame(card.body, bg=theme.BG_PANEL)
        pad.pack(fill=tk.X, padx=theme.PAD, pady=theme.PAD)

        SectionLabel(pad, "AI Cycle").pack(anchor="w", pady=(0, 6))
        grid = tk.Frame(pad, bg=theme.BG_PANEL)
        grid.pack(fill=tk.X)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        f, self.cycle_value = stat_block(grid, "Cycle")
        f.grid(row=0, column=0, sticky="w", pady=4)
        f, self.last_request_value = stat_block(grid, "Last request")
        f.grid(row=0, column=1, sticky="w", pady=4)
        f, self.next_request_value = stat_block(grid, "Next request")
        f.grid(row=1, column=0, sticky="w", pady=4)
        f, self.rate_value = stat_block(grid, "Request rate")
        f.grid(row=1, column=1, sticky="w", pady=4)
        self.rate_value.configure(font=theme.FONT_SECTION)
        self.rate_value.configure(text=f"~1/{MIN_CYCLE_SECONDS:.0f}s")

    def _build_stats_card(self, parent):
        card = RoundedCard(parent, bg=theme.BG_PANEL)
        card.pack(fill=tk.BOTH, expand=True)
        pad = tk.Frame(card.body, bg=theme.BG_PANEL)
        pad.pack(fill=tk.BOTH, expand=True, padx=theme.PAD, pady=theme.PAD)

        SectionLabel(pad, "Request Statistics").pack(anchor="w", pady=(0, 6))
        grid = tk.Frame(pad, bg=theme.BG_PANEL)
        grid.pack(fill=tk.X)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        f, self.stat_total = stat_block(grid, "Requests")
        f.grid(row=0, column=0, sticky="w", pady=4)
        f, self.stat_successful = stat_block(grid, "Successful")
        f.grid(row=0, column=1, sticky="w", pady=4)
        f, self.stat_failed = stat_block(grid, "Failed")
        f.grid(row=1, column=0, sticky="w", pady=4)
        f, self.stat_cycle = stat_block(grid, "Current cycle")
        f.grid(row=1, column=1, sticky="w", pady=4)
        f, self.stat_avg = stat_block(grid, "Average request time")
        f.grid(row=2, column=0, sticky="w", pady=4, columnspan=2)

    def _build_log_card(self, parent):
        card = RoundedCard(parent, bg=theme.BG_PANEL)
        card.pack(fill=tk.BOTH, expand=False)
        pad = tk.Frame(card.body, bg=theme.BG_PANEL)
        pad.pack(fill=tk.BOTH, expand=True, padx=theme.PAD, pady=theme.PAD)

        SectionLabel(pad, "Activity Log").pack(anchor="w", pady=(0, 6))

        self.log_text = scrolledtext.ScrolledText(
            pad, height=10, state=tk.DISABLED, font=theme.FONT_MONO, wrap=tk.WORD,
            bg=theme.BG_PANEL_ALT, fg=theme.TEXT_PRIMARY, insertbackground=theme.TEXT_PRIMARY,
            relief=tk.FLAT, borderwidth=0,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        for category, color in theme.LOG_COLORS.items():
            self.log_text.tag_configure(category, foreground=color)
        self.log_text.tag_configure("_ts", foreground=theme.TEXT_MUTED)

    # ------------------------------------------------------------------ #
    def _make_button(self, parent, text, command, accent=False, width=None):
        bg = theme.ACCENT if accent else theme.BG_INPUT
        fg = "white" if accent else theme.TEXT_PRIMARY
        active_bg = "#4472DB" if accent else theme.BORDER
        kwargs = dict(
            text=text, command=command, bg=bg, fg=fg, activebackground=active_bg,
            activeforeground=fg, relief=tk.FLAT, bd=0, font=theme.FONT_SMALL_BOLD,
            padx=16, pady=8, cursor="hand2",
        )
        if width:
            kwargs["width"] = width
        return tk.Button(parent, **kwargs)

    # ================================================================== #
    # Startup helpers
    # ================================================================== #
    def _set_idle_ui(self):
        self._apply_status(TaskState.IDLE)
        self.current_action_label.configure(text="READY")
        self.current_action_detail.configure(text="Type a task above and press Start.")
        self.live_indicator.set("IDLE", theme.TEXT_MUTED)
        self.next_request_value.configure(text="Stopped")

    def _apply_capture_protection_when_ready(self):
        def apply():
            ok = exclude_from_capture(self.root)
            if IS_WINDOWS and not ok:
                self.logger.log(
                    "Could not exclude this window from screen capture (needs "
                    "Windows 10 2004+). The screen preview may show itself recursively.",
                    "WARNING",
                )
        self.root.after(250, apply)

    # ================================================================== #
    # Logging / status
    # ================================================================== #
    def _append_log_line(self, line):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{line.timestamp}  ", "_ts")
        self.log_text.insert(tk.END, f"{line.category:<8} ", line.category)
        self.log_text.insert(tk.END, f"{line.message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _apply_status(self, state: TaskState):
        color = STATE_COLORS.get(state, theme.TEXT_SECONDARY)
        self.status_pill.set(state.value, color)

    # ================================================================== #
    # Agent callback plumbing (may be called from the AGENT thread)
    # ================================================================== #
    def _handle_status(self, state: TaskState):
        self.root.after(0, lambda s=state: self._apply_status(s))
        is_ended = state in (TaskState.STOPPED, TaskState.COMPLETED, TaskState.ERROR)
        self.root.after(0, lambda: self.live_indicator.set(
            "LIVE" if not is_ended else "IDLE",
            theme.SUCCESS if not is_ended else theme.TEXT_MUTED,
        ))
        if is_ended:
            self.root.after(0, self._on_task_ended)

    def _handle_current_action(self, label: str, detail: str):
        self.root.after(0, lambda: self.current_action_label.configure(text=label))
        self.root.after(0, lambda: self.current_action_detail.configure(text=detail or " "))

    def _handle_log(self, message: str, category: str):
        self.logger.log(message, category)

    def _handle_screenshot(self, pil_image):
        self.root.after(0, lambda img=pil_image: self._update_screen_image(img))
        self.root.after(0, lambda: self.last_capture_label.configure(
            text=f"Last capture: {time.strftime('%H:%M:%S')}"))

    def _handle_model_changed(self, model_id: str):
        if model_id == FREE_MODELS_ROUTER_MODEL:
            return
        self._actual_model_seen = model_id
        self.root.after(0, lambda m=model_id: self.actual_model_label.configure(text=f"(via {m})"))

    def _handle_next_request_eta(self, deadline):
        self._next_request_deadline = deadline

    def _cursor_hook_threadsafe(self, x, y, style):
        self.root.after(0, lambda: self._apply_cursor_hook(x, y, style))

    def _apply_cursor_hook(self, x, y, style):
        self.cursor_overlay.move_to(x, y, animate=True)
        if style == "click":
            self.cursor_overlay.animate_click()
        elif style == "drag_start":
            self.cursor_overlay.animate_drag()
        elif style == "drag_end":
            self.cursor_overlay.end_drag()

    def _update_screen_image(self, pil_image):
        frame_w = max(200, self.screen_label.winfo_width())
        frame_h = max(150, self.screen_label.winfo_height())
        img = pil_image.copy()
        img.thumbnail((frame_w, frame_h))
        photo = ImageTk.PhotoImage(img)
        self._last_screen_photo = photo  # prevent garbage collection
        self.screen_label.configure(image=photo, text="")

    def _request_confirmation(self, tool_name: str, reason: str, args: dict) -> bool:
        """Called from the AGENT thread. Blocks until the user answers (or
        an emergency stop cancels the wait)."""
        event = threading.Event()
        result = {"approved": False}

        def on_answer(approved: bool):
            result["approved"] = approved
            event.set()

        def show_dialog():
            self._active_confirm_dialog = ConfirmDialog(self.root, tool_name, reason, on_answer)

        self.root.after(0, show_dialog)

        while not event.wait(timeout=0.2):
            if self.safety.is_stopped():
                break
        self._active_confirm_dialog = None
        return result["approved"]

    def _force_close_confirm_dialog(self):
        if self._active_confirm_dialog is not None:
            try:
                self._active_confirm_dialog.force_close()
            except Exception:
                pass
            self._active_confirm_dialog = None

    # ================================================================== #
    # Periodic UI polling (stats / countdown) - purely informational, never
    # triggers any request itself.
    # ================================================================== #
    def _poll_stats_and_countdown(self):
        snap = self.stats.snapshot()
        self.cycle_value.configure(text=str(snap["cycle"]))
        self.last_request_value.configure(text=snap["last_request_at"] or "-")
        self.stat_total.configure(text=str(snap["total"]))
        self.stat_successful.configure(text=str(snap["successful"]))
        self.stat_failed.configure(text=str(snap["failed"]))
        self.stat_cycle.configure(text=str(snap["cycle"]))
        self.stat_avg.configure(
            text=f"{snap['avg_request_seconds']:.2f}s" if snap["total"] else "-"
        )

        if self._next_request_deadline is None:
            self.next_request_value.configure(
                text="Stopped" if self.state.get() in AT_REST_STATES else "-"
            )
        else:
            remaining = max(0.0, self._next_request_deadline - time.monotonic())
            self.next_request_value.configure(text=f"{remaining:.1f}s")

        self.root.after(150, self._poll_stats_and_countdown)

    def _poll_active_window(self):
        if win32gui is not None:
            try:
                hwnd = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(hwnd) or "(untitled)"
            except Exception:
                title = "(unavailable)"
        else:
            title = "(pywin32 not installed)"
        # Currently surfaced only internally, not a dedicated header field
        # in this layout - kept cheap here to avoid visual clutter per the
        # "don't clutter with tiny controls" guidance.
        self._active_window_title = title
        self.root.after(2000, self._poll_active_window)

    # ================================================================== #
    # Task lifecycle
    # ================================================================== #
    def _on_start(self):
        if self.state.get() not in AT_REST_STATES:
            return  # already running - ignore extra clicks (belt & suspenders)
        if self._executor is None:
            messagebox.showerror(
                APP_NAME,
                "Computer control is unavailable on this system:\n\n"
                f"{self._executor_error}\n\n"
                "This application must run on Windows with its dependencies "
                "installed (see README.md).",
            )
            return
        if not self.key_manager.has_key():
            messagebox.showwarning(
                APP_NAME, "Please add your OpenRouter API key in Settings first."
            )
            self._open_settings()
            return

        task_text = self.task_entry.get().strip()
        if not task_text:
            messagebox.showinfo(APP_NAME, "Type or say what you'd like the AI to do.")
            return

        self.safety.clear_stop()
        self.cursor_overlay.show()
        self.start_button.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.NORMAL, text="PAUSE")
        self.task_entry.config(state=tk.DISABLED)

        callbacks = AgentCallbacks(
            on_log=self._handle_log,
            on_status=self._handle_status,
            on_current_action=self._handle_current_action,
            on_screenshot=self._handle_screenshot,
            on_speak=self._speak_if_enabled,
            on_model_changed=self._handle_model_changed,
            on_next_request_eta=self._handle_next_request_eta,
            request_confirmation=self._request_confirmation,
        )
        agent = AgentLoop(self.client, self._executor, self.safety, self.state, self.stats, callbacks)

        def worker():
            try:
                agent.run(task_text)
            except DuplicateWorkerError:
                return  # another worker already owns the UI state - don't touch it
            except Exception as e:  # noqa: BLE001 - last-resort safety net
                self.logger.log(f"Unexpected error, stopping: {e}", "ERROR")
            # Reached after a normal return OR a caught Exception (not after
            # DuplicateWorkerError, which returns above). Belt-and-suspenders:
            # whatever happened inside run(), force the state machine and the
            # UI back to a usable, restartable state. This is what actually
            # fixes "it stopped working after an error and Start did nothing" -
            # previously that recovery depended on every callback along the
            # way succeeding.
            if self.state.get() not in AT_REST_STATES:
                self.logger.log("Recovered from an inconsistent state after the task ended.", "WARNING")
                self.state.set(TaskState.ERROR)
            self.root.after(0, self._on_task_ended)

        self._agent_thread = threading.Thread(target=worker, daemon=True)
        self._agent_thread.start()

    def _speak_if_enabled(self, text: str):
        self.speech_out.speak(text)

    def _on_task_ended(self):
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED, text="PAUSE")
        self.task_entry.config(state=tk.NORMAL)
        self.cursor_overlay.hide()

    def _on_pause_resume(self):
        if self.state.get() not in (TaskState.WORKING, TaskState.THINKING, TaskState.WAITING, TaskState.PAUSED):
            return
        if not self.safety.is_paused():
            self.safety.pause()
            self.pause_button.config(text="RESUME")
            self.logger.log("Task paused by user.", "SYSTEM")
        else:
            self.safety.resume()
            self.pause_button.config(text="PAUSE")
            self.logger.log("Task resumed by user.", "SYSTEM")

    # ================================================================== #
    # Emergency stop
    # ================================================================== #
    def _emergency_stop_from_ui(self):
        self._trigger_emergency_stop("STOP button pressed")

    def _emergency_stop_from_hotkey(self):
        # NOTE: runs on the keyboard-hook thread, not the Tk thread.
        self._trigger_emergency_stop(f"{EMERGENCY_STOP_HOTKEY.upper()} hotkey pressed")

    def _trigger_emergency_stop(self, source: str):
        self.state.set(TaskState.STOPPING)
        self.safety.trigger_emergency_stop()
        if self._executor is not None:
            self._executor.release_all_inputs()
        self.speech_out.stop()
        self.logger.log(f"Emergency stop \u2014 {source}", "STOP")
        self.root.after(0, self._apply_emergency_stop_ui)

    def _apply_emergency_stop_ui(self):
        self.cursor_overlay.hide()
        self._force_close_confirm_dialog()
        self._on_task_ended()
        self.state.set(TaskState.STOPPED)
        self._apply_status(TaskState.STOPPED)
        self.current_action_label.configure(text="STOPPED")
        self.current_action_detail.configure(text="Emergency stop was triggered.")
        self.live_indicator.set("IDLE", theme.TEXT_MUTED)
        self._next_request_deadline = None

    # ================================================================== #
    # Voice input
    # ================================================================== #
    def _on_mic_clicked(self):
        if not self.speech_in.is_available():
            messagebox.showerror(
                APP_NAME,
                "Voice input isn't available: install 'SpeechRecognition' and "
                "'PyAudio' (see README.md).",
            )
            return
        if self.speech_in.is_listening():
            return

        self.mic_button.config(state=tk.DISABLED)
        self.voice_status_label.config(text="Voice: Listening...")

        def on_status(msg):
            self.root.after(0, lambda: self.voice_status_label.config(text=f"Voice: {msg}"))

        def on_result(text):
            def apply():
                self.task_entry.delete(0, tk.END)
                self.task_entry.insert(0, text)
                self.mic_button.config(state=tk.NORMAL)
                self.voice_status_label.config(text="Voice: ON")
            self.root.after(0, apply)

        def on_error(msg):
            def apply():
                self.mic_button.config(state=tk.NORMAL)
                self.voice_status_label.config(text="Voice: ON")
                messagebox.showwarning(APP_NAME, f"Voice input: {msg}")
            self.root.after(0, apply)

        self.speech_in.listen_once_async(on_result, on_error, on_status)

    # ================================================================== #
    # Settings / API key
    # ================================================================== #
    def _refresh_key_status(self):
        if self.key_manager.has_key():
            masked = self.key_manager.mask_key(self.key_manager.get_key())
            self.conn_pill.set(f"Key set ({masked})", theme.TEXT_SECONDARY)
            threading.Thread(target=self._verify_key_async, daemon=True).start()
        else:
            self.conn_pill.set("No API key", theme.TEXT_MUTED)

    def _verify_key_async(self):
        try:
            ok = self.client.verify_key()
        except OpenRouterError as e:
            ok = False
            self.logger.log(f"Could not verify OpenRouter connection: {e}", "WARNING")
        text = "OpenRouter Connected" if ok else "OpenRouter: invalid key"
        color = theme.SUCCESS if ok else theme.DANGER
        self.root.after(0, lambda: self.conn_pill.set(text, color))

    def _open_settings(self):
        SettingsDialog(self.root, self)

    # ================================================================== #
    # Misc / lifecycle
    # ================================================================== #
    def _on_close(self):
        try:
            self.safety.trigger_emergency_stop()
            if self._executor is not None:
                self._executor.release_all_inputs()
            self.hotkey.stop()
            self.speech_out.stop()
            self.cursor_overlay.destroy()
            self.client.close()
        finally:
            self.root.destroy()


class ConfirmDialog:
    def __init__(self, root, tool_name, reason, on_answer):
        self.on_answer = on_answer
        self.top = tk.Toplevel(root)
        self.top.title("Confirm action")
        self.top.configure(bg=theme.BG_PANEL)
        self.top.attributes("-topmost", True)
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", lambda: self._answer(False))

        pad = tk.Frame(self.top, bg=theme.BG_PANEL, padx=18, pady=16)
        pad.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            pad, text="The AI wants to perform an important action:",
            bg=theme.BG_PANEL, fg=theme.TEXT_PRIMARY, font=theme.FONT_SECTION,
        ).pack(anchor=tk.W)
        tk.Label(pad, text=reason, bg=theme.BG_PANEL, fg=theme.TEXT_PRIMARY,
                 font=theme.FONT_BODY, wraplength=380, justify=tk.LEFT).pack(anchor=tk.W, pady=(6, 2))
        tk.Label(pad, text=f"(tool: {tool_name})", bg=theme.BG_PANEL,
                 fg=theme.TEXT_MUTED, font=theme.FONT_SMALL).pack(anchor=tk.W)

        btns = tk.Frame(pad, bg=theme.BG_PANEL)
        btns.pack(pady=(16, 0), fill=tk.X)
        tk.Button(btns, text="Skip this action", command=lambda: self._answer(False),
                  bg=theme.BG_INPUT, fg=theme.TEXT_PRIMARY, relief=tk.FLAT, bd=0,
                  padx=12, pady=6, font=theme.FONT_SMALL_BOLD, cursor="hand2").pack(side=tk.RIGHT)
        tk.Button(btns, text="Allow", command=lambda: self._answer(True),
                  bg=theme.ACCENT, fg="white", relief=tk.FLAT, bd=0,
                  padx=14, pady=6, font=theme.FONT_SMALL_BOLD, cursor="hand2").pack(side=tk.RIGHT, padx=(0, 8))

        self.top.grab_set()

    def _answer(self, approved: bool):
        try:
            self.top.grab_release()
            self.top.destroy()
        except Exception:
            pass
        self.on_answer(approved)

    def force_close(self):
        try:
            self.top.grab_release()
            self.top.destroy()
        except Exception:
            pass


class SettingsDialog:
    def __init__(self, root, main_window: "MainWindow"):
        self.mw = main_window
        self.top = tk.Toplevel(root)
        self.top.title("Settings")
        self.top.configure(bg=theme.BG)
        self.top.resizable(False, False)
        self.top.transient(root)

        pad = tk.Frame(self.top, bg=theme.BG, padx=14, pady=14)
        pad.pack(fill=tk.BOTH, expand=True)

        self._build_key_section(pad)
        self._build_safety_section(pad)
        self._build_voice_section(pad)

        tk.Button(
            pad, text="Close", command=self.top.destroy, bg=theme.BG_INPUT,
            fg=theme.TEXT_PRIMARY, relief=tk.FLAT, bd=0, padx=14, pady=6,
            font=theme.FONT_SMALL_BOLD, cursor="hand2",
        ).pack(anchor=tk.E, pady=(4, 0))

    def _section_card(self, parent, title):
        card = RoundedCard(parent, bg=theme.BG_PANEL)
        card.pack(fill=tk.X, pady=(0, 10))
        inner = tk.Frame(card.body, bg=theme.BG_PANEL, padx=12, pady=12)
        inner.pack(fill=tk.BOTH, expand=True)
        SectionLabel(inner, title).pack(anchor="w", pady=(0, 8))
        return inner

    def _build_key_section(self, parent):
        inner = self._section_card(parent, "OpenRouter API key")
        current = self.mw.key_manager.mask_key(self.mw.key_manager.get_key())
        self.current_key_label = tk.Label(inner, text=f"Current: {current}", bg=theme.BG_PANEL,
                                           fg=theme.TEXT_SECONDARY, font=theme.FONT_BODY)
        self.current_key_label.pack(anchor=tk.W)

        entry_row = tk.Frame(inner, bg=theme.BG_PANEL)
        entry_row.pack(fill=tk.X, pady=(8, 0))
        tk.Label(entry_row, text="New key:", bg=theme.BG_PANEL, fg=theme.TEXT_SECONDARY,
                 font=theme.FONT_BODY).pack(side=tk.LEFT)
        self.key_entry = tk.Entry(
            entry_row, show="*", width=34, bg=theme.BG_INPUT, fg=theme.TEXT_PRIMARY,
            insertbackground=theme.TEXT_PRIMARY, relief=tk.FLAT, highlightthickness=1,
            highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
        )
        self.key_entry.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True, ipady=4)

        btn_row = tk.Frame(inner, bg=theme.BG_PANEL)
        btn_row.pack(fill=tk.X, pady=(10, 0))
        tk.Button(btn_row, text="Save key", command=self._save_key, bg=theme.ACCENT, fg="white",
                  relief=tk.FLAT, bd=0, padx=12, pady=6, font=theme.FONT_SMALL_BOLD,
                  cursor="hand2").pack(side=tk.LEFT)
        tk.Button(btn_row, text="Remove API Key", command=self._remove_key, bg=theme.BG_INPUT,
                  fg=theme.TEXT_PRIMARY, relief=tk.FLAT, bd=0, padx=12, pady=6,
                  font=theme.FONT_SMALL_BOLD, cursor="hand2").pack(side=tk.LEFT, padx=(8, 0))

        self.key_status_label = tk.Label(inner, text="", bg=theme.BG_PANEL, fg=theme.TEXT_SECONDARY,
                                          font=theme.FONT_SMALL)
        self.key_status_label.pack(anchor=tk.W, pady=(8, 0))

    def _build_safety_section(self, parent):
        inner = self._section_card(parent, "Safety")

        row = tk.Frame(inner, bg=theme.BG_PANEL)
        row.pack(fill=tk.X)
        tk.Label(row, text="Confirm important actions", bg=theme.BG_PANEL,
                 fg=theme.TEXT_PRIMARY, font=theme.FONT_BODY).pack(side=tk.LEFT)
        self.confirm_toggle = ToggleSwitch(
            row, initial=self.mw.safety.limits.confirmation_mode, command=self._apply_safety
        )
        self.confirm_toggle.pack(side=tk.RIGHT)

        grid = tk.Frame(inner, bg=theme.BG_PANEL)
        grid.pack(fill=tk.X, pady=(10, 0))

        self.max_actions_var = tk.StringVar(value=str(self.mw.safety.limits.max_actions))
        self.max_duration_var = tk.StringVar(value=str(self.mw.safety.limits.max_duration_seconds))
        self.max_retries_var = tk.StringVar(value=str(self.mw.safety.limits.max_consecutive_retries))

        self._limit_row(grid, 0, "Max actions per task", self.max_actions_var)
        self._limit_row(grid, 1, "Max task duration (s)", self.max_duration_var)
        self._limit_row(grid, 2, "Max consecutive retries", self.max_retries_var)

        tk.Button(inner, text="Apply limits", command=self._apply_safety, bg=theme.BG_INPUT,
                  fg=theme.TEXT_PRIMARY, relief=tk.FLAT, bd=0, padx=12, pady=6,
                  font=theme.FONT_SMALL_BOLD, cursor="hand2").pack(anchor=tk.W, pady=(10, 0))

        tk.Label(
            inner, text=f"Emergency stop hotkey: {EMERGENCY_STOP_HOTKEY.upper()} (fixed, always active)",
            bg=theme.BG_PANEL, fg=theme.TEXT_MUTED, font=theme.FONT_SMALL,
        ).pack(anchor=tk.W, pady=(10, 0))

    def _limit_row(self, grid, row, label, var):
        tk.Label(grid, text=label, bg=theme.BG_PANEL, fg=theme.TEXT_SECONDARY,
                 font=theme.FONT_BODY).grid(row=row, column=0, sticky="w", pady=3)
        tk.Entry(grid, textvariable=var, width=8, bg=theme.BG_INPUT, fg=theme.TEXT_PRIMARY,
                 insertbackground=theme.TEXT_PRIMARY, relief=tk.FLAT, highlightthickness=1,
                 highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
                 ).grid(row=row, column=1, sticky="w", padx=8, pady=3)

    def _build_voice_section(self, parent):
        inner = self._section_card(parent, "Voice")

        row = tk.Frame(inner, bg=theme.BG_PANEL)
        row.pack(fill=tk.X)
        tk.Label(row, text="Voice output", bg=theme.BG_PANEL, fg=theme.TEXT_PRIMARY,
                 font=theme.FONT_BODY).pack(side=tk.LEFT)
        self.voice_toggle = ToggleSwitch(row, initial=True, command=self._apply_voice)
        self.voice_toggle.pack(side=tk.RIGHT)

        vol_row = tk.Frame(inner, bg=theme.BG_PANEL)
        vol_row.pack(fill=tk.X, pady=(10, 0))
        tk.Label(vol_row, text="Volume", bg=theme.BG_PANEL, fg=theme.TEXT_SECONDARY,
                 font=theme.FONT_BODY).pack(side=tk.LEFT)
        self.volume_var = tk.DoubleVar(value=100)
        tk.Scale(
            vol_row, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.volume_var,
            command=lambda v: self._apply_voice(), bg=theme.BG_PANEL, fg=theme.TEXT_PRIMARY,
            troughcolor=theme.BG_INPUT, highlightthickness=0, activebackground=theme.ACCENT,
            bd=0,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        if not self.mw.speech_out.is_available():
            tk.Label(
                inner, text="pyttsx3 isn't available - voice output is disabled on this system.",
                bg=theme.BG_PANEL, fg=theme.WARNING, font=theme.FONT_SMALL, wraplength=360,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(8, 0))

        self._apply_voice()

    def _save_key(self):
        key = self.key_entry.get().strip()
        if not key:
            self.key_status_label.config(text="Enter a key first.", fg=theme.DANGER)
            return
        if not self.mw.key_manager.is_valid_format(key):
            self.key_status_label.config(
                text="That doesn't look like an OpenRouter key (should start with 'sk-or-').",
                fg=theme.DANGER,
            )
            return
        try:
            self.mw.key_manager.save_key(key)
        except RuntimeError as e:
            self.key_status_label.config(text=str(e), fg=theme.DANGER)
            return
        self.key_entry.delete(0, tk.END)
        masked = self.mw.key_manager.mask_key(self.mw.key_manager.get_key())
        self.current_key_label.config(text=f"Current: {masked}")
        self.key_status_label.config(text="Saved. Verifying...", fg=theme.TEXT_SECONDARY)
        self.mw._refresh_key_status()
        self.mw.logger.log("API key saved.", "SYSTEM")

    def _remove_key(self):
        if not messagebox.askyesno(
            "Remove API key", "Remove the stored OpenRouter API key from this computer?"
        ):
            return
        try:
            self.mw.key_manager.remove_key()
        except RuntimeError as e:
            self.key_status_label.config(text=str(e), fg=theme.DANGER)
            return
        self.current_key_label.config(text="Current: (not set)")
        self.key_status_label.config(text="Key removed.", fg=theme.TEXT_SECONDARY)
        self.mw.conn_pill.set("No API key", theme.TEXT_MUTED)
        self.mw.logger.log("API key removed.", "SYSTEM")

    def _apply_safety(self, *_args):
        limits = self.mw.safety.limits
        limits.confirmation_mode = self.confirm_toggle.get()
        try:
            limits.max_actions = max(1, int(self.max_actions_var.get()))
        except ValueError:
            pass
        try:
            limits.max_duration_seconds = max(10, int(self.max_duration_var.get()))
        except ValueError:
            pass
        try:
            limits.max_consecutive_retries = max(1, int(self.max_retries_var.get()))
        except ValueError:
            pass

    def _apply_voice(self, *_args):
        enabled = self.voice_toggle.get()
        self.mw.speech_out.set_enabled(enabled)
        self.mw.speech_out.set_volume(self.volume_var.get() / 100.0)
        self.mw.voice_status_label.config(text="Voice: ON" if enabled else "Voice: OFF")
