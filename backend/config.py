"""Model paths and runtime configuration.

Thin re-export of :mod:`pipeline.config`. The resolution logic lives in
``pipeline`` because the stages that load models need it and ``pipeline``
must not depend on ``backend``; this module keeps the ``backend.config``
import path stable for the web layer.
"""
from __future__ import annotations

from pipeline.config import (MODEL_DEFAULTS, MODEL_KEYS, SETTING_DEFAULTS,
                             SETTING_KEYS, is_local, model_path,
                             model_status, setting)

__all__ = [
    'MODEL_DEFAULTS', 'MODEL_KEYS', 'SETTING_DEFAULTS', 'SETTING_KEYS',
    'is_local', 'model_path', 'model_status', 'setting',
]