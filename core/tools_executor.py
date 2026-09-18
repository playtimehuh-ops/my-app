"""
Executes a single, already-validated tool call using local Windows APIs.

Nothing here calls the network and nothing here decides *whether* an
action should run - by the time `ToolExecutor.execute()` is called, the
agent loop has already checked SafetyController (limits/stop/pause) and,
if needed, obtained user confirmation. This module's only job is to
physically perform the action and report what happened.

Design notes:
  * `cursor_hook(x, y, style)` is called before any real mouse action so
    the visible AI-cursor overlay (ui/ai_cursor_overlay.py) can animate to
    the target first. `style` is one of "move" | "click" | "drag_start" |
    "drag_end". It is safe to pass None (headless/testing).
  * pyautogui's built-in fail-safe (slam the real mouse into a screen
    corner) is left ON as a *redundant, independent* way for the user to
    interrupt automation, on top of the F8 hotkey and STOP button - if it
    fires we translate it into the same emergency-stop signal.
"""

import base64
import io
import platform
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from PIL import Image

try:
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.02
except Exception:  # pragma: no cover - allows import on non-Windows for review
    pyautogui = None

try:
    import keyboard as kb
except Exception:  # pragma: no cover
    kb = None

_IS_WINDOWS = platform.system() == "Windows"

if _IS_WINDOWS:
    try:
        import win32gui
        import win32con
    except Exception:  # pywin32 not installed yet
        win32gui = None
        win32con = None
else:
    win32gui = None
    win32con = None

from config.settings import SCREENSHOT_MAX_DIMENSION


class ToolExecutionError(Exception):
    pass


class FailSafeTriggered(Exception):
    """Raised when the user yanks the real mouse to a screen corner."""


_KEY_ALIASES = {
    "esc": "esc",
    "escape": "esc",
    "return": "enter",
    "del": "delete",
}

_VALID_SCROLL_DIRECTIONS = {"up", "down", "left", "right"}


@dataclass
class ScreenshotResult:
    image: Image.Image
    data_url: str
    width: int
    height: int


