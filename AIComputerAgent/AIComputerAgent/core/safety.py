"""
SafetyController centralises everything that must work even if the model
misbehaves: emergency stop, pause, and configurable limits. Nothing in
this module depends on the AI cooperating - the agent loop is required to
check these flags between every single step, and the hotkey/STOP button
paths call `trigger_emergency_stop()` directly without going through the
model at all.
"""

import threading
import time
from dataclasses import dataclass

from config.settings import (
    DEFAULT_MAX_ACTIONS_PER_TASK,
    DEFAULT_MAX_TASK_DURATION_SECONDS,
    DEFAULT_MAX_CONSECUTIVE_RETRIES,
    DEFAULT_CONFIRMATION_MODE,
)


@dataclass
class SafetyLimits:
    max_actions: int = DEFAULT_MAX_ACTIONS_PER_TASK
    max_duration_seconds: int = DEFAULT_MAX_TASK_DURATION_SECONDS
    max_consecutive_retries: int = DEFAULT_MAX_CONSECUTIVE_RETRIES
    confirmation_mode: bool = DEFAULT_CONFIRMATION_MODE


class LimitReached(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SafetyController:
    def __init__(self, limits: SafetyLimits = None):
        self.limits = limits or SafetyLimits()

        self._emergency_stop = threading.Event()
        self._pause = threading.Event()

        self._lock = threading.Lock()
        self._action_count = 0
        self._consecutive_retries = 0
        self._start_time = None

    # --- lifecycle ------------------------------------------------------
    def start_task(self):
        with self._lock:
            self._action_count = 0
            self._consecutive_retries = 0
            self._start_time = time.monotonic()
        self._emergency_stop.clear()
        self._pause.clear()

    # --- emergency stop ---------------------------------------------------
    def trigger_emergency_stop(self):
        """Must be safe to call from ANY thread (hotkey thread, GUI thread,
        agent thread) at ANY time, including mid-action."""
        self._emergency_stop.set()
        self._pause.clear()  # don't leave the loop stuck waiting on pause

    def is_stopped(self) -> bool:
        return self._emergency_stop.is_set()

    def clear_stop(self):
        self._emergency_stop.clear()

    # --- pause --------------------------------------------------------------
    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def wait_while_paused(self, poll_seconds: float = 0.2):
        """Blocks the calling (agent) thread while paused. Wakes up early if
        an emergency stop happens while paused."""
        while self._pause.is_set() and not self._emergency_stop.is_set():
            time.sleep(poll_seconds)

    # --- limits -------------------------------------------------------------
    def record_action(self):
        with self._lock:
            self._action_count += 1
            if self._action_count > self.limits.max_actions:
                raise LimitReached(
                    f"Maximum actions per task reached ({self.limits.max_actions})."
                )
        if self._start_time is not None:
            elapsed = time.monotonic() - self._start_time
            if elapsed > self.limits.max_duration_seconds:
                raise LimitReached(
                    f"Maximum task duration reached ({self.limits.max_duration_seconds}s)."
                )

    def record_success(self):
        with self._lock:
            self._consecutive_retries = 0

    def record_failure(self):
        with self._lock:
            self._consecutive_retries += 1
            if self._consecutive_retries > self.limits.max_consecutive_retries:
                raise LimitReached(
                    "Maximum consecutive retries reached "
                    f"({self.limits.max_consecutive_retries}); the same action "
                    "kept failing."
                )

    def status_snapshot(self) -> dict:
        with self._lock:
            elapsed = (
                time.monotonic() - self._start_time if self._start_time else 0
            )
            return {
                "actions_used": self._action_count,
                "actions_max": self.limits.max_actions,
                "elapsed_seconds": round(elapsed, 1),
                "duration_max": self.limits.max_duration_seconds,
                "consecutive_retries": self._consecutive_retries,
            }
