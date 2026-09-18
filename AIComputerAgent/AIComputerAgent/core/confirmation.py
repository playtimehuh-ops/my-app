"""
Heuristic detection of "important" actions that should be confirmed by the
user before they execute, when confirmation mode is ON.

Honest limitation: the agent only ever knows screen coordinates and the
model's own one-line description of an action - it has no ground truth
about what a click actually does. This heuristic scans the tool name and
the model-provided `reason` text for risk keywords (delete/purchase/
install/run command/etc.) as a practical safety net, not a guarantee.
When in doubt, leave confirmation mode ON.
"""

from typing import Optional

from config.settings import CONFIRMATION_KEYWORDS, TOOLS_ALWAYS_REQUIRING_CONFIRMATION
from core.tools_schema import READ_ONLY_TOOLS


def requires_confirmation(tool_name: str, reason: Optional[str], enabled: bool) -> bool:
    if not enabled:
        return False
    if tool_name in READ_ONLY_TOOLS:
        return False
    if tool_name in TOOLS_ALWAYS_REQUIRING_CONFIRMATION:
        return True

    haystack = f"{tool_name} {reason or ''}".lower()
    return any(keyword in haystack for keyword in CONFIRMATION_KEYWORDS)
