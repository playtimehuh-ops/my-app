"""
Voice output via `pyttsx3` (wraps Windows SAPI5 - works fully offline).

Only ever fed short, user-facing status strings by the agent loop (the
`reason` field of a tool call, or a final task_complete/task_failed
summary) - never hidden model reasoning, never raw API payloads, never
the API key.
"""

import queue
import threading
from typing import Optional

try:
    import pyttsx3
except Exception:  # pragma: no cover
    pyttsx3 = None


class SpeechOutput:
    def __init__(self):
        self._available = pyttsx3 is not None
        self._enabled = True
        self._volume = 1.0
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._engine = None
        if self._available:
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def is_available(self) -> bool:
        return self._available

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if not enabled:
            self.stop()

    def set_volume(self, volume: float):
        """volume in [0.0, 1.0]"""
        self._volume = max(0.0, min(1.0, volume))

    def speak(self, text: str):
        if not self._available or not self._enabled or not text:
            return
        self._queue.put(text)

    def stop(self):
        """Immediately silence any in-progress or queued speech. Called by
        the emergency-stop path."""
        if not self._available:
            return
        with self._queue.mutex:
            self._queue.queue.clear()
        try:
            if self._engine is not None:
                self._engine.stop()
        except Exception:
            pass

    def _worker(self):
        self._engine = pyttsx3.init()
        while True:
            text = self._queue.get()
            if text is None:
                break
            try:
                self._engine.setProperty("volume", self._volume)
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception:
                pass
