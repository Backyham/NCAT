"""Anthropic Messages API MT backend."""
from __future__ import annotations

from ..api import ApiError, api_endpoint, request_json
from .base import MTCapabilities, MTRequest, MTResult, Usage
from .prompt import build_messages, parse_response


class AnthropicBackend:
    name = "anthropic"
    capabilities = MTCapabilities(
        supports_batch=True, supports_json=True, local=False,
        prompt_style="anthropic",
        notes="Anthropic Messages API (/v1/messages)",
    )

    def translate(self, request: MTRequest) -> MTResult:
        options = request.options
        base_url = str(options.get("base_url") or "")
        model = str(options.get("model") or "")
        if not base_url:
            raise RuntimeError("anthropic backend requires base_url")
        if not model:
            raise RuntimeError("anthropic backend requires model")
        headers = {"anthropic-version": "2023-06-01"}
        api_key = str(options.get("api_key") or "")
        if api_key:
            headers["x-api-key"] = api_key
        headers.update(options.get("extra_headers") or {})
        payload = {
            "model": model,
            "max_tokens": int(options.get("max_tokens", 4096)),
            "temperature": float(options.get("temperature", 0)),
            "messages": [{
                "role": "user",
                "content": build_messages(request)[0]["content"],
            }],
        }
        try:
            body = request_json(
                api_endpoint(base_url, "/messages"),
                json_body=payload, headers=headers,
                timeout=float(options.get("timeout", 600)),
                retries=int(options.get("retries", 2)),
                retry_backoff=float(options.get("retry_backoff", 1.0)),
            )
        except ApiError as exc:
            raise RuntimeError(f"MT anthropic request failed: {exc}") from exc
        content = ""
        for block in body.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                content += str(block.get("text") or "")
        usage_data = body.get("usage") or {}
        return MTResult(
            segments=parse_response(content, request),
            provider=self.name, model=model,
            usage=Usage(
                input_tokens=usage_data.get("input_tokens"),
                output_tokens=usage_data.get("output_tokens"),
            ),
            raw={"content": content},
        )
