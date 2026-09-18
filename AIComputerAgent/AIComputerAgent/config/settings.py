"""
Central configuration and defaults for AI Computer Agent.
Nothing secret lives in this file. The OpenRouter API key is handled
exclusively by core/api_key_manager.py via the Windows credential store.
"""

APP_NAME = "AI Computer Agent"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_ENDPOINT = f"{OPENROUTER_BASE_URL}/models"
OPENROUTER_CHAT_ENDPOINT = f"{OPENROUTER_BASE_URL}/chat/completions"

# Credential store identifiers (keyring)
CRED_SERVICE_NAME = "AIComputerAgent"
CRED_USERNAME = "openrouter_api_key"

# --- Model routing ----------------------------------------------------------
# We do NOT hard-code an individual model (e.g. a specific vendor's free
# model). Instead we use OpenRouter's own "Free Models Router" alias, which
# OpenRouter itself keeps pointed at a currently-available free model that
# supports the features the request needs (image input, tool calling).
# See: https://openrouter.ai/docs/guides/routing/routers/free-models-router
# This replaces the app's previous custom "query /models and pick one"
# logic, which had no way to know that a given free model might be
# restricted (e.g. some providers restrict a model to agentic harnesses
# only) - OpenRouter's router is responsible for avoiding that, and picks
# again (potentially a different underlying model) on every request.
FREE_MODELS_ROUTER_MODEL = "openrouter/free"

# --- Safety defaults --------------------------------------------------------
DEFAULT_MAX_ACTIONS_PER_TASK = 60
DEFAULT_MAX_TASK_DURATION_SECONDS = 600
DEFAULT_MAX_CONSECUTIVE_RETRIES = 3
DEFAULT_CONFIRMATION_MODE = True
EMERGENCY_STOP_HOTKEY = "f8"

# --- Cycle pacing -------------------------------------------------------------
# Target: about one AI request started per second, never overlapping.
# This is the interval between the *start* of one request and the *start*
# of the next - not an unconditional sleep after every step. See
# core/agent_loop.py's `_pace_cycle`.
MIN_CYCLE_SECONDS = 1.0
# Granularity of the interruptible wait used while pacing/paused, so
# STOP/PAUSE take effect quickly instead of only after a long sleep() call.
STOP_POLL_INTERVAL_SECONDS = 0.05
# Extra backoff added on top of normal cycle pacing specifically after a
# 429 (rate limited) response, so we don't hammer the same limit at ~1/sec.
RATE_LIMIT_EXTRA_BACKOFF_SECONDS = 3.0

# Loop pacing (other)
SCREENSHOT_MAX_DIMENSION = 1280   # downscale screenshots before sending to the model
# Network request timeout. Kept well under a minute so an emergency STOP is
# never blocked for long behind an in-flight request that cannot itself be
# cancelled - see the honest limitation noted in core/agent_loop.py.
MODEL_CALL_TIMEOUT_SECONDS = 30
POST_ACTION_SETTLE_SECONDS = 0.4  # brief pause after an action before re-screenshotting

# --- Confirmation heuristics -------------------------------------------------
# The agent cannot know with certainty what a click "means" - it only knows
# screen coordinates. As a practical safeguard, every tool call the model
# makes must include a short plain-English "reason" string. When
# confirmation mode is ON, we scan that reason (and the tool name) for the
# keywords below and pause for the user's explicit go-ahead before acting.
# This is a heuristic, not a guarantee - see README "Honest limitations".
#
# NOTE: keywords are deliberately stored as word STEMS (e.g. "delet" rather
# than "delete") so that plain substring matching still catches common
# conjugations - "deleting"/"deleted"/"deletion" all contain "delet", but
# none of them contain the full word "delete". Don't "fix" these back to
# whole words without re-checking the -ing/-ed/-ion forms.
CONFIRMATION_KEYWORDS = [
    "delet", "remov", "eras", "format", "wip",
    "send", "submit", "post", "publish", "repl", "messag",
    "buy", "purchas", "checkout", "order", "pay", "card number",
    "install", "uninstall", "setup.exe", "update driver",
    "run command", "cmd", "powershell", "terminal", "execut", "sudo",
    "registry", "regedit", "system setting", "control panel", "admin",
    "overwrit", "permanent", "confirm delet", "empty recycle bin",
    "shutdown", "restart", "reset", "factory reset",
]

TOOLS_ALWAYS_REQUIRING_CONFIRMATION = set()  # reserved for future use

ACTIVITY_LOG_MAX_LINES = 2000