class ToolExecutor:
    def __init__(self, cursor_hook: Optional[Callable[[int, int, str], None]] = None):
        self.cursor_hook = cursor_hook or (lambda x, y, style: None)
        if pyautogui is None:
            raise RuntimeError(
                "pyautogui is not available. This application must run on "
                "Windows with dependencies installed - see README.md."
            )

    # ------------------------------------------------------------------ #
    # Screenshot
    # ------------------------------------------------------------------ #
    def take_screenshot(self) -> ScreenshotResult:
        img = pyautogui.screenshot()
        w, h = img.size
        scale = min(1.0, SCREENSHOT_MAX_DIMENSION / max(w, h))
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        data_url = f"data:image/jpeg;base64,{encoded}"
        return ScreenshotResult(image=img, data_url=data_url, width=w, height=h)

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    def execute(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            raise ToolExecutionError(f"Unknown tool '{tool_name}'.")
        try:
            return handler(args)
        except pyautogui.FailSafeException as e:
            raise FailSafeTriggered(str(e)) from e

    # ------------------------------------------------------------------ #
    # Individual tools
    # ------------------------------------------------------------------ #
    def _tool_take_screenshot(self, args):
        return {"ok": True, "detail": "Screenshot captured."}

    def _tool_mouse_move(self, args):
        x, y = int(args["x"]), int(args["y"])
        self.cursor_hook(x, y, "move")
        pyautogui.moveTo(x, y, duration=0.25)
        return {"ok": True, "detail": f"Moved real cursor to ({x}, {y})."}

    def _tool_ai_cursor_move(self, args):
        x, y = int(args["x"]), int(args["y"])
        self.cursor_hook(x, y, "move")
        return {"ok": True, "detail": f"AI indicator moved to ({x}, {y}) (real cursor untouched)."}

    def _tool_left_click(self, args):
        x, y = int(args["x"]), int(args["y"])
        self.cursor_hook(x, y, "click")
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.click(button="left")
        return {"ok": True, "detail": f"Left-clicked at ({x}, {y})."}

    def _tool_right_click(self, args):
        x, y = int(args["x"]), int(args["y"])
        self.cursor_hook(x, y, "click")
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.click(button="right")
        return {"ok": True, "detail": f"Right-clicked at ({x}, {y})."}

    def _tool_double_click(self, args):
        x, y = int(args["x"]), int(args["y"])
        self.cursor_hook(x, y, "click")
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.doubleClick()
        return {"ok": True, "detail": f"Double-clicked at ({x}, {y})."}

    def _tool_drag(self, args):
        x1, y1 = int(args["x1"]), int(args["y1"])
        x2, y2 = int(args["x2"]), int(args["y2"])
        self.cursor_hook(x1, y1, "drag_start")
        pyautogui.moveTo(x1, y1, duration=0.2)
        pyautogui.mouseDown()
        self.cursor_hook(x2, y2, "drag_end")
        pyautogui.moveTo(x2, y2, duration=0.35)
        pyautogui.mouseUp()
        return {"ok": True, "detail": f"Dragged ({x1},{y1}) -> ({x2},{y2})."}

    def _tool_scroll(self, args):
        direction = str(args["direction"]).lower()
        amount = int(args["amount"])
        if direction not in _VALID_SCROLL_DIRECTIONS:
            raise ToolExecutionError(f"Invalid scroll direction '{direction}'.")
        clicks = amount if direction in ("up", "right") else -amount
        if direction in ("up", "down"):
            pyautogui.scroll(clicks)
        else:
            pyautogui.hscroll(clicks)
        return {"ok": True, "detail": f"Scrolled {direction} by {amount}."}

    def _tool_type_text(self, args):
        text = str(args["text"])
        pyautogui.write(text, interval=0.01)
        return {"ok": True, "detail": f"Typed {len(text)} character(s)."}

    def _tool_press_key(self, args):
        key = str(args["key"]).lower()
        key = _KEY_ALIASES.get(key, key)
        pyautogui.press(key)
        return {"ok": True, "detail": f"Pressed key '{key}'."}

    def _tool_keyboard_shortcut(self, args):
        keys = [str(k).lower() for k in args["keys"]]
        keys = [_KEY_ALIASES.get(k, k) for k in keys]
        pyautogui.hotkey(*keys)
        return {"ok": True, "detail": f"Pressed shortcut {'+'.join(keys)}."}

    def _tool_open_application(self, args):
        name = str(args["name"])
        # Most robust generic approach: Windows search, works for anything
        # with a Start Menu entry without needing to know install paths.
        pyautogui.hotkey("win", "s")
        time.sleep(0.4)
        pyautogui.write(name, interval=0.02)
        time.sleep(0.6)
        pyautogui.press("enter")
        return {"ok": True, "detail": f"Requested Windows to open '{name}'."}

    def _tool_switch_window(self, args):
        title_contains = str(args["title_contains"]).lower()
        if win32gui is None:
            # Fallback: best-effort Alt+Tab (imprecise - cannot target a
            # specific window by title without pywin32 installed).
            pyautogui.keyDown("alt")
            pyautogui.press("tab")
            time.sleep(0.3)
            pyautogui.keyUp("alt")
            return {
                "ok": True,
                "detail": "pywin32 not available - used a generic Alt+Tab instead "
                "of targeting a specific window.",
            }

        match = {"hwnd": None}

        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if title and title_contains in title.lower():
                match["hwnd"] = hwnd
                return False  # stop enumerating
            return True

        win32gui.EnumWindows(_cb, None)
        if not match["hwnd"]:
            raise ToolExecutionError(
                f"No open window with a title containing '{title_contains}' was found."
            )
        hwnd = match["hwnd"]
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return {"ok": True, "detail": f"Switched to window matching '{title_contains}'."}

    def _tool_wait(self, args):
        seconds = min(float(args["seconds"]), 10.0)
        time.sleep(max(0.0, seconds))
        return {"ok": True, "detail": f"Waited {seconds:.1f}s."}

    def _tool_task_complete(self, args):
        return {"ok": True, "detail": str(args.get("summary", "Task complete."))}

    def _tool_task_failed(self, args):
        return {"ok": True, "detail": str(args.get("summary", "Task failed."))}

    # ------------------------------------------------------------------ #
    # Emergency helpers - called directly by SafetyController's stop path,
    # NOT through the normal tool-call flow.
    # ------------------------------------------------------------------ #
    @staticmethod
    def release_all_inputs():
        """Best-effort release of any held mouse button / modifier key.
        Safe to call repeatedly and safe to call even if nothing is held."""
        if pyautogui is None:
            return
        try:
            pyautogui.mouseUp(button="left")
            pyautogui.mouseUp(button="right")
            pyautogui.mouseUp(button="middle")
        except Exception:
            pass
        for key in ("shift", "ctrl", "alt", "win"):
            try:
                pyautogui.keyUp(key)
            except Exception:
                pass
