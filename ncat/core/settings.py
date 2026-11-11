"""Workspace-local UI settings: local/cloud mode and per-service API keys.

Model selection, endpoint and provider are backend-service settings, not
frontend settings. Local endpoints are configured by environment variables;
cloud endpoints/models likewise. The UI only chooses local vs cloud and stores
separate ASR/MT API keys (masked from reads).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SETTINGS_DIR = ".ncat"
SETTINGS_FILE = "settings.json"

DEFAULTS: dict[str, Any] = {
    "asr": {"mode": "local", "api_key": ""},
    "mt": {"mode": "local", "api_key": ""},
}

LOCAL_ASR_URL = "NCAT_ASR_LOCAL_URL"
LOCAL_MT_URL = "NCAT_MT_LOCAL_URL"
LOCAL_MT_MODEL = "NCAT_MT_LOCAL_MODEL"
CLOUD_ASR_URL = "NCAT_ASR_CLOUD_URL"
CLOUD_ASR_MODEL = "NCAT_ASR_CLOUD_MODEL"
CLOUD_MT_URL = "NCAT_MT_CLOUD_URL"
CLOUD_MT_MODEL = "NCAT_MT_CLOUD_MODEL"


def settings_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / SETTINGS_DIR / SETTINGS_FILE


def _merge(defaults: dict, values: dict) -> dict:
    result = {key: (dict(value) if isinstance(value, dict) else value)
              for key, value in defaults.items()}
    for key, value in (values or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update({
                inner_key: inner_value
                for inner_key, inner_value in value.items()
                if inner_key in result[key]
            })
    return result


def load_settings(workspace: str | Path) -> dict[str, Any]:
    path = settings_path(workspace)
    values: dict[str, Any] = {}
    if path.is_file():
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            values = {}
    current = _merge(DEFAULTS, values)
    # Migrate the old shared key once, without assuming both services use it.
    legacy = values.get("api_key")
    if legacy:
        for service in ("asr", "mt"):
            if not current[service].get("api_key"):
                current[service]["api_key"] = legacy
    return current


def save_settings(workspace: str | Path, values: dict[str, Any]) -> dict[str, Any]:
    current = load_settings(workspace)
    for service in ("asr", "mt"):
        incoming = values.get(service)
        if not isinstance(incoming, dict):
            continue
        if incoming.get("mode") in {"local", "cloud"}:
            current[service]["mode"] = incoming["mode"]
        if incoming.get("clear_api_key"):
            current[service]["api_key"] = ""
        elif incoming.get("api_key"):
            current[service]["api_key"] = str(incoming["api_key"]).strip()
    path = settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    return current


def _public_service(service: dict[str, Any],
                    provider: str) -> dict[str, Any]:
    configured = bool(service.get("api_key"))
    environment = bool(os.environ.get(f"NCAT_{provider.upper()}_API_KEY"))
    return {
        "mode": service.get("mode", "local"),
        "api_key_set": configured or environment,
        "api_key_source": ("settings" if configured else
                           "environment" if environment else "none"),
        "api_key_hint": (service["api_key"][-4:]
                         if configured and len(service["api_key"]) >= 4
                         else ""),
    }


def public_settings(workspace: str | Path) -> dict[str, Any]:
    current = load_settings(workspace)
    return {
        "asr": _public_service(current["asr"], "asr"),
        "mt": _public_service(current["mt"], "mt"),
    }


def apply_action_defaults(workspace: str | Path,
                          action: str, body: dict[str, Any]) -> dict[str, Any]:
    """Fill ASR/MT routing from UI mode plus server-side environment config."""
    settings = load_settings(workspace)
    result = dict(body)
    options = dict(result.get("options") or {})
    if action == "transcribe":
        service = settings["asr"]
        mode = service.get("mode", "local")
        result["backend"] = "openai"
        if mode == "local":
            options["base_url"] = (options.get("base_url")
                                   or os.environ.get(LOCAL_ASR_URL)
                                   or "http://127.0.0.1:8081")
            options.pop("api_key", None)
        else:
            options["base_url"] = (options.get("base_url")
                                   or os.environ.get(CLOUD_ASR_URL) or "")
            model = os.environ.get(CLOUD_ASR_MODEL, "")
            if model and not result.get("model"):
                result["model"] = model
            if service.get("api_key") and not options.get("api_key"):
                options["api_key"] = service["api_key"]
    elif action == "translate":
        service = settings["mt"]
        mode = service.get("mode", "local")
        if mode == "local":
            result["backend"] = "llama_cpp"
            result["base_url"] = (result.get("base_url")
                                  or os.environ.get(LOCAL_MT_URL)
                                  or "http://127.0.0.1:8080")
            local_model = os.environ.get(LOCAL_MT_MODEL, "")
            if local_model and not result.get("model"):
                result["model"] = local_model
            options.pop("api_key", None)
        else:
            result["backend"] = "openai_compatible"
            result["base_url"] = (result.get("base_url")
                                  or os.environ.get(CLOUD_MT_URL) or "")
            model = os.environ.get(CLOUD_MT_MODEL, "")
            if model and not result.get("model"):
                result["model"] = model
            if service.get("api_key") and not options.get("api_key"):
                options["api_key"] = service["api_key"]
    result["options"] = options
    return result
