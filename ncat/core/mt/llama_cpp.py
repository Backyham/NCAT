"""OpenAI-compatible llama.cpp MT backend (HTTP client only)."""
from __future__ import annotations

from ..api import ApiError, api_endpoint, request_json, resolve_api_key
from .base import (
    MTCapabilities, MTRequest, MTResult, Usage,
)
from .prompt import build_messages, parse_response


class LlamaCppBackend:
    name = "llama_cpp"
    capabilities = MTCapabilities(
        supports_batch=True, supports_json=True, local=True,
        prompt_style="chat",
        notes="llama.cpp /v1/chat/completions; start llama-server separately",
    )

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}

    def translate(self, request: MTRequest) -> MTResult:
        options = {**self.options, **request.options}
        base_url = str(options.get("base_url") or "http://127.0.0.1:8080")
        model = str(options.get("model") or "local")
        payload = {
            "model": model,
            "messages": build_messages(request),
            "temperature": float(options.get("temperature", 0)),
            "max_tokens": int(options.get("max_tokens", 4096)),
            "stream": False,
        }
        local_endpoint = base_url.startswith((
            "http://127.", "http://localhost", "http://[::1]"))
        template_kwargs = options.get(
            "chat_template_kwargs",
            {"enable_thinking": False} if local_endpoint else False)
        if template_kwargs is not False and template_kwargs is not None:
            payload["chat_template_kwargs"] = (
                template_kwargs if isinstance(template_kwargs, dict)
                else {"enable_thinking": False})
        api_key = resolve_api_key(options.get("api_key"), provider="mt")
        endpoint = api_endpoint(base_url, "/chat/completions")
        try:
            body = request_json(
                endpoint, json_body=payload, api_key=api_key,
                extra_headers=options.get("extra_headers"),
                timeout=float(options.get("timeout", 600)),
                retries=int(options.get("retries", 2)),
                retry_backoff=float(options.get("retry_backoff", 1.0)),
            )
        except ApiError as exc:
            raise RuntimeError(f"MT request failed: {exc}") from exc
        choices = body.get("choices") or []
        content = ""
        if choices:
            content = ((choices[0].get("message") or {}).get("content")
                       or choices[0].get("text") or "")
        segments = parse_response(content, request)
        usage_data = body.get("usage") or {}
        return MTResult(
            segments=segments,
            provider=self.name,
            model=model,
            usage=Usage(
                input_tokens=usage_data.get("prompt_tokens"),
                output_tokens=usage_data.get("completion_tokens"),
            ),
            raw={"content": content},
        )


class OpenAICompatibleBackend(LlamaCppBackend):
    """Cloud OpenAI-compatible chat endpoint (API key via header)."""

    name = "openai_compatible"
    capabilities = MTCapabilities(
        supports_batch=True, supports_json=True, local=False,
        prompt_style="chat",
        notes="OpenAI-compatible /v1/chat/completions; API key from "
              "NCAT_MT_API_KEY or api_key option",
    )

    def __init__(self, options: dict | None = None) -> None:
        super().__init__({"chat_template_kwargs": False, **(options or {})})
