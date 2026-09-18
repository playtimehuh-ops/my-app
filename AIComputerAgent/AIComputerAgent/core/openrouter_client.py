"""
OpenRouter API client.

Model selection: this app sends every request with model =
"openrouter/free" (config.settings.FREE_MODELS_ROUTER_MODEL) - OpenRouter's
own Free Models Router, which picks a free model that supports the
request's needed features (image input, tool calling) on OpenRouter's
side. This app does not query /models to pick a specific vendor's free
model itself, and never hard-codes an individual model id - see
core/agent_loop.py for where the model string is actually used.

The API key is read fresh from ApiKeyManager for every request and is
placed ONLY in the Authorization header - never inside any message
content, never logged, never included in exceptions raised upward
(exceptions are redacted via ApiKeyManager.redact before being raised).
"""

import json
from typing import Any, Dict, List

import requests

from config.settings import (
    OPENROUTER_MODELS_ENDPOINT,
    OPENROUTER_CHAT_ENDPOINT,
    MODEL_CALL_TIMEOUT_SECONDS,
)
from core.api_key_manager import ApiKeyManager


class OpenRouterError(Exception):
    """`.kind` is one of:
    'invalid_key' | 'rate_limited' | 'model_unavailable' |
    'network' | 'server' | 'unknown'
    """

    def __init__(self, message: str, kind: str = "unknown", status: int = None):
        super().__init__(ApiKeyManager.redact(message))
        self.kind = kind
        self.status = status


class OpenRouterClient:
    def __init__(self, key_manager: ApiKeyManager):
        self._keys = key_manager
        self._session = requests.Session()

    # ------------------------------------------------------------------ #
    # Low-level helpers
    # ------------------------------------------------------------------ #
    def _auth_headers(self) -> Dict[str, str]:
        key = self._keys.get_key()
        if not key:
            raise OpenRouterError("No OpenRouter API key is configured.", "invalid_key")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Optional but recommended by OpenRouter for analytics/routing:
            "HTTP-Referer": "https://local.aicomputeragent.app",
            "X-Title": "AI Computer Agent",
        }

    def _raise_for_response(self, resp: requests.Response):
        if resp.status_code == 200:
            return
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message", resp.text)
        except Exception:
            msg = resp.text

        if resp.status_code == 401:
            raise OpenRouterError(f"Invalid or revoked API key: {msg}", "invalid_key", 401)
        if resp.status_code == 429:
            raise OpenRouterError(f"Rate limited by OpenRouter: {msg}", "rate_limited", 429)
        if resp.status_code in (400, 404) and (
            "model" in msg.lower() or "not found" in msg.lower()
        ):
            raise OpenRouterError(f"Model unavailable: {msg}", "model_unavailable", resp.status_code)
        if 500 <= resp.status_code < 600:
            raise OpenRouterError(f"OpenRouter server error: {msg}", "server", resp.status_code)
        raise OpenRouterError(f"OpenRouter request failed: {msg}", "unknown", resp.status_code)

    # ------------------------------------------------------------------ #
    # Connection check (used by the setup screen / header status dot)
    # ------------------------------------------------------------------ #
    def check_connection(self) -> bool:
        """Cheap call that only confirms the API key is accepted by
        OpenRouter. Does not select or return a model - model choice is
        entirely delegated to the 'openrouter/free' router at request time."""
        try:
            resp = self._session.get(
                OPENROUTER_MODELS_ENDPOINT,
                headers=self._auth_headers(),
                timeout=MODEL_CALL_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise OpenRouterError(f"Network error checking connection: {e}", "network") from e
        self._raise_for_response(resp)
        return True

    def verify_key(self) -> bool:
        try:
            return self.check_connection()
        except OpenRouterError as e:
            if e.kind == "invalid_key":
                return False
            raise

    # ------------------------------------------------------------------ #
    # Chat completion (vision + tool calling)
    # ------------------------------------------------------------------ #
    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 1000,
        timeout: int = MODEL_CALL_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        }
        try:
            resp = self._session.post(
                OPENROUTER_CHAT_ENDPOINT,
                headers=self._auth_headers(),
                data=json.dumps(payload),
                timeout=timeout,
            )
        except requests.Timeout as e:
            raise OpenRouterError(f"Request to model timed out: {e}", "network") from e
        except requests.RequestException as e:
            raise OpenRouterError(f"Network error calling model: {e}", "network") from e

        self._raise_for_response(resp)
        return resp.json()

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass
