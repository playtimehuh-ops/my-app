"""
Small, dependency-free themed widgets used to build the dark, modern UI in
ui/main_window.py, built from plain `tk` primitives (see config/theme.py's
docstring for why - ttk fights back on Windows for colored widgets).
"""

import tkinter as tk

from config import theme


class RoundedCard(tk.Frame):
    """A panel with rounded corners and a subtle border, drawn on a Canvas
    (Tkinter has no native rounded-rect widget). Put child widgets inside
    `.body`, a plain Frame, using normal pack/grid - the rounded background
    just redraws itself to match on every resize."""

    def __init__(self, parent, bg=theme.BG_PANEL, border=theme.BORDER, radius=theme.RADIUS, **kwargs):
        parent_bg = parent.cget("bg") if "bg" in parent.keys() else theme.BG
        super().__init__(parent, bg=parent_bg, **kwargs)
        self._bg = bg
        self._border = border
        self._radius = radius
        self.canvas = tk.Canvas(self, bg=parent_bg, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        w, h = max(event.width, 10), max(event.height, 10)
        self.canvas.delete("bg_shape")
        self._round_rect(1, 1, w - 1, h - 1, radius=self._radius,
                          fill=self._bg, outline=self._border, tags="bg_shape")
        self.canvas.tag_lower("bg_shape")
        self.canvas.coords(self._window, 6, 6)
        self.canvas.itemconfig(self._window, width=max(w - 12, 1), height=max(h - 12, 1))

    def _round_rect(self, x1, y1, x2, y2, radius=12, **kwargs):
        r = radius
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)


class SectionLabel(tk.Label):
    """A small caps-style card header, e.g. 'TASK', 'LIVE DESKTOP'."""

    def __init__(self, parent, text, **kwargs):
        super().__init__(
            parent, text=text.upper(), bg=parent.cget("bg"),
            fg=theme.TEXT_SECONDARY, font=theme.FONT_SECTION, anchor="w", **kwargs
        )


class ToggleSwitch(tk.Canvas):
    """A small ON/OFF pill toggle. `command(new_state: bool)` fires on click."""

    WIDTH, HEIGHT = 44, 22

    def __init__(self, parent, initial=True, command=None, **kwargs):
        super().__init__(
            parent, width=self.WIDTH, height=self.HEIGHT,
            bg=parent.cget("bg"), highlightthickness=0, **kwargs
        )
        self._state = bool(initial)
        self._command = command
        self.bind("<Button-1>", self._on_click)
        self._redraw()

    def _on_click(self, _event):
        self.set(not self._state, fire=True)

    def get(self) -> bool:
        return self._state

    def set(self, value: bool, fire: bool = False):
        self._state = bool(value)
        self._redraw()
        if fire and self._command:
            self._command(self._state)

    def _redraw(self):
        self.delete("all")
        track_color = theme.SUCCESS if self._state else theme.BG_INPUT
        outline = theme.SUCCESS if self._state else theme.BORDER
        r = self.HEIGHT / 2
        self.create_oval(0, 0, self.HEIGHT, self.HEIGHT, fill=track_color, outline=outline)
        self.create_oval(self.WIDTH - self.HEIGHT, 0, self.WIDTH, self.HEIGHT, fill=track_color, outline=outline)
        self.create_rectangle(r, 0, self.WIDTH - r, self.HEIGHT, fill=track_color, outline=track_color)
        knob_x = self.WIDTH - r if self._state else r
        self.create_oval(knob_x - r + 3, 3, knob_x + r - 3, self.HEIGHT - 3, fill="#FFFFFF", outline="")


class StatusPill(tk.Label):
    """Colored '● STATE' label used for the header status and log-style
    indicators; call `.set(text, color)` to update."""

    def __init__(self, parent, text="IDLE", color=theme.TEXT_SECONDARY, **kwargs):
        super().__init__(
            parent, text=f"\u25CF {text}", bg=parent.cget("bg"), fg=color,
            font=theme.FONT_SMALL_BOLD, **kwargs
        )

    def set(self, text: str, color: str):
        self.configure(text=f"\u25CF {text}", fg=color)


def stat_block(parent, label_text: str):
    """A small 'value over label' block used in the Request Statistics
    card, e.g. a big '37' over a small 'Requests'. Returns the value Label
    so the caller can update its text."""
    frame = tk.Frame(parent, bg=parent.cget("bg"))
    value_label = tk.Label(frame, text="0", bg=parent.cget("bg"), fg=theme.TEXT_PRIMARY, font=theme.FONT_BIG_STAT)
    value_label.pack(anchor="w")
    tk.Label(frame, text=label_text, bg=parent.cget("bg"), fg=theme.TEXT_SECONDARY, font=theme.FONT_SMALL).pack(anchor="w")
    return frame, value_label
