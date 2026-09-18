"""
Global F8 emergency-stop hotkey.

This listener is registered directly against the OS keyboard hook and
calls the provided callback immediately - it does not go through the
model, the agent loop, or the GUI event queue, so it keeps working even
if those are stuck (e.g. blocked on a slow network call).

Note: on Windows, `keyboard`'s global hook generally works for standard
user-level target windows. If the AI is interacting with an application
running elevated (as Administrator) while this app is not, Windows will
block the low-level hook from seeing keys destined for that elevated
window. For maximum reliability, run this application elevated too, or
avoid using it to control elevated applications. This is a Windows
security boundary, not a bug in this app - documented here rather than
silently assumed away.
"""

import threading
from typing import Callable, Optional

from config.settings import EMERGENCY_STOP_HOTKEY

try:
    import keyboard as kb
except Exception:  # pragma: no cover - allows import on non-Windows for review
    kb = None


class HotkeyListener:
    def __init__(self, on_trigger: Callable[[], None], hotkey: str = EMERGENCY_STOP_HOTKEY):
        self._on_trigger = on_trigger
        self._hotkey = hotkey
        self._registered = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        """Returns True if the hotkey was registered, False if the
        `keyboard` library is unavailable (e.g. running for code review on
        a non-Windows machine, or missing elevation)."""
        if kb is None:
            return False
        with self._lock:
            if self._registered:
                return True
            try:
                kb.add_hotkey(self._hotkey, self._on_trigger, suppress=False)
                self._registered = True
                return True
            except Exception:
                return False

    def stop(self):
        if kb is None:
            return
        with self._lock:
            if self._registered:
                try:
                    kb.remove_hotkey(self._hotkey)
                except Exception:
                    pass
                self._registered = False
