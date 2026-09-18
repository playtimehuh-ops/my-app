"""
Design tokens for the dark, modern UI. Centralised here so every panel
uses the same palette/spacing instead of ad hoc colors scattered through
ui/main_window.py.

Tkinter's ttk theming engine on Windows (the "vista" theme) stubbornly
overrides colors on several ttk widgets, so panels that need real color
(cards, the header, the log, buttons) are built from plain `tk` widgets
with these colors set directly, rather than fighting ttk styles.
"""

# --- Palette -----------------------------------------------------------------
BG = "#0F1115"            # window background
BG_PANEL = "#171A21"      # card / panel background
BG_PANEL_ALT = "#1B1F28"  # slightly lighter panel (nested sections)
BG_INPUT = "#20242E"      # entries / inputs
BORDER = "#2A2F3A"

TEXT_PRIMARY = "#E8EAED"
TEXT_SECONDARY = "#9AA3B2"
TEXT_MUTED = "#6B7280"

ACCENT = "#5B8CFF"
ACCENT_SOFT = "#243357"
ACCENT_TEXT = "#CFE0FF"

SUCCESS = "#3DDC84"
WARNING = "#FFB020"
DANGER = "#FF5470"
DANGER_DARK = "#D93A55"
DANGER_DARKER = "#B22E45"

# --- Activity log category colors --------------------------------------------
LOG_COLORS = {
    "SYSTEM": TEXT_SECONDARY,
    "AI": ACCENT_TEXT,
    "OBSERVE": "#8AD1FF",
    "ACTION": SUCCESS,
    "WAIT": TEXT_MUTED,
    "WARNING": WARNING,
    "ERROR": DANGER,
    "STOP": DANGER,
}

# --- Fonts ---------------------------------------------------------------------
FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"

FONT_TITLE = (FONT_FAMILY, 15, "bold")
FONT_SECTION = (FONT_FAMILY, 10, "bold")
FONT_BODY = (FONT_FAMILY, 10)
FONT_SMALL = (FONT_FAMILY, 9)
FONT_SMALL_BOLD = (FONT_FAMILY, 9, "bold")
FONT_MONO = (MONO_FAMILY, 9)
FONT_BIG_STAT = (FONT_FAMILY, 18, "bold")

# --- Spacing -------------------------------------------------------------------
PAD = 10
PAD_SM = 6
RADIUS = 10
