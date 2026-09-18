"""
A second, visually distinct cursor that shows where the AI is acting.

Implementation reality check (documented honestly, per the project's own
"don't overclaim" principle for the API key): stock Windows only has one
hardware mouse-pointer position. This overlay is a separate, click-through,
always-on-top, transparent window drawn with Tkinter that renders an
icon + "AI" tag and animates smoothly to each target. It gives a clear,
constant visual distinction between "this is the AI acting" and the
user's own pointer, and it moves independently for pure preview moves
(`ai_cursor_move`, which never touches the real pointer). For an action
that actually clicks or drags, Windows still requires the *real* pointer
to physically be at that location to register the click - the overlay
animates there first, then the real pointer follows to perform the click,
which is what makes the AI's clicks visibly distinct rather than an
instant, disorienting teleport of the user's own cursor.

This window is a Tkinter Toplevel, so it must be created on (and only
driven from) the main GUI thread. `AICursorOverlay.move_to()` is safe to
call from a background thread only if you route it through
`root.after(0, ...)` - `MainWindow` does this for you.
"""

import tkinter as tk

from utils.capture_protection import exclude_from_capture


class AICursorOverlay:
    SIZE = 64
    TRANSPARENT_COLOR = "#FF00FF"  # magenta, chosen as a color unlikely to appear in the icon

    def __init__(self, root: tk.Tk):
        self.root = root
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)  # no title bar/border
        self.top.attributes("-topmost", True)
        try:
            self.top.attributes("-transparentcolor", self.TRANSPARENT_COLOR)
        except tk.TclError:
            pass  # platform doesn't support color-key transparency
        try:
            self.top.attributes("-disabled", True)  # click-through-ish on Windows
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.top,
            width=self.SIZE,
            height=self.SIZE,
            bg=self.TRANSPARENT_COLOR,
            highlightthickness=0,
        )
        self.canvas.pack()

        self._x, self._y = -1000, -1000
        self._visible = False
        self._animating = False
        self._draw(state="idle")
        self.top.withdraw()

        # This overlay must never appear in the screenshots this app takes
        # (it's a human-facing indicator, not part of the real desktop) -
        # see utils/capture_protection.py for why/how. self.top must be
        # mapped (deiconify'd) at least once before Windows will accept a
        # display-affinity change on it, so this is retried on first show().
        self._capture_excluded = False

    # ------------------------------------------------------------------ #
    def show(self):
        if not self._visible:
            self.top.deiconify()
            self._visible = True
        if not self._capture_excluded:
            self._capture_excluded = exclude_from_capture(self.top)

    def hide(self):
        if self._visible:
            self.top.withdraw()
            self._visible = False

    # ------------------------------------------------------------------ #
    def move_to(self, x: int, y: int, animate: bool = True, steps: int = 12):
        """Smoothly animate the overlay's tip to screen coordinate (x, y)."""
        self.show()
        if not animate:
            self._place(x, y)
            return

        start_x, start_y = self._x, self._y
        if start_x < -500:  # first placement, nothing to animate from
            self._place(x, y)
            return

        def step(i=0):
            if i > steps:
                self._place(x, y)
                return
            t = i / steps
            ix = start_x + (x - start_x) * t
            iy = start_y + (y - start_y) * t
            self._place(int(ix), int(iy))
            self.root.after(8, lambda: step(i + 1))

        step()

    def animate_click(self):
        self._draw(state="click")
        self.root.after(180, lambda: self._draw(state="idle"))

    def animate_drag(self):
        self._draw(state="drag")

    def end_drag(self):
        self._draw(state="idle")

    # ------------------------------------------------------------------ #
    def _place(self, x: int, y: int):
        self._x, self._y = x, y
        # tip of the pointer glyph is near the top-left of the canvas
        self.top.geometry(f"+{x - 4}+{y - 4}")

    def _draw(self, state: str):
        c = self.canvas
        c.delete("all")
        color = {"idle": "#00E5FF", "click": "#FF3D71", "drag": "#FFC542"}.get(state, "#00E5FF")

        # Pointer glyph (simple arrow) with a dark outline for visibility on
        # any background, plus a pulsing ring on click.
        points = [4, 4, 4, 40, 14, 30, 20, 44, 26, 41, 20, 27, 34, 27]
        c.create_polygon(points, fill=color, outline="#101018", width=2)

        if state == "click":
            c.create_oval(0, 0, self.SIZE, self.SIZE, outline=color, width=3)

        # "AI" badge
        c.create_oval(30, 2, 62, 26, fill="#101018", outline=color, width=2)
        c.create_text(46, 14, text="AI", fill=color, font=("Segoe UI", 9, "bold"))

    # ------------------------------------------------------------------ #
    def destroy(self):
        try:
            self.top.destroy()
        except Exception:
            pass
