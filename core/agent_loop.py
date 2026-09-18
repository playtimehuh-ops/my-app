"""
The core see -> decide -> act -> observe loop.

This class knows nothing about Tkinter. It talks to the outside world only
through the `AgentCallbacks` it is given, so it can be driven from any UI
(and unit-tested headlessly, which is how this rewrite was actually
verified - see the test scripts referenced in the implementation report).

Key properties of this version:
  * Model: always "openrouter/free" (config.settings.FREE_MODELS_ROUTER_MODEL)
    - OpenRouter's own Free Models Router. This app never queries /models
      to hand-pick a specific vendor's free model, so it cannot silently
      end up on a restricted one again.
  * Pacing: about one request *started* per second, timed with
    time.monotonic() around the whole cycle (request + action + observe),
    waiting only the leftover time - never an unconditional sleep(1), and
    never overlapping requests (this loop is single-threaded and
    sequential by construction; see AgentLoop.run()'s guard below for the
    duplicate-worker protection).
  * Every wait (pacing, pause) is interruptible in ~50ms slices so
    emergency stop takes effect quickly instead of only after a long
    sleep() returns.
  * States reported via `AgentCallbacks.on_status` are core.task_state's
    TaskState values, driving the header/status pill directly.
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from config.settings import (
    FREE_MODELS_ROUTER_MODEL,
    MIN_CYCLE_SECONDS,
    STOP_POLL_INTERVAL_SECONDS,
    RATE_LIMIT_EXTRA_BACKOFF_SECONDS,
    POST_ACTION_SETTLE_SECONDS,
)
from core.confirmation import requires_confirmation
from core.openrouter_client import OpenRouterClient, OpenRouterError
from core.safety import SafetyController, LimitReached
from core.stats import RequestStats
from core.task_state import TaskState, TaskStateMachine
from core.tools_executor import ToolExecutor, ToolExecutionError, FailSafeTriggered
from core.tools_schema import TOOLS, TERMINAL_TOOLS, TOOL_ACTION_LABELS


SYSTEM_PROMPT = """You are a careful computer-operating assistant. You control \
this Windows PC ONLY through the provided tools - you have no other way to \
act. On every turn you are shown the current screen as an image.

