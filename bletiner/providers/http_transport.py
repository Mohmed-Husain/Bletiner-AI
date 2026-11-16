"""Real HTTP transport for live LLM API calls (Anthropic, OpenAI, Ollama, DeepSeek).

Uses standard library ``urllib.request`` to avoid external network dependencies while maintaining
strict compliance with the :class:`~bletiner.providers.base.Transport` protocol. The transport is the
single seam where network failures are mapped to the typed provider errors the rest of the app uses:

* 401 -> :class:`~bletiner.models.errors.AuthenticationError` (bad or missing key)
* 404 -> :class:`~bletiner.models.errors.ModelUnavailableError` (wrong endpoint/model)
* 429 -> :class:`~bletiner.models.errors.ProviderError` (rate limited)
* network/timeout -> :class:`~bletiner.models.errors.ProviderError` with an actionable message

``post()`` serves the chat adapters; ``get()`` serves model discovery (DOC-06 section 4).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from bletiner.models.errors import AuthenticationError, ModelUnavailableError, ProviderError

_USER_AGENT = "Bletiner-AI/0.3.0"


class HttpTransport:
    """Standard HTTP JSON transport executing live requests to LLM APIs."""

    def __init__(self, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        """Execute a JSON POST request and return the parsed JSON object."""
        data = json.dumps(body).encode("utf-8")
        return self._request("POST", url, headers, data)

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        """Execute a GET request and return the parsed JSON payload (object or list).

        Used for provider model listing (``GET /models``, ``GET /api/tags``). Same error mapping
        as :meth:`post`.
        """
        return self._request("GET", url, headers or {}, None)

    def _request(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> Any:
        req_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
            **headers,
        }
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                if not raw.strip():
                    return {}
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
        except TimeoutError as exc:
            raise ProviderError(f"Request timed out after {self.timeout_s:.0f}s calling {url}.") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Provider returned invalid JSON from {url}: {exc}") from exc
        except Exception as exc:
            raise ProviderError(f"Unexpected error communicating with {url}: {exc}") from exc
