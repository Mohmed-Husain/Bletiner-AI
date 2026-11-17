"""Provider model discovery and minimal capability probing (DOC-06 sections 3-4).

Discovery is real: it issues the provider's own model-list request through
:class:`~bletiner.providers.http_transport.HttpTransport` and returns only the ids that endpoint
actually reported. Nothing is hardcoded into a successful discovery result.

Endpoints (mirrors the wire formats used by the adapters in this package):

====================  ==============================================  ==========================
provider              endpoint                                        auth
====================  ==============================================  ==========================
openai                GET https://api.openai.com/v1/models             Bearer
deepseek              GET https://api.deepseek.com/models              Bearer
anthropic             GET https://api.anthropic.com/v1/models          x-api-key + version
ollama                GET http://localhost:11434/api/tags              none
custom                GET <base_url>/models                            Bearer if provided
====================  ==============================================  ==========================

:func:`curated_models` exists only as a clearly-labeled offline fallback for the UI when discovery
fails; those entries are never mixed into a successful discovery result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from bletiner.models.errors import AuthenticationError, ModelUnavailableError, ProviderError
from bletiner.providers.http_transport import HttpTransport

__all__ = [
    "ANTHROPIC_VERSION",
    "BASE_URLS",
    "PROVIDERS",
    "DiscoveredModel",
    "curated_models",
    "default_probe_model",
    "discover_models",
    "models_url",
    "normalize_ollama_base",
    "normalize_provider",
    "probe_model",
]

ANTHROPIC_VERSION = "2023-06-01"

PROVIDERS: dict[str, str] = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "ollama": "Ollama (local)",
    "custom": "Custom OpenAI-compatible endpoint",
}

BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "anthropic": "https://api.anthropic.com/v1",
    "ollama": "http://localhost:11434",
}

_ALIASES: dict[str, str] = {
    "claude": "anthropic",
    "gpt": "openai",
    "open-ai": "openai",
    "local": "ollama",
    "ollama-local": "ollama",
    "openai-compatible": "custom",
    "openai_compatible": "custom",
    "custom-openai": "custom",
}

# Providers that cannot list models without credentials.
_KEY_REQUIRED = frozenset({"openai", "deepseek", "anthropic"})

_DEFAULT_PROBE_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "anthropic": "claude-3-5-haiku-latest",
}

# Clearly-labeled offline fallback only. These are commonly published ids, never verification results.
_CURATED: dict[str, tuple[tuple[str, str], ...]] = {
    "anthropic": (
        ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
        ("claude-opus-4-1", "Claude Opus 4.1"),
    ),
    "openai": (
        ("gpt-4.1", "GPT-4.1"),
        ("gpt-4o", "GPT-4o"),
        ("gpt-4o-mini", "GPT-4o mini"),
        ("o4-mini", "o4-mini"),
    ),
    "deepseek": (
        ("deepseek-chat", "DeepSeek Chat"),
        ("deepseek-reasoner", "DeepSeek Reasoner"),
    ),
    "ollama": (),
    "custom": (),
}

_PROBE_PROMPT = "Reply with exactly: BLETINER_OK"
_PROBE_EXPECTED = "BLETINER_OK"


@dataclass(frozen=True)
class DiscoveredModel:
    """One model reported by a provider's own model-list endpoint (or a labeled curated fallback)."""

    provider: str
    model_id: str
    display_name: str
    context_window: int | None = None
    supports_tools: bool | None = None
    verified: bool = False


def normalize_provider(provider: str) -> str:
    """Map user/alias spellings to a canonical provider id or raise an actionable error."""
    key = (provider or "").strip().lower()
    key = _ALIASES.get(key, key)
    if key not in PROVIDERS:
        supported = ", ".join(PROVIDERS)
        raise ProviderError(f"Unsupported provider {provider!r}. Supported providers: {supported}.")
    return key


