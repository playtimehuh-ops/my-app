"""
API key storage.

Security notes (read this before changing anything):
  * The key is written only to the OS credential store via the `keyring`
    package. On Windows this is the Windows Credential Manager (DPAPI
    protected, tied to the logged-in user account).
  * The key is never written to any application config file, log file,
    or the SQLite/JSON app state.
  * The full key is never logged, and it is never placed into any prompt
    or message sent to the model - the model only ever sees screenshots
    and short text, and has no tool that could read this module's memory.
  * `get_key()` is used only by openrouter_client.py to build the
    Authorization header of the HTTPS request. It is not exposed to the
    UI once saved; the UI only ever calls `get_masked_key()`.

Honesty note: because this is a local, unencrypted-at-rest-from-the-OS
perspective desktop app running under the user's own Windows account,
a sufficiently privileged process (e.g. something else running as the
same user, or the user themselves with debugging tools) can still
recover the key -- Windows Credential Manager protects the key from
*other user accounts* and from casual disk inspection, not from the
machine's own administrator/owner. We do not claim otherwise.
"""

import re
from typing import Optional

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from config.settings import CRED_SERVICE_NAME, CRED_USERNAME

_KEY_PATTERN = re.compile(r"^sk-or-[A-Za-z0-9\-_]{8,}$")


class ApiKeyManager:
    def __init__(self):
        self._service = CRED_SERVICE_NAME
        self._username = CRED_USERNAME

    def is_valid_format(self, key: str) -> bool:
        """Loose sanity check only - real validation happens via a live API call."""
        return bool(key) and key.startswith("sk-or-") and len(key) >= 12

    def save_key(self, key: str) -> None:
        key = key.strip()
        if not key:
            raise ValueError("API key is empty.")
        try:
            keyring.set_password(self._service, self._username, key)
        except KeyringError as e:
            raise RuntimeError(
                "Could not write to the Windows credential store. "
                "The key was NOT saved."
            ) from e

    def get_key(self) -> Optional[str]:
        try:
            return keyring.get_password(self._service, self._username)
        except KeyringError:
            return None

    def has_key(self) -> bool:
        return bool(self.get_key())

    def remove_key(self) -> None:
        try:
            keyring.delete_password(self._service, self._username)
        except PasswordDeleteError:
            pass  # already absent - fine
        except KeyringError as e:
            raise RuntimeError("Could not remove the stored key.") from e

    @staticmethod
    def mask_key(key: Optional[str]) -> str:
        """sk-or-v1-abcdef...1234  ->  sk-or-••••••••••••1234"""
        if not key:
            return "(not set)"
        prefix = "sk-or-"
        tail = key[-4:] if len(key) >= 4 else key
        return f"{prefix}{'•' * 12}{tail}"

    @staticmethod
    def redact(text: str) -> str:
        """Utility for logger.py: strip anything that looks like an OpenRouter
        key out of a string before it is ever written to the activity log,
        an exception message, or a crash report."""
        if not text:
            return text
        return re.sub(r"sk-or-[A-Za-z0-9\-_]{6,}", "sk-or-[REDACTED]", text)
