"""ASR backend registry.

No ASR backend is shipped in the package. Local models (for example NVIDIA
NeMo Parakeet), subprocess workers, and cloud APIs all differ in how they are
invoked, so each deployment registers its own backend through the same
``ASRBackend`` contract. The registry only resolves names and factories; it
never imports a model or a provider SDK.

Library/tests can call :func:`register_backend`. The CLI loads custom backends
from ``NCAT_ASR_PLUGINS`` (``name=module:factory``; comma or semicolon
separated, factory called with no arguments).
"""
from __future__ import annotations

import importlib
import os
import re
from dataclasses import asdict
from typing import Callable

from .base import ASRBackend, ASRCapabilities, ASRError, ASRRequest

ENV_PLUGINS = "NCAT_ASR_PLUGINS"

_BACKENDS: dict[str, Callable[[], ASRBackend]] = {}


def register_backend(name: str, factory: Callable[[], ASRBackend], *,
                     replace: bool = False) -> None:
    """Register a custom backend factory."""
    name = name.strip()
    if not name:
        raise ASRError("backend name must not be empty")
    if name in _BACKENDS and not replace:
        raise ASRError(f"ASR backend already registered: {name}")
    _BACKENDS[name] = factory


def load_plugins(spec: str | None = None, *,
                 replace: bool = False) -> list[str]:
    """Load ``name=module:factory`` entries from ``NCAT_ASR_PLUGINS``.

    The factory is called with no arguments and must return an ASRBackend.
    Semicolons or commas separate entries; the name defaults to the factory
    attribute name. Example::

        NCAT_ASR_PLUGINS="parakeet=my_asr:ParakeetBackend"
    """
    raw = spec if spec is not None else os.environ.get(ENV_PLUGINS, "")
    loaded: list[str] = []
    for entry in re.split(r"[,;]", raw or ""):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            name, target = entry.split("=", 1)
        else:
            name, target = "", entry
        module_name, separator, attribute = target.partition(":")
        if not separator or not module_name.strip() or not attribute.strip():
            raise ASRError(
                f"invalid ASR plugin entry {entry!r}; expected "
                "name=module:factory or module:factory")
        try:
            module = importlib.import_module(module_name.strip())
        except ImportError as error:
            raise ASRError(
                f"cannot import ASR plugin module {module_name!r}: {error}"
            ) from error
        factory = getattr(module, attribute.strip(), None)
        if factory is None:
            raise ASRError(
                f"ASR plugin {entry!r} has no attribute {attribute!r}")
        if not callable(factory):
            raise ASRError(f"ASR plugin factory is not callable: {entry!r}")
        chosen = name.strip() or attribute.strip()
        if chosen in _BACKENDS and _BACKENDS[chosen] is factory:
            loaded.append(chosen)
            continue
        register_backend(chosen, factory, replace=replace)
        loaded.append(chosen)
    return loaded


def get_backend(name: str) -> ASRBackend:
    try:
        factory = _BACKENDS[name]
    except KeyError as error:
        registered = ", ".join(sorted(_BACKENDS)) or "none"
        raise ASRError(
            f"unknown ASR backend {name!r}; registered: {registered}. "
            "Register a custom backend with NCAT_ASR_PLUGINS="
            "name=module:factory"
        ) from error
    return factory()


def available_backends() -> list[str]:
    return sorted(_BACKENDS)


def describe_backends() -> list[dict]:
    """Machine-readable backend names and capabilities for ``--list-backends``."""
    descriptions = []
    for name in available_backends():
        backend = get_backend(name)
        descriptions.append({
            "name": name,
            "capabilities": asdict(backend.capabilities),
        })
    return descriptions


__all__ = [
    "ASRBackend", "ASRCapabilities", "ASRError", "ASRRequest",
    "get_backend", "available_backends", "register_backend", "load_plugins",
    "describe_backends",
]