def normalize_ollama_base(base_url: str | None) -> str:
    """Ollama native endpoints live at the daemon root; tolerate a legacy trailing /v1 or /api."""
    base = (base_url or BASE_URLS["ollama"]).strip().rstrip("/")
    for suffix in ("/v1", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
    return base or BASE_URLS["ollama"]


def models_url(provider: str, base_url: str | None = None) -> str:
    """Return the exact model-list URL for *provider* (honest: this is what Discover will call)."""
    prov = normalize_provider(provider)
    if prov == "custom" and not (base_url or "").strip():
        raise ProviderError(
            "The custom provider needs an OpenAI-compatible base URL, e.g. http://localhost:8080/v1."
        )
    base = (base_url or BASE_URLS.get(prov, "")).strip().rstrip("/")
    if not base:
        raise ProviderError(f"No base URL is configured for provider {PROVIDERS[prov]}.")
    if prov == "ollama":
        return f"{normalize_ollama_base(base)}/api/tags"
    if prov == "anthropic":
        return f"{base}/models?limit=1000"
    return f"{base}/models"


def default_probe_model(provider: str) -> str:
    """Cheapest sensible model id for a minimal inference probe, when the user has not chosen one."""
    try:
        prov = normalize_provider(provider)
    except ProviderError:
        return ""
    return _DEFAULT_PROBE_MODELS.get(prov, "")


def _missing_key_message(prov: str) -> str:
    return (
        f"No API key configured for {PROVIDERS[prov]}. Add one in Settings -> Providers, "
        "or use the local Ollama provider."
    )


def _auth_headers(prov: str, api_key: str | None) -> dict[str, str]:
    key = (api_key or "").strip()
    headers: dict[str, str] = {"Accept": "application/json"}
    if prov == "anthropic":
        if not key:
            raise AuthenticationError(_missing_key_message(prov))
        headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        return headers
    if prov == "ollama":
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers
    if prov in _KEY_REQUIRED and not key:
        raise AuthenticationError(_missing_key_message(prov))
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _fetch_models(prov: str, url: str, headers: dict[str, str], transport: Any) -> Any:
    payload = transport.get(url, headers)
    if prov != "anthropic" or not isinstance(payload, dict):
        return payload
    first_page = payload.get("data")
    if not isinstance(first_page, list):
        return payload
    collected: list[Any] = list(first_page)
    pages = 1
    while payload.get("has_more") and pages < 5:
        last_item = first_page[-1] if first_page else None
        last_id = payload.get("last_id") or (last_item.get("id") if isinstance(last_item, dict) else None)
        if not last_id:
            break
        separator = "&" if "?" in url else "?"
        payload = transport.get(f"{url}{separator}after_id={quote(str(last_id))}", headers)
        page = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(page, list) or not page:
            break
        collected.extend(page)
        pages += 1
    return {"data": collected}


def _entries(prov: str, payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "models", "model_list"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    label = PROVIDERS.get(prov, prov)
    raise ProviderError(
        f"Unexpected model-list payload from {label}: expected a JSON object with a 'data' or "
        f"'models' list, got {type(payload).__name__}."
    )


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _parse_models(prov: str, payload: Any) -> list[DiscoveredModel]:
    models: list[DiscoveredModel] = []
    seen: set[str] = set()
    for entry in _entries(prov, payload):
        model_id = ""
        display_name = ""
        context_window: int | None = None
        supports_tools: bool | None = None
        if isinstance(entry, str):
            model_id = entry.strip()
            display_name = model_id
        elif isinstance(entry, dict):
            model_id = str(entry.get("id") or entry.get("name") or entry.get("model") or "").strip()
            display_name = str(
                entry.get("display_name") or entry.get("name") or entry.get("model") or model_id
            ).strip()
            context_window = _as_int(
                entry.get("context_window")
                or entry.get("context_length")
                or entry.get("max_context_tokens")
                or entry.get("max_input_tokens")
            )
            supports_tools = (
                entry.get("supports_tools") if isinstance(entry.get("supports_tools"), bool) else None
            )
        else:
            continue
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(
            DiscoveredModel(
                provider=prov,
                model_id=model_id,
                display_name=display_name or model_id,
                context_window=context_window,
                supports_tools=supports_tools,
                verified=False,
            )
        )
    return models


def discover_models(
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    transport: Any = None,
) -> list[DiscoveredModel]:
    """Query *provider* for the models its endpoint actually reports.

    Raises :class:`AuthenticationError`, :class:`ModelUnavailableError` or
    :class:`ProviderError` with actionable messages; callers (panels) surface them inline.
    """
    prov = normalize_provider(provider)
    url = models_url(prov, base_url)
    headers = _auth_headers(prov, api_key)
    client = transport if transport is not None else HttpTransport(timeout_s=10.0)
    try:
        payload = _fetch_models(prov, url, headers, client)
    except AuthenticationError as exc:
        raise AuthenticationError(
            f"{PROVIDERS[prov]} rejected the API key while listing models (HTTP 401). "
            f"Check the key in Settings -> Providers. {exc}"
        ) from exc
    except ModelUnavailableError as exc:
        raise ModelUnavailableError(
            f"{PROVIDERS[prov]} has no model-list endpoint at {url} (HTTP 404). Check the base URL. {exc}"
        ) from exc
    except ProviderError as exc:
        raise ProviderError(f"Model discovery failed for {PROVIDERS[prov]}: {exc}") from exc
    models = _parse_models(prov, payload)
    if not models:
        raise ModelUnavailableError(
            f"{PROVIDERS[prov]} returned an empty model list from {url}. "
            "The key may lack model-list permission, or no models are installed/pulled."
        )
    return models


def probe_model(
    provider: str,
    model_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    transport: Any = None,
) -> tuple[bool, str]:
    """Send one minimal real inference request and report the measured result.

    Returns ``(ok, message)`` and never raises: the panel shows the message verbatim. The prompt is
    fixed and ``max_tokens`` is tiny, so a commercial call costs a negligible amount of tokens --
    the UI still asks for explicit confirmation before probing paid providers (DOC-06 section 4.3).
    """
    try:
        prov = normalize_provider(provider)
    except ProviderError as exc:
        return False, str(exc)
    model = (model_id or "").strip()
    if not model:
        return False, "No model id selected. Run Discover and pick a model first."
    if prov == "custom" and not (base_url or "").strip():
        return False, "The custom provider needs an OpenAI-compatible base URL before it can be probed."
    client = transport if transport is not None else HttpTransport(timeout_s=20.0)
    started = time.perf_counter()
    try:
        content = _probe_request(prov, model, client, api_key, base_url)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        reply = (content or "").strip()
        if _PROBE_EXPECTED in reply:
            return True, (
                f"{PROVIDERS[prov]} model '{model}' answered a minimal test prompt correctly "
                f"in {elapsed_ms:.0f} ms."
            )
        preview = reply[:40] if reply else "<empty>"
        return True, (
            f"{PROVIDERS[prov]} model '{model}' responded in {elapsed_ms:.0f} ms "
            f"(reply {preview!r}; expected {_PROBE_EXPECTED!r}) - connectivity confirmed."
        )
    except AuthenticationError as exc:
        return False, (
            f"Authentication rejected for {PROVIDERS[prov]} (HTTP 401). "
            f"Check the API key in Settings -> Providers. {exc}"
        )
    except ModelUnavailableError as exc:
        return False, (
            f"Model '{model}' or endpoint not found for {PROVIDERS[prov]} (HTTP 404). "
            f"The model may be unavailable - rediscover models. {exc}"
        )
    except ProviderError as exc:
        return False, f"Probe failed for {PROVIDERS[prov]} model '{model}': {exc}"
    except Exception as exc:
        return False, f"Unexpected probe failure for {PROVIDERS[prov]} model '{model}': {exc}"


def _probe_request(prov: str, model: str, client: Any, api_key: str | None, base_url: str | None) -> str:
    key = (api_key or "").strip()
    if prov == "ollama":
        base = normalize_ollama_base(base_url)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        payload = client.post(
            f"{base}/api/chat",
            headers,
            {
                "model": model,
                "messages": [{"role": "user", "content": _PROBE_PROMPT}],
                "stream": False,
                "options": {"num_predict": 8},
            },
        )
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            raise ProviderError("malformed Ollama /api/chat response (missing 'message')")
        return str(message.get("content") or "")
    base = (base_url or BASE_URLS[prov]).strip().rstrip("/")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if prov == "anthropic":
        if not key:
            raise AuthenticationError(_missing_key_message(prov))
        headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        payload = client.post(
            f"{base}/messages",
            headers,
            {
                "model": model,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": _PROBE_PROMPT}],
            },
        )
        blocks = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(blocks, list):
            raise ProviderError("malformed Anthropic response (missing 'content')")
        return "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
    if prov in _KEY_REQUIRED and not key:
        raise AuthenticationError(_missing_key_message(prov))
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = client.post(
        f"{base}/chat/completions",
        headers,
        {
            "model": model,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": _PROBE_PROMPT}],
        },
    )
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ProviderError("malformed OpenAI-compatible response (missing 'choices')")
    first = choices[0] if isinstance(choices[0], dict) else None
    message = first.get("message") if first else None
    if not isinstance(message, dict):
        raise ProviderError("malformed OpenAI-compatible response (missing 'choices[0].message')")
    return str(message.get("content") or "")


def curated_models(provider: str) -> list[DiscoveredModel]:
    """Clearly-labeled offline fallback ids for the Models panel (empty for local/custom providers).

    These entries are *not* discovery results: the UI must render them under a
    "curated fallback (discovery failed) - unverified" label and encourage a retry.
    """
    try:
        prov = normalize_provider(provider)
    except ProviderError:
        return []
    return [
        DiscoveredModel(
            provider=prov, model_id=model_id, display_name=display_name or model_id, verified=False
        )
        for model_id, display_name in _CURATED.get(prov, ())
    ]
