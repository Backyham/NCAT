"""Shared OpenAI-style HTTP/API-key helpers for ASR and MT backends."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from ncat import __version__

API_KEY_PRIMARY = "NCAT_API_KEY"
API_KEY_FALLBACKS = ("OPENAI_API_KEY",)


class ApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None,
                 code: str = "api_error",
                 body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body


def resolve_api_key(explicit: str | None = None, *,
                    provider: str | None = None) -> str | None:
    """Resolve a key from explicit value, NCAT_API_KEY, OpenAI, provider env."""
    candidates = [explicit, os.environ.get(API_KEY_PRIMARY)]
    for name in API_KEY_FALLBACKS:
        candidates.append(os.environ.get(name))
    if provider:
        candidates.append(os.environ.get(f"NCAT_{provider.upper()}_API_KEY"))
    for value in candidates:
        if value and str(value).strip():
            return str(value).strip()
    return None


def auth_headers(api_key: str | None, *,
                 extra_headers: dict[str, Any] | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update({str(key): str(value)
                        for key, value in extra_headers.items()})
    return headers


def api_endpoint(base_url: str, path: str) -> str:
    """Join a base URL and an API path, accepting a base with or without /v1."""
    base = (base_url or "").rstrip("/")
    if not base:
        raise ApiError("base_url is required", code="invalid_argument")
    path = path if path.startswith("/") else "/" + path
    if base.endswith("/v1"):
        return base + (path[3:] if path.startswith("/v1/") else path)
    if path.startswith("/v1/"):
        return base + path
    return base + "/v1" + path


def multipart_body(fields: dict[str, Any],
                   files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----ncat-" + uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="{key}"'
                 "\r\n\r\n").encode()
        body += f"{value}\r\n".encode()
    for field, filename, content, content_type in files:
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="{field}"; '
                 f'filename="{filename}"\r\n').encode()
        body += f"Content-Type: {content_type}\r\n\r\n".encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def request_json(
    url: str,
    *,
    method: str = "POST",
    json_body: Any = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    api_key: str | None = None,
    extra_headers: dict[str, Any] | None = None,
    timeout: float = 600,
    retries: int = 2,
    retry_backoff: float = 1.0,
) -> Any:
    request_headers = {
        "Content-Type": "application/json",
        "User-Agent": f"NCAT/{__version__}",
    }
    if headers:
        request_headers.update(headers)
    request_headers.update(auth_headers(api_key, extra_headers=extra_headers))
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method=method, headers=request_headers)
    last_error: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            payload = None
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                pass
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                raise ApiError(
                    f"HTTP {exc.code}: {exc.reason}", status=exc.code,
                    code="http_error", body=payload) from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt >= retries:
                raise ApiError(f"request failed: {exc}",
                               code="network_error") from exc
            last_error = exc
        time.sleep(max(0.0, retry_backoff) * (2 ** attempt))
    raise ApiError(f"request failed: {last_error}", code="request_failed")
