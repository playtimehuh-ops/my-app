"""
Thread-safe, categorized activity log. Every line is redacted for anything
that looks like an OpenRouter API key before it is stored or handed to a
UI callback, so the key can never end up in the on-screen log, a saved log
file, or a screenshot of this application.

Categories match the app's spec: SYSTEM, AI, OBSERVE, ACTION, WAIT,
WARNING, ERROR, STOP. The UI colors each one differently (see
config/theme.py's LOG_COLORS) and never has to guess a category from
message text.
"""

import threading
import time
from collections import deque
from typing import Callable, List, NamedTuple, Optional

from config.settings import ACTIVITY_LOG_MAX_LINES
from core.api_key_manager import ApiKeyManager

VALID_CATEGORIES = {
    "SYSTEM", "AI", "OBSERVE", "ACTION", "WAIT", "WARNING", "ERROR", "STOP",
}


class LogLine(NamedTuple):
    timestamp: str
    category: str
    message: str

    def formatted(self) -> str:
        return f"{self.timestamp}  {self.category:<8} {self.message}"


class ActivityLogger:
    def __init__(self, on_line: Optional[Callable[[LogLine], None]] = None):
        self._lines: deque = deque(maxlen=ACTIVITY_LOG_MAX_LINES)
        self._lock = threading.Lock()
        self._on_line = on_line
        self._last_message: Optional[str] = None
        self._last_message_count = 0

    def log(self, message: str, category: str = "SYSTEM"):
        category = category if category in VALID_CATEGORIES else "SYSTEM"
        message = ApiKeyManager.redact(str(message))

        # Avoid spamming identical consecutive lines (e.g. a repeated
        # "Desktop captured" every single cycle is noise, not signal).
        with self._lock:
            if message == self._last_message and category in ("OBSERVE", "WAIT"):
                self._last_message_count += 1
                if self._last_message_count > 1:
                    return
            else:
                self._last_message = message
                self._last_message_count = 0

        line = LogLine(timestamp=time.strftime("%H:%M:%S"), category=category, message=message)
        with self._lock:
            self._lines.append(line)
        if self._on_line:
            try:
                self._on_line(line)
            except Exception:
                pass  # a UI callback error must never crash the agent loop

    def snapshot(self) -> List[LogLine]:
        with self._lock:
            return list(self._lines)

    def clear(self):
        with self._lock:
            self._lines.clear()
            self._last_message = None
            self._last_message_count = 0
