"""Pluggable MT backends."""
from __future__ import annotations

import importlib
import os
from typing import Callable

from .base import MTBackend

_BACKENDS: dict[str, Callable[[], MTBackend]] = {}


def register_backend(name: str, factory: Callable[[], MTBackend], *,
                     replace: bool = False) -> None:
    if name in _BACKENDS and not replace:
        raise ValueError(f"MT backend already registered: {name}")
    _BACKENDS[name] = factory


def load_plugins(spec: str | None = None) -> None:
    spec = spec if spec is not None else os.environ.get("NCAT_MT_PLUGINS", "")
    for item in [part.strip() for part in spec.replace(";", ",").split(",")]:
        if not item or "=" not in item:
            continue
        name, target = [part.strip() for part in item.split("=", 1)]
        if ":" not in target:
            raise ValueError(f"MT plugin expects module:factory, got {target!r}")
        module_name, factory_name = target.split(":", 1)
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        register_backend(name, factory, replace=True)


def get_backend(name: str) -> MTBackend:
    load_plugins()
    if name in _BACKENDS:
        return _BACKENDS[name]()
    if name == "llama_cpp":
        from .llama_cpp import LlamaCppBackend
        return LlamaCppBackend()
    if name == "openai_compatible":
        from .llama_cpp import OpenAICompatibleBackend
        return OpenAICompatibleBackend()
    if name == "openai_responses":
        from .openai_responses import OpenAIResponsesBackend
        return OpenAIResponsesBackend()
    if name == "anthropic":
        from .anthropic import AnthropicBackend
        return AnthropicBackend()
    raise KeyError(
        f"unknown MT backend {name!r}; register with NCAT_MT_PLUGINS "
        "or use llama_cpp")
