"""
A small, thread-safe state machine for the task lifecycle.

This is the single source of truth for "is a task currently running", and
`try_begin()` is the ONLY way to transition into a running task - it is an
atomic compare-and-swap, so even if two threads (e.g. a double-clicked
START button and a stale retry) call it at the same instant, only one can
win. The UI never has to trust that its own button-disabling logic is
race-free; `AgentLoop.run()` re-checks this itself as the authoritative
guard (see core/agent_loop.py).

States (per the app spec):
    IDLE, STARTING, THINKING, WORKING, WAITING, PAUSED, STOPPING,
    STOPPED, ERROR
Plus one addition not in the original list: COMPLETED, used only to
distinguish "the task finished successfully" from "the task was stopped/
errored", since STOPPED alone couldn't otherwise tell those apart. Both
COMPLETED and STOPPED are "at rest" and equally startable.
"""

import threading
from enum import Enum


class TaskState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    THINKING = "THINKING"
    WORKING = "WORKING"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"

    def __str__(self):
        return self.value


# States from which a new task is allowed to begin.
_STARTABLE = {TaskState.IDLE, TaskState.STOPPED, TaskState.COMPLETED, TaskState.ERROR}


class TaskStateMachine:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = TaskState.IDLE

    def try_begin(self) -> bool:
        """Atomically move to STARTING iff currently at rest. Returns True
        only for the caller that actually won the transition - that caller,
        and only that caller, may start a worker thread."""
        with self._lock:
            if self._state not in _STARTABLE:
                return False
            self._state = TaskState.STARTING
            return True

    def set(self, state: TaskState):
        with self._lock:
            self._state = state

    def get(self) -> TaskState:
        with self._lock:
            return self._state

    def is_active(self) -> bool:
        """True while a worker is running/paused/starting/stopping - i.e.
        anything other than the four 'at rest' states."""
        with self._lock:
            return self._state not in _STARTABLE
