"""
Fixes the recursive screen-preview bug at its root: this app's own windows
(the main window and the AI-cursor overlay) are marked so Windows leaves
them out of ANY screen capture - including the very screenshot this app
takes of the desktop for the AI and for its own "Live Desktop" preview.

Without this, a screenshot taken while the app is on screen includes the
app's own live-preview panel, which shows the previous screenshot, which
included the panel again, and so on - the "infinite mirror" bug.

How: `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)`. This is a
real Win32 API (available since Windows 10 version 2004, mid-2020) used
for exactly this class of problem - e.g. it's how password managers and
DRM-protected video players keep their own window out of screenshots and
screen recordings while it stays fully visible to the person using the
computer. Unlike the older WDA_MONITOR flag, EXCLUDEFROMCAPTURE does not
paint a black box in the capture - the window is treated as not present
at all, so whatever's behind it shows through.

Practical implication worth knowing (documented for the README too): a
window with this flag set also won't appear if the person screen-shares
or records their screen with some other tool while this app is open, or
in any other screenshot they take on the machine, while it remains
perfectly visible to them locally. That's the correct tradeoff for a
tool whose entire job is capturing the desktop, but it's worth knowing
about ahead of time.

On Windows versions older than 10 2004, or on non-Windows platforms
(e.g. this code being reviewed/tested on Linux), this degrades to a
harmless no-op (returns False) rather than raising - callers should
proceed either way and just log a warning if it returns False.

Because a single, shared screenshot function is used both for what the
AI sees and for the UI's own "Live Desktop" preview, this one fix
eliminates the recursion for both at the source, rather than needing two
separate capture pipelines that must each avoid the other.
"""

import ctypes
import platform

WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 2004+

IS_WINDOWS = platform.system() == "Windows"


def _hwnd_from_tk(tk_widget) -> int:
    """Tkinter's winfo_id() on Windows returns a *child* drawable id, not
    the real top-level HWND that Windows' window manager (and this API)
    operate on - Tk wraps every toplevel in its own frame window. Walking
    up one GetParent() gets the actual top-level HWND. This is the
    standard, widely-used recipe for reaching into a Tkinter window's
    native handle on Windows."""
    child_id = tk_widget.winfo_id()
    return ctypes.windll.user32.GetParent(child_id)


def exclude_from_capture(tk_widget) -> bool:
    """Best-effort. Returns True if the exclusion was actually applied."""
    if not IS_WINDOWS:
        return False
    try:
        hwnd = _hwnd_from_tk(tk_widget)
        if not hwnd:
            return False
        ok = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        return bool(ok)
    except Exception:
        return False
