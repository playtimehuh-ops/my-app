"""
OpenAI-style function-calling tool schema sent to OpenRouter.

Every action tool requires a short `reason` string: a plain-English,
user-facing description of what the action is for (e.g. "Clicking the
Chrome icon on the taskbar"). This is what gets spoken via TTS, written
to the activity log, and scanned by the confirmation-heuristic - it is
NOT a place for the model to dump hidden chain-of-thought, and the app
never displays anything longer than this one line.
"""

REASON_PROP = {
    "reason": {
        "type": "string",
        "description": (
            "Short (<15 words) plain-English, user-facing description of "
            "this action, e.g. 'Clicking the search box'. No private "
            "reasoning - just the visible action."
        ),
    }
}


def _fn(name, description, properties=None, required=None):
    props = dict(REASON_PROP)
    if properties:
        props.update(properties)
    req = ["reason"] + (required or [])
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": req,
            },
        },
    }


TOOLS = [
    _fn(
        "take_screenshot",
        "Capture a fresh screenshot of the current screen. Use this if you "
        "need to re-check the screen without performing any other action "
        "(a fresh screenshot is also always provided automatically after "
        "every action).",
    ),
    _fn(
        "mouse_move",
        "Physically move the real Windows mouse cursor to (x, y). Use this "
        "when you need the actual pointer at a location, e.g. to hover.",
        {
            "x": {"type": "integer", "description": "X pixel coordinate."},
            "y": {"type": "integer", "description": "Y pixel coordinate."},
        },
        ["x", "y"],
    ),
    _fn(
        "ai_cursor_move",
        "Move ONLY the visible AI-indicator cursor to (x, y) to show intent, "
        "WITHOUT moving the user's real mouse pointer. Useful for previewing "
        "where you are about to act.",
        {
            "x": {"type": "integer", "description": "X pixel coordinate."},
            "y": {"type": "integer", "description": "Y pixel coordinate."},
        },
        ["x", "y"],
    ),
    _fn(
        "left_click",
        "Move the real cursor to (x, y) and perform a left mouse click.",
        {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        ["x", "y"],
    ),
    _fn(
        "right_click",
        "Move the real cursor to (x, y) and perform a right mouse click.",
        {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        ["x", "y"],
    ),
    _fn(
        "double_click",
        "Move the real cursor to (x, y) and perform a double left click.",
        {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        ["x", "y"],
    ),
    _fn(
        "drag",
        "Press the left mouse button at (x1, y1), drag to (x2, y2), and "
        "release.",
        {
            "x1": {"type": "integer"},
            "y1": {"type": "integer"},
            "x2": {"type": "integer"},
            "y2": {"type": "integer"},
        },
        ["x1", "y1", "x2", "y2"],
    ),
    _fn(
        "scroll",
        "Scroll the mouse wheel at the current cursor position.",
        {
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
            },
            "amount": {
                "type": "integer",
                "description": "Number of scroll 'clicks' (positive integer).",
            },
        },
        ["direction", "amount"],
    ),
    _fn(
        "type_text",
        "Type text at the current keyboard focus/caret position.",
        {"text": {"type": "string"}},
        ["text"],
    ),
    _fn(
        "press_key",
        "Press and release a single key, e.g. 'enter', 'esc', 'tab', "
        "'backspace', 'up', 'down', 'left', 'right', 'delete', 'home', 'end'.",
        {"key": {"type": "string"}},
        ["key"],
    ),
    _fn(
        "keyboard_shortcut",
        "Press a combination of keys together, e.g. ['ctrl','c'] or "
        "['alt','tab'] or ['win','s'].",
        {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
        ["keys"],
    ),
    _fn(
        "open_application",
        "Open a Windows application by name (uses the Start menu search), "
        "e.g. 'Notepad', 'Google Chrome', 'Calculator'.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _fn(
        "switch_window",
        "Bring an already-open window to the foreground by matching part of "
        "its title, e.g. 'Chrome' or 'Notepad'.",
        {"title_contains": {"type": "string"}},
        ["title_contains"],
    ),
    _fn(
        "wait",
        "Do nothing for a short time, e.g. to let a page finish loading.",
        {
            "seconds": {
                "type": "number",
                "description": "How long to wait, in seconds (max 10).",
            }
        },
        ["seconds"],
    ),
    _fn(
        "task_complete",
        "Call this when the requested task has been fully completed and "
        "verified on screen. This ends the task.",
        {
            "summary": {
                "type": "string",
                "description": "One short sentence describing the completed result.",
            }
        },
        ["summary"],
    ),
    _fn(
        "task_failed",
        "Call this if the task cannot be completed (e.g. missing "
        "application, impossible request, repeated failures). This ends "
        "the task.",
        {
            "summary": {
                "type": "string",
                "description": "One short sentence explaining why the task cannot continue.",
            }
        },
        ["summary"],
    ),
]

# Tool names that only affect the visual overlay / observation and never
# touch the real mouse/keyboard - exempt from confirmation & retry-risk logic.
READ_ONLY_TOOLS = {"take_screenshot", "ai_cursor_move", "wait"}

# Tool names that end the loop.
TERMINAL_TOOLS = {"task_complete", "task_failed"}

# Human-facing label for the "Current Action" panel, keyed by tool name.
# The panel shows "<LABEL> \u2014 <model's reason text>", e.g.
# "CLICKING \u2014 Clicking the search box".
TOOL_ACTION_LABELS = {
    "take_screenshot": "OBSERVING SCREEN",
    "mouse_move": "MOVING CURSOR",
    "ai_cursor_move": "MOVING CURSOR",
    "left_click": "CLICKING",
    "right_click": "RIGHT-CLICKING",
    "double_click": "DOUBLE-CLICKING",
    "drag": "DRAGGING",
    "scroll": "SCROLLING",
    "type_text": "TYPING",
    "press_key": "PRESSING KEY",
    "keyboard_shortcut": "PRESSING SHORTCUT",
    "open_application": "OPENING APPLICATION",
    "switch_window": "SWITCHING WINDOW",
    "wait": "WAITING",
}
