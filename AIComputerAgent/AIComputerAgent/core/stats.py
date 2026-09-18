"""
Tracks only numbers that can actually be measured - no invented/estimated
values. Read by the UI via `snapshot()`, written by the agent loop as
requests happen. All access is lock-protected since the two run on
different threads.
"""

import threading
import time
from typing import Optional


class RequestStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.total = 0
            self.successful = 0
            self.failed = 0
            self.cycle = 0
            self._durations = []
            self.last_request_at: Optional[str] = None

    def next_cycle(self) -> int:
        with self._lock:
            self.cycle += 1
            return self.cycle

    def record(self, success: bool, duration_seconds: float):
        with self._lock:
            self.total += 1
            if success:
                self.successful += 1
            else:
                self.failed += 1
            self._durations.append(duration_seconds)
            if len(self._durations) > 200:  # bounded memory over a long task
                self._durations.pop(0)
            self.last_request_at = time.strftime("%H:%M:%S")

    def snapshot(self) -> dict:
        with self._lock:
            avg = (
                sum(self._durations) / len(self._durations) if self._durations else 0.0
            )
            return {
                "total": self.total,
                "successful": self.successful,
                "failed": self.failed,
                "cycle": self.cycle,
                "avg_request_seconds": avg,
                "last_request_at": self.last_request_at,
            }
