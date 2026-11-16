"""Real HTTP transport for live LLM API calls (Anthropic, OpenAI, Ollama, DeepSeek).

Uses standard library ``urllib.request`` to avoid external network dependencies while maintaining
strict compliance with the :class:`~bletiner.providers.base.Transport` protocol.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from bletiner.models.errors import AuthenticationError, ModelUnavailableError, ProviderError


class HttpTransport:
    """Standard HTTP JSON transport executing live POST requests to LLM APIs."""

    def __init__(self, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        """Execute JSON POST request and return parsed JSON dictionary."""
        data = json.dumps(body).encode("utf-8")
        req_headers = {
            "Content-Type": "application/json",
            "User-Agent": "Bletiner-AI/0.3.0",
            **headers,
        }
        req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise AuthenticationError(
                    f"Authentication failed (HTTP 401) for {url}: Invalid or missing API key. {err_body}"
                ) from exc
            if exc.code == 404:
                raise ModelUnavailableError(
                    f"Model or endpoint not found (HTTP 404) at {url}: {err_body}"
                ) from exc
            if exc.code == 429:
                raise ProviderError(f"Rate limit exceeded (HTTP 429) from {url}: {err_body}") from exc
            raise ProviderError(f"Provider API HTTP error {exc.code} from {url}: {err_body}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Network connection failed calling {url}: {exc.reason}") from exc
        except Exception as exc:
            raise ProviderError(f"Unexpected error communicating with {url}: {exc}") from exc
