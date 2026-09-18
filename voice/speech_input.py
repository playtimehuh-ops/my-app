"""
Voice input via the `speech_recognition` package.

By default this uses SpeechRecognition's free Google Web Speech API
recognizer (`recognize_google`), which needs no API key but does require
an internet connection and is rate-limited / best-effort (it is a free
demo endpoint, not an SLA'd product). If you need fully offline
recognition, swap `_recognize` to use a local engine such as Vosk or
Whisper - the rest of the app only depends on this class's public
methods, not on which engine is behind them.

Threading: `listen_once_async` runs recognition on a background thread
and delivers the result via callbacks on that same thread; callers
(the GUI) must marshal back to the UI thread themselves (e.g. via
`root.after`), the same as any other background work in this app.
"""

import threading
from dataclasses import dataclass
from typing import Callable, Optional

try:
    import speech_recognition as sr
except Exception:  # pragma: no cover
    sr = None


class VoiceInputError(Exception):
    pass


@dataclass
class VoiceInputResult:
    text: str


class SpeechInput:
    def __init__(self):
        self._available = sr is not None
        self._recognizer = sr.Recognizer() if self._available else None
        self._listening = False
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return self._available

    def is_listening(self) -> bool:
        return self._listening

    def listen_once_async(
        self,
        on_result: Callable[[str], None],
        on_error: Callable[[str], None],
        on_status: Optional[Callable[[str], None]] = None,
        timeout: float = 8.0,
        phrase_time_limit: float = 15.0,
    ):
        if not self._available:
            on_error(
                "Voice input is unavailable: the 'speech_recognition' / "
                "'PyAudio' packages are not installed. See README.md."
            )
            return

        def worker():
            with self._lock:
                self._listening = True
            try:
                if on_status:
                    on_status("Listening...")
                with sr.Microphone() as source:
                    self._recognizer.adjust_for_ambient_noise(source, duration=0.4)
                    audio = self._recognizer.listen(
                        source, timeout=timeout, phrase_time_limit=phrase_time_limit
                    )
                if on_status:
                    on_status("Recognizing...")
                text = self._recognizer.recognize_google(audio)
                on_result(text)
            except sr.WaitTimeoutError:
                on_error("No speech detected in time.")
            except sr.UnknownValueError:
                on_error("Could not understand the audio.")
            except sr.RequestError as e:
                on_error(f"Speech recognition service error: {e}")
            except OSError as e:
                on_error(f"Microphone error (is one connected/permitted?): {e}")
            except Exception as e:  # noqa: BLE001
                on_error(f"Voice input error: {e}")
            finally:
                with self._lock:
                    self._listening = False

        threading.Thread(target=worker, daemon=True).start()
