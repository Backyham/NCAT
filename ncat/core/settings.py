"""Workspace-local provider settings for ASR and MT.

Providers are independent records: the same service can have local and cloud
providers side by side. Each service selects one active provider; there is no
implicit local/cloud fallback. An empty provider list means both services are
unconfigured.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path
from typing import Any

SETTINGS_DIR = ".ncat"
SETTINGS_FILE = "settings.json"

ASR_API_TYPES = {"openai_audio"}
MT_API_TYPES = {"openai_chat", "openai_responses", "anthropic"}


def _provider_defaults(service: str) -> dict[str, Any]:
    return {
        "id": "",
        "service": service,
        "provider_id": "",
        "provider_name": "",
        "api_type": "openai_audio" if service == "asr" else "openai_chat",
        "base_url": "",
        "api_key": "",
        "headers": {},
        "ignore_health": False,
        "default_model": "",
        "models": [],
        "model_names": {},
        "model_tags": {},
    }


DEFAULTS: dict[str, Any] = {
    "providers": [],
    "asr": {"active_provider_id": ""},
    "mt": {"active_provider_id": ""},
}


def settings_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / SETTINGS_DIR / SETTINGS_FILE


def _new_provider_id() -> str:
    return f"prov_{uuid.uuid4().hex[:12]}"


def _clean_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key).strip(): str(item).strip()
        for key, item in value.items()
        if str(key).strip()
    }


def _clean_tags(value: Any) -> dict[str, dict[str, bool]]:
    if not isinstance(value, dict):
        return {}
    tags: dict[str, dict[str, bool]] = {}
    for key, item in value.items():
        if not str(key).strip() or not isinstance(item, dict):
            continue
        tags[str(key).strip()] = {
            "reasoning": bool(item.get("reasoning")),
            "image": bool(item.get("image")),
        }
    return tags


def _sanitize_provider(raw: dict[str, Any], *,
                       existing: dict[str, Any] | None = None,
                       validate: bool = False) -> dict[str, Any]:
    service = str(raw.get("service") or "asr").strip().lower()
    if service not in {"asr", "mt"}:
        raise ValueError(f"unknown provider service: {service}")
    provider = copy.deepcopy(_provider_defaults(service))
    provider["id"] = str(
        raw.get("id") or (existing or {}).get("id") or _new_provider_id()
    ).strip()
    for key in ("provider_id", "provider_name", "base_url", "default_model"):
        provider[key] = str(raw.get(key, provider[key]) or "").strip()[:512]
    if not provider["default_model"] and raw.get("model"):
        provider["default_model"] = str(raw["model"]).strip()[:512]
    allowed_types = ASR_API_TYPES if service == "asr" else MT_API_TYPES
    api_type = str(raw.get("api_type") or provider["api_type"])
    if api_type in allowed_types:
        provider["api_type"] = api_type
    elif validate:
        raise ValueError(f"invalid API type for {service}: {api_type}")
    provider["headers"] = _clean_mapping(raw.get("headers"))
    if isinstance(raw.get("models"), list):
        provider["models"] = [
            str(item).strip() for item in raw["models"] if str(item).strip()
        ][:500]
    provider["model_names"] = _clean_mapping(raw.get("model_names"))
    provider["model_tags"] = _clean_tags(raw.get("model_tags"))

    key = str((existing or {}).get("api_key") or "")
    if raw.get("clear_api_key"):
        key = ""
    elif raw.get("api_key"):
        key = str(raw["api_key"]).strip()
    provider["api_key"] = key
    provider["ignore_health"] = bool(
        raw.get("ignore_health",
                (existing or {}).get("ignore_health", False)))

    if validate:
        if not provider["base_url"]:
            raise ValueError(f"{service.upper()} provider needs a base URL")
        if service == "mt" and not provider["default_model"]:
            raise ValueError("MT provider needs a default model")
    return provider


def _legacy_providers(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one-per-service settings written before providers became a list."""
    providers: list[dict[str, Any]] = []
    for service in ("asr", "mt"):
        old = values.get(service)
        if not isinstance(old, dict):
            continue
        if not (old.get("configured") or old.get("base_url")
                or old.get("provider_id")):
            continue
        item = dict(old)
        item["service"] = service
        item["id"] = str(old.get("id") or f"migrated-{service}")
        providers.append(item)
    return providers


def _read_values(workspace: str | Path) -> dict[str, Any]:
    path = settings_path(workspace)
    if not path.is_file():
        return {}
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return values if isinstance(values, dict) else {}


def load_settings(workspace: str | Path) -> dict[str, Any]:
    values = _read_values(workspace)
    raw_providers = values.get("providers")
    if not isinstance(raw_providers, list):
        raw_providers = _legacy_providers(values)
    providers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_providers:
        if not isinstance(raw, dict):
            continue
        try:
            provider = _sanitize_provider(raw)
        except ValueError:
            continue
        if provider["id"] in seen:
            provider["id"] = _new_provider_id()
        seen.add(provider["id"])
        providers.append(provider)

    active: dict[str, str] = {}
    for service in ("asr", "mt"):
        old = values.get(service)
        ref = str(old.get("active_provider_id") or "") if isinstance(old, dict) else ""
        candidates = [item for item in providers if item["service"] == service]
        if not any(item["id"] == ref for item in candidates):
            ref = candidates[0]["id"] if candidates else ""
        active[service] = ref
    return {
        "providers": providers,
        "asr": {"active_provider_id": active["asr"]},
        "mt": {"active_provider_id": active["mt"]},
    }


