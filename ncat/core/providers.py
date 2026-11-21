"""Provider discovery helpers for the settings page.

The UI can ask NCAT to list models from an OpenAI-compatible endpoint or an
Anthropic endpoint. This is a local, user-initiated request; failures are
returned to the settings page so the user can enter a model manually.
"""
from __future__ import annotations

from typing import Any

from .api import ApiError, api_endpoint, request_json

SUPPORTED_API_TYPES = {
    "openai_audio", "openai_chat", "openai_responses", "anthropic",
}


class ProviderError(Exception):
    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


def fetch_models(base_url: str, api_type: str, *, api_key: str | None = None,
                 headers: dict[str, Any] | None = None,
                 timeout: float = 15) -> list[str]:
    """Return model ids advertised by a provider's ``/models`` endpoint."""
    base_url = (base_url or "").strip()
    if not base_url:
        raise ProviderError("base URL is required", code="missing_base_url")
    if api_type not in SUPPORTED_API_TYPES:
        raise ProviderError(f"unsupported API type: {api_type}",
                            code="invalid_api_type")
    request_headers = {str(key): str(value)
                       for key, value in (headers or {}).items()
                       if str(key).strip()}
    bearer_key = api_key or None
    if api_type == "anthropic":
        request_headers.setdefault("anthropic-version", "2023-06-01")
        if api_key:
            request_headers.setdefault("x-api-key", api_key)
        bearer_key = None
    try:
        payload = request_json(
            api_endpoint(base_url, "/models"), method="GET",
            headers=request_headers, api_key=bearer_key,
            timeout=timeout, retries=0,
        )
    except ApiError as error:
        raise ProviderError(str(error), code=error.code) from error
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = (payload.get("data") or payload.get("models")
                 or payload.get("result") or [])
    else:
        items = []
    models: list[str] = []
    for item in items:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = (item.get("id") or item.get("name")
                    or item.get("model") or item.get("slug"))
        else:
            name = None
        if name:
            value = str(name)
            if value not in models:
                models.append(value)
    return sorted(models)