Rules you must follow:
1. Look at the screenshot before deciding. Never assume a previous action \
succeeded - check the new screenshot for evidence it worked.
2. Call exactly ONE tool per turn.
3. Every tool call must include a short "reason" field: a plain, \
user-facing sentence fragment (under 15 words) describing the action, e.g. \
"Clicking the Chrome icon". Do not put private reasoning, plans, or \
chain-of-thought anywhere in "reason" - it will be shown and spoken to the \
user verbatim.
4. If an action does not seem to have worked, look carefully at the new \
screenshot and try a sensible alternative - do not repeat the exact same \
action blindly more than once.
5. When the task is fully done and you can see evidence of that on screen, \
call task_complete with a one-sentence summary.
6. If you determine the task cannot be completed (missing app, impossible \
request, stuck after trying alternatives), call task_failed with a \
one-sentence explanation.
7. Never claim an action succeeded without visual evidence from the screenshot.
"""


@dataclass
class AgentCallbacks:
    on_log: Callable[[str, str], None] = lambda msg, category: None
    on_status: Callable[[TaskState], None] = lambda status: None
    on_current_action: Callable[[str, str], None] = lambda label, detail: None
    on_screenshot: Callable[[Any], None] = lambda img: None
    on_speak: Callable[[str], None] = lambda text: None
    on_model_changed: Callable[[str], None] = lambda model_id: None
    on_next_request_eta: Callable[[Optional[float]], None] = lambda deadline: None
    # Must be safe to call from the agent (background) thread and must
    # block until the user answers, returning True (proceed) / False (skip).
    request_confirmation: Callable[[str, str, Dict[str, Any]], bool] = (
        lambda tool_name, reason, args: True
    )


class DuplicateWorkerError(Exception):
    """Raised (and immediately caught) if run() is somehow invoked while a
    task is already active - should be unreachable via the UI, which
    itself checks TaskStateMachine.try_begin() before spawning a thread,
    but this is the authoritative, race-free guard."""


class AgentLoop:
    def __init__(
        self,
        client: OpenRouterClient,
        executor: ToolExecutor,
        safety: SafetyController,
        state: TaskStateMachine,
        stats: RequestStats,
        callbacks: AgentCallbacks,
    ):
        self.client = client
        self.executor = executor
        self.safety = safety
        self.state = state
        self.stats = stats
        self.cb = callbacks
        self.model = FREE_MODELS_ROUTER_MODEL

    # ------------------------------------------------------------------ #
    # Interruptible waiting - never a blind sleep() that STOP can't cut short
    # ------------------------------------------------------------------ #
    def _interruptible_sleep(self, seconds: float):
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            if self.safety.is_stopped():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(STOP_POLL_INTERVAL_SECONDS, remaining))

    def _pace_cycle(self, cycle_started_at: float):
        """Called once per loop iteration, after the request/action/observe
        for that cycle are done. Sleeps only the leftover time needed to
        keep ~MIN_CYCLE_SECONDS between the *starts* of consecutive
        requests - never stacks an unconditional extra second on top of
        however long the cycle already took."""
        elapsed = time.monotonic() - cycle_started_at
        remaining = MIN_CYCLE_SECONDS - elapsed
        if remaining <= 0:
            self.cb.on_next_request_eta(time.monotonic())  # about to start immediately
            return
        deadline = time.monotonic() + remaining
        self.cb.on_status(TaskState.WAITING)
        self.cb.on_current_action("WAITING FOR NEXT AI CYCLE", "")
        self.cb.on_next_request_eta(deadline)
        self.cb.on_log(f"Next request in {remaining:.1f}s", "WAIT")
        self._interruptible_sleep(remaining)

    # ------------------------------------------------------------------ #
    def run(self, task_text: str):
        # Authoritative duplicate-worker guard: only the caller that wins
        # this atomic transition may proceed. The UI also checks this
        # before spawning a thread (for instant button feedback), but this
        # is what actually prevents two loops from ever running together.
        if not self.state.try_begin():
            raise DuplicateWorkerError("A task is already running.")

        try:
            self.safety.start_task()
            self.stats.reset()
            self.cb.on_status(TaskState.STARTING)
            self.cb.on_log(f"Task started: {task_text}", "SYSTEM")
            self.cb.on_model_changed(self.model)
            self._run_inner(task_text)
        except Exception as e:  # noqa: BLE001 - last-resort safety net; covers
            # startup calls above AND the whole cycle loop below, so nothing
            # between try_begin() and a clean _finish() can leave the state
            # machine stuck.
            try:
                self.cb.on_log(f"Unexpected internal error: {e}", "ERROR")
            except Exception:
                pass
            try:
                self._finish(TaskState.ERROR, f"Unexpected error: {e}")
            except Exception:
                # Absolute last resort: even if a callback itself is broken,
                # force the state machine back to ERROR directly so the app
                # can always be restarted instead of Start staying disabled
                # forever. This is the fix for "it stopped working after an
                # error" - previously an exception here could leave the
                # state machine stuck in STARTING/THINKING permanently.
                self.state.set(TaskState.ERROR)

    def _run_inner(self, task_text: str):
        messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.append({"role": "user", "content": self._user_turn(task_text, initial=True)})

        while True:
            cycle_started_at = time.monotonic()
            cycle_num = self.stats.next_cycle()

            if self.safety.is_stopped():
                self._finish(TaskState.STOPPED, "Stopped by user.")
                return

            if self.safety.is_paused():
                self.cb.on_status(TaskState.PAUSED)
                self.cb.on_current_action("PAUSED", "")
                self.cb.on_next_request_eta(None)
                self.safety.wait_while_paused()
                if self.safety.is_stopped():
                    self._finish(TaskState.STOPPED, "Stopped by user.")
                    return

            self.cb.on_status(TaskState.THINKING)
            self.cb.on_current_action("THINKING", f"Cycle {cycle_num}")
            self.cb.on_log(f"Request sent (cycle {cycle_num})", "AI")

            request_started_at = time.monotonic()
            try:
                response = self.client.chat(self.model, messages, TOOLS)
                self.stats.record(True, time.monotonic() - request_started_at)
            except OpenRouterError as e:
                self.stats.record(False, time.monotonic() - request_started_at)
                if self.safety.is_stopped():
                    self._finish(TaskState.STOPPED, "Stopped by user.")
                    return

                if e.kind == "invalid_key":
                    self._finish(TaskState.ERROR, "Your OpenRouter API key is invalid or missing.")
                    return

                readable = self._readable_error(e)
                self.cb.on_log(readable, "ERROR")
                self.cb.on_log("The AI could not complete this cycle. Retrying on the "
                                "next scheduled cycle.", "SYSTEM")
                try:
                    self.safety.record_failure()
                except LimitReached as lr:
                    self._finish(TaskState.ERROR, str(lr))
                    return

                if e.kind == "rate_limited":
                    self._interruptible_sleep(RATE_LIMIT_EXTRA_BACKOFF_SECONDS)
                    if self.safety.is_stopped():
                        self._finish(TaskState.STOPPED, "Stopped by user.")
                        return

                self._pace_cycle(cycle_started_at)
                continue

            if self.safety.is_stopped():
                self._finish(TaskState.STOPPED, "Stopped by user.")
                return

            actual_model = response.get("model")
            if actual_model:
                self.cb.on_model_changed(actual_model)

            tool_call, assistant_msg = self._extract_tool_call(response)
            messages.append(assistant_msg)
            self.cb.on_log("Decision received", "AI")

            if tool_call is None:
                self.cb.on_log("Model responded without an action; prompting it to continue.", "WARNING")
                messages.append(
                    {
                        "role": "user",
                        "content": "Please call exactly one tool to continue, or "
                        "task_complete / task_failed if you are done.",
                    }
                )
                self._pace_cycle(cycle_started_at)
                continue

            name = tool_call["name"]
            args = tool_call["args"]
            reason = args.get("reason", "").strip() or name.replace("_", " ")

            if name in TERMINAL_TOOLS:
                summary = args.get("summary", "") or reason
                if name == "task_complete":
                    self._finish(TaskState.COMPLETED, summary)
                else:
                    self._finish(TaskState.ERROR, summary)
                return

            # --- limits ---------------------------------------------------
            try:
                self.safety.record_action()
            except LimitReached as lr:
                self._finish(TaskState.STOPPED, str(lr))
                return

            # --- confirmation ----------------------------------------------
            if requires_confirmation(name, reason, self.safety.limits.confirmation_mode):
                self.cb.on_status(TaskState.WAITING)
                self.cb.on_current_action("WAITING FOR CONFIRMATION", reason)
                self.cb.on_log(f"Confirmation requested: {reason}", "WARNING")
                approved = self.cb.request_confirmation(name, reason, args)
                if self.safety.is_stopped():
                    self._finish(TaskState.STOPPED, "Stopped by user.")
                    return
                if not approved:
                    self.cb.on_log("User declined this action; skipping it.", "WARNING")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": "The user declined to confirm this action. "
                            "It was NOT performed. Choose a different approach.",
                        }
                    )
                    self._pace_cycle(cycle_started_at)
                    continue

            # --- execute -----------------------------------------------------
            if self.safety.is_stopped():
                self._finish(TaskState.STOPPED, "Stopped by user.")
                return

            label = TOOL_ACTION_LABELS.get(name, name.upper())
            self.cb.on_status(TaskState.WORKING)
            self.cb.on_current_action(label, reason)
            self.cb.on_log(reason, "ACTION")
            self.cb.on_speak(reason)

            try:
                result = self.executor.execute(name, args)
                self.safety.record_success()
            except FailSafeTriggered:
                self.cb.on_log("Fail-safe triggered (mouse moved to screen corner).", "STOP")
                self.safety.trigger_emergency_stop()
                self._finish(TaskState.STOPPED, "Stopped: fail-safe corner triggered by user.")
                return
            except (ToolExecutionError, Exception) as e:  # noqa: BLE001 - feed failures back to the model
                self.cb.on_log(f"Action failed: {e}", "ERROR")
                try:
                    self.safety.record_failure()
                except LimitReached as lr:
                    self._finish(TaskState.ERROR, str(lr))
                    return
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"Action failed: {e}. Try a different approach.",
                    }
                )
                self._pace_cycle(cycle_started_at)
                continue

            if self.safety.is_stopped():
                self._finish(TaskState.STOPPED, "Stopped by user.")
                return

            self._interruptible_sleep(POST_ACTION_SETTLE_SECONDS)
            if self.safety.is_stopped():
                self._finish(TaskState.STOPPED, "Stopped by user.")
                return

            # --- observe -------------------------------------------------------
            self.cb.on_current_action("OBSERVING SCREEN", "")
            self.cb.on_log("Desktop captured", "OBSERVE")
            shot = self.executor.take_screenshot()
            self.cb.on_screenshot(shot.image)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result.get("detail", "Done."),
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Here is the screen after that action. Continue the task.",
                        },
                        {"type": "image_url", "image_url": {"url": shot.data_url}},
                    ],
                }
            )
            messages = self._trim_history(messages)

            self._pace_cycle(cycle_started_at)

    # ------------------------------------------------------------------ #
    def _user_turn(self, task_text: str, initial: bool):
        shot = self.executor.take_screenshot()
        self.cb.on_screenshot(shot.image)
        self.cb.on_log("Desktop captured", "OBSERVE")
        prefix = (
            f"Task: {task_text}\n\nHere is the current screen."
            if initial
            else "Here is the current screen."
        )
        return [
            {"type": "text", "text": prefix},
            {"type": "image_url", "image_url": {"url": shot.data_url}},
        ]

    @staticmethod
    def _readable_error(e: OpenRouterError) -> str:
        if e.kind == "rate_limited":
            return "OpenRouter request failed: rate limited."
        if e.kind == "model_unavailable":
            return "OpenRouter request failed: no free model was available for this request."
        if e.kind == "network":
            return "OpenRouter request failed: network error or timeout."
        if e.kind == "server":
            return "OpenRouter request failed: server error."
        return f"OpenRouter request failed: {e}"

    @staticmethod
    def _extract_tool_call(response: Dict[str, Any]):
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message", {})
        tool_calls = message.get("tool_calls") or []

        assistant_msg = {
            "role": "assistant",
            "content": message.get("content") or "",
        }
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls

        if not tool_calls:
            return None, assistant_msg

        call = tool_calls[0]
        fn = call.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        return {"id": call.get("id", ""), "name": fn.get("name", ""), "args": args}, assistant_msg

    @staticmethod
    def _trim_history(messages: List[Dict[str, Any]], keep_last_images: int = 2):
        """Keep the system + first user turn, and only the most recent N
        image-bearing turns, to control token/cost growth over a long task."""
        if len(messages) <= 6:
            return messages
        head = messages[:2]
        tail = messages[2:]

        image_turn_indices = [
            i
            for i, m in enumerate(tail)
            if isinstance(m.get("content"), list)
            and any(part.get("type") == "image_url" for part in m["content"])
        ]
        if len(image_turn_indices) <= keep_last_images:
            return messages

        cutoff = image_turn_indices[-keep_last_images]
        trimmed_tail = []
        for i, m in enumerate(tail):
            if i < cutoff and isinstance(m.get("content"), list):
                text_parts = [p for p in m["content"] if p.get("type") == "text"]
                m = {**m, "content": text_parts or "(earlier screen omitted)"}
            trimmed_tail.append(m)
        return head + trimmed_tail

    def _finish(self, state: TaskState, message: str):
        self.state.set(state)
        self.cb.on_status(state)
        self.cb.on_current_action(str(state), message)
        self.cb.on_next_request_eta(None)
        category = "STOP" if state == TaskState.STOPPED else "SYSTEM"
        self.cb.on_log(
            f"Task {state.value.lower()}: {message}" if message else f"Task {state.value.lower()}",
            category,
        )
        self.cb.on_speak(message)