def save_settings(workspace: str | Path, values: dict[str, Any]) -> dict[str, Any]:
    current = load_settings(workspace)
    existing = {item["id"]: item for item in current["providers"]}
    raw_providers = values.get("providers")
    if not isinstance(raw_providers, list):
        raw_providers = _legacy_providers(values)
    providers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_providers:
        if not isinstance(raw, dict):
            continue
        provider = _sanitize_provider(
            raw, existing=existing.get(str(raw.get("id") or "")),
            validate=True)
        if provider["id"] in seen:
            raise ValueError(f"duplicate provider id: {provider['id']}")
        seen.add(provider["id"])
        providers.append(provider)

    active: dict[str, str] = {}
    for service in ("asr", "mt"):
        old = values.get(service)
        ref = str(old.get("active_provider_id") or "") if isinstance(old, dict) else ""
        candidates = [item for item in providers if item["service"] == service]
        if not any(item["id"] == ref for item in candidates):
            ref = candidates[0]["id"] if candidates else ""
        active[service] = ref

    result = {
        "providers": providers,
        "asr": {"active_provider_id": active["asr"]},
        "mt": {"active_provider_id": active["mt"]},
    }
    path = settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    return result


def _public_provider(provider: dict[str, Any]) -> dict[str, Any]:
    configured = bool(provider.get("api_key"))
    payload = {key: copy.deepcopy(value)
               for key, value in provider.items() if key != "api_key"}
    payload["api_key_set"] = configured
    payload["api_key_source"] = "settings" if configured else "none"
    payload["api_key_hint"] = (provider["api_key"][-4:]
                               if configured and len(provider["api_key"]) >= 4
                               else "")
    return payload


def public_settings(workspace: str | Path) -> dict[str, Any]:
    current = load_settings(workspace)
    return {
        "providers": [_public_provider(item)
                      for item in current["providers"]],
        "asr": dict(current["asr"]),
        "mt": dict(current["mt"]),
    }


class ProviderNotConfigured(Exception):
    def __init__(self, service: str) -> None:
        super().__init__(
            f"{service.upper()} has no active provider; add one in Settings")
        self.service = service


def _active_provider(settings: dict[str, Any], service: str) -> dict[str, Any]:
    ref = settings[service]["active_provider_id"]
    for provider in settings["providers"]:
        if provider["service"] == service and provider["id"] == ref:
            return provider
    for provider in settings["providers"]:
        if provider["service"] == service:
            return provider
    raise ProviderNotConfigured(service)


def _provider_headers(provider: dict[str, Any]) -> dict | None:
    headers = provider.get("headers") or {}
    cleaned = {str(key).strip(): str(value)
               for key, value in headers.items() if str(key).strip()}
    return cleaned or None


def _asr_route(provider: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {"base_url": provider["base_url"]}
    if provider.get("api_key"):
        options["api_key"] = provider["api_key"]
    headers = _provider_headers(provider)
    if headers:
        options["extra_headers"] = headers
    return {
        "backend": "openai",
        "model": provider.get("default_model") or None,
        "options": options,
    }


def _mt_route(provider: dict[str, Any]) -> dict[str, Any]:
    base_url = provider["base_url"]
    api_type = provider.get("api_type", "openai_chat")
    local = base_url.startswith(("http://127.", "http://localhost",
                                 "http://[::1]"))
    if api_type == "openai_responses":
        backend = "openai_responses"
    elif api_type == "anthropic":
        backend = "anthropic"
    elif local:
        backend = "llama_cpp"
    else:
        backend = "openai_compatible"
    options: dict[str, Any] = {"base_url": base_url}
    if provider.get("api_key"):
        options["api_key"] = provider["api_key"]
    headers = _provider_headers(provider)
    if headers:
        options["extra_headers"] = headers
    model = provider.get("default_model") or (
        "local" if backend == "openai_compatible" else "")
    return {"backend": backend, "model": model, "options": options}


def apply_action_defaults(workspace: str | Path,
                          action: str, body: dict[str, Any]) -> dict[str, Any]:
    """Fill ASR/MT routing from the active provider for the service."""
    settings = load_settings(workspace)
    result = dict(body)
    body_options = dict(result.get("options") or {})
    if action == "transcribe":
        route = _asr_route(_active_provider(settings, "asr"))
        result["backend"] = route["backend"]
        if route["model"] and not result.get("model"):
            result["model"] = route["model"]
        result["options"] = {**route["options"], **body_options}
    elif action == "translate":
        route = _mt_route(_active_provider(settings, "mt"))
        result["backend"] = route["backend"]
        result["base_url"] = (result.get("base_url")
                              or route["options"].get("base_url"))
        if route["model"] and not result.get("model"):
            result["model"] = route["model"]
        result["options"] = {**route["options"], **body_options}
    return result
