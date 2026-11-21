"""OpenAI Responses API MT backend."""
from __future__ import annotations

from ..api import ApiError, api_endpoint, request_json, resolve_api_key
from .base import MTCapabilities, MTRequest, MTResult, Usage
from .prompt import build_messages, parse_response


class OpenAIResponsesBackend:
    name = "openai_responses"
    capabilities = MTCapabilities(
        supports_batch=True, supports_json=True, local=False,
        prompt_style="responses",
        notes="OpenAI Responses API (/v1/responses)",
    )

    def translate(self, request: MTRequest) -> MTResult:
        options = request.options
        base_url = str(options.get("base_url") or "")
        model = str(options.get("model") or "")
        if not base_url:
            raise RuntimeError("openai responses backend requires base_url")
        if not model:
            raise RuntimeError("openai responses backend requires model")
        prompt = build_messages(request)[0]["content"]
        payload = {
            "model": model,
            "input": [{
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }],
            "max_output_tokens": int(options.get("max_tokens", 4096)),
        }
        try:
            body = request_json(
                api_endpoint(base_url, "/responses"),
                json_body=payload,
                api_key=resolve_api_key(
                    options.get("api_key"), provider="mt"),
                extra_headers=options.get("extra_headers"),
                timeout=float(options.get("timeout", 600)),
                retries=int(options.get("retries", 2)),
                retry_backoff=float(options.get("retry_backoff", 1.0)),
            )
        except ApiError as exc:
            raise RuntimeError(f"MT responses request failed: {exc}") from exc
        content = str(body.get("output_text") or "")
        if not content:
            for item in body.get("output") or []:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") in {
                            "output_text", "text"}:
                        content += str(part.get("text") or "")
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
