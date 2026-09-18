"""
AI Computer Agent - entry point.

Run with:  python main.py
Build with: see build/build.bat or README.md
"""

import sys
import tkinter as tk
import traceback

from core.api_key_manager import ApiKeyManager


def _install_crash_guard(root: tk.Tk):
    """Make sure an unexpected exception never leaks the API key: every
    uncaught exception is redacted before being shown or written anywhere."""

    def handle(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        safe_text = ApiKeyManager.redact(text)
        print(safe_text, file=sys.stderr)
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "AI Computer Agent - unexpected error",
                "An unexpected error occurred and the app may need to restart.\n\n"
                + safe_text[-1500:],
            )
        except Exception:
            pass

    def tk_report_callback_exception(exc_type, exc, tb):
        handle(exc_type, exc, tb)

    root.report_callback_exception = tk_report_callback_exception
    sys.excepthook = handle


def main():
    root = tk.Tk()
    _install_crash_guard(root)

    # Imported after Tk root exists / crash guard installed, so import-time
    # errors on non-Windows dev machines still show a friendly message.
    from ui.main_window import MainWindow

    MainWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
