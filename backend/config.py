"""Model paths and runtime configuration.

Resolution order, first hit wins:

1. environment variable (``NCAT_ASR_MODEL``, ...)
2. ``[tool.ncat]`` in ``pyproject.toml``
3. empty

Nothing here has a baked-in path. The repository carries the *mechanism* for
pointing at local model files; the values belong to the machine running it,
so they are supplied by whoever installs.
"""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# pyproject key -> environment variable
_MODEL_KEYS: dict[str, str] = {
    'models_root': 'NCAT_MODELS_ROOT',
    'asr_model': 'NCAT_ASR_MODEL',
    'aligner_model': 'NCAT_ALIGNER_MODEL',
    'mt_model': 'NCAT_MT_MODEL',
    'llm_model': 'NCAT_LLM_MODEL',
}

_SCALAR_KEYS: dict[str, str] = {
    'data_dir': 'NCAT_DATA_DIR',
    'mt_url': 'NCAT_MT_URL',
}

DEFAULTS: dict[str, str] = {
    'mt_url': 'http://127.0.0.1:8088',
}


@lru_cache(maxsize=1)
def _pyproject_config() -> dict[str, str]:
    pyproject = BASE_DIR / 'pyproject.toml'
    if not pyproject.is_file():
        return {}
    with pyproject.open('rb') as fh:
        section = tomllib.load(fh).get('tool', {}).get('ncat', {}) or {}
    return {k: str(v) for k, v in section.items() if v}


def model_path(key: str) -> str:
    """Return a configured model path, or '' when unset.

    An empty result is meaningful: it means "not configured", which the
    web UI surfaces through ``/api/models`` so a missing model is visible
    before a pipeline run rather than after it.
    """
    env_name = _MODEL_KEYS.get(key)
    if env_name:
        value = os.environ.get(env_name, '').strip()
        if value:
            return value
    return _pyproject_config().get(key, '')


def model_status() -> dict[str, dict[str, object]]:
    """Every known model key with its resolved path and existence."""
    out: dict[str, dict[str, object]] = {}
    for key in _MODEL_KEYS:
        if key == 'models_root':
            continue
        path = model_path(key)
        out[key] = {'path': path, 'present': bool(path) and Path(path).exists()}
    return out


def setting(key: str) -> str:
    """Non-model scalar setting (data dir, MT URL, ...)."""
    env_name = _SCALAR_KEYS.get(key)
    if env_name:
        value = os.environ.get(env_name, '').strip()
        if value:
            return value
    return DEFAULTS.get(key, '')
