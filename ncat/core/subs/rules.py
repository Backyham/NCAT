"""Subtitle readability metrics and timing checks."""
from __future__ import annotations

import re
from typing import Any, Iterable

from ..text import cps
from ..types import Cue

_TAG = re.compile(r"\{[^}]*\}")

DEFAULT_MAX_CPS_SOURCE = 20.0
DEFAULT_MAX_CPS_TARGET = 17.0
DEFAULT_MAX_LINE_LENGTH = 42
DEFAULT_MAX_LINES = 2
DEFAULT_MIN_DURATION_MS = 500


def visible_text(text: str) -> str:
    value = _TAG.sub("", text or "")
    return value.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")


def text_lines(text: str) -> list[str]:
    return [line.strip() for line in visible_text(text).splitlines() if line.strip()]


def cue_metrics(
    cue: Cue,
    *,
    max_cps_source: float = DEFAULT_MAX_CPS_SOURCE,
    max_cps_target: float = DEFAULT_MAX_CPS_TARGET,
    max_line_length: int = DEFAULT_MAX_LINE_LENGTH,
    max_lines: int = DEFAULT_MAX_LINES,
    min_duration_ms: int = DEFAULT_MIN_DURATION_MS,
) -> dict[str, Any]:
    duration = None
    if cue.start_ms is not None and cue.end_ms is not None:
        duration = max(0, cue.end_ms - cue.start_ms)
    source_lines = text_lines(cue.source_text)
    target_lines = text_lines(cue.target_text)
    source_length = max((len(line) for line in source_lines), default=0)
    target_length = max((len(line) for line in target_lines), default=0)
    source_cps = cps(visible_text(cue.source_text), duration or 0)
    target_cps = cps(visible_text(cue.target_text), duration or 0)
    warnings: list[str] = []
    if duration is None:
        warnings.append("missing_timing")
    elif duration < min_duration_ms:
        warnings.append("duration_too_short")
    if source_cps is not None and source_cps > max_cps_source:
        warnings.append("source_cps")
    if target_cps is not None and target_cps > max_cps_target:
        warnings.append("target_cps")
    if max(source_length, target_length) > max_line_length:
        warnings.append("line_too_long")
    if max(len(source_lines), len(target_lines)) > max_lines:
        warnings.append("too_many_lines")
    if not cue.target_text.strip():
        warnings.append("empty_target")
    return {
        "duration_ms": duration,
        "cps_source": round(source_cps, 2) if source_cps is not None else None,
        "cps_target": round(target_cps, 2) if target_cps is not None else None,
        "line_length": max(source_length, target_length),
        "lines": max(len(source_lines), len(target_lines)),
        "warnings": warnings,
    }


def apply_cue_metrics(cue: Cue, **kwargs: Any) -> dict[str, Any]:
    metrics = cue_metrics(cue, **kwargs)
    cue.cps_source = metrics["cps_source"]
    cue.cps_target = metrics["cps_target"]
    cue.line_length = metrics["line_length"]
    cue.lines = metrics["lines"]
    return metrics


def apply_all(cues: Iterable[Cue], **kwargs: Any) -> list[dict[str, Any]]:
    return [apply_cue_metrics(cue, **kwargs) for cue in cues]
