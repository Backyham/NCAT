"""Deterministic QA checks for segments and cues."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from .project import new_id, now_iso
from .subs.rules import cue_metrics
from .text import normalise_key
from .types import (
    Cue, QAItem, QASeverity, QAStatus, Segment, Term, TermStatus,
)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _item(project_id: str, kind: str, severity: QASeverity, message: str,
          *, segment_id: str | None = None, cue_id: str | None = None,
          evidence: str = "", suggestion: str = "") -> QAItem:
    return QAItem(
        id=new_id("qa"), project_id=project_id, kind=kind,
        severity=severity, message=message, segment_id=segment_id,
        cue_id=cue_id, evidence=evidence, suggestion=suggestion,
        created_at=now_iso(), updated_at=now_iso(),
    )


def run_qa(project_id: str, segments: Iterable[Segment],
           cues: Iterable[Cue], terms: Iterable[Term] = ()) -> list[QAItem]:
    segment_list = list(segments)
    cue_list = list(cues)
    term_list = list(terms)
    items: list[QAItem] = []

    for segment in segment_list:
        if not segment.target.strip():
            items.append(_item(project_id, "empty_target", QASeverity.ERROR,
                               "Target is empty", segment_id=segment.id,
                               evidence=segment.source))
            continue
        if normalise_key(segment.source) == normalise_key(segment.target):
            items.append(_item(project_id, "source_equals_target",
                               QASeverity.WARNING,
                               "Source and target are identical",
                               segment_id=segment.id,
                               evidence=segment.source))
        source_numbers = set(_NUMBER.findall(segment.source))
        target_numbers = set(_NUMBER.findall(segment.target))
        if source_numbers != target_numbers:
            items.append(_item(project_id, "number_mismatch",
                               QASeverity.WARNING,
                               "Numbers differ between source and target",
                               segment_id=segment.id,
                               evidence=f"{sorted(source_numbers)} -> "
                                        f"{sorted(target_numbers)}"))

    for term in term_list:
        if term.status != TermStatus.PREFERRED and not term.forbidden:
            continue
        key = normalise_key(term.source)
        if not key:
            continue
        for segment in segment_list:
            if key not in normalise_key(segment.source):
                continue
            if term.forbidden:
                if term.target and normalise_key(term.target) in \
                        normalise_key(segment.target):
                    items.append(_item(
                        project_id, "forbidden_term", QASeverity.ERROR,
                        f"Forbidden term used: {term.source}",
                        segment_id=segment.id, evidence=segment.target))
            elif term.target and normalise_key(term.target) not in \
                    normalise_key(segment.target):
                items.append(_item(
                    project_id, "term_missing", QASeverity.WARNING,
                    f"Preferred term missing: {term.source} -> {term.target}",
                    segment_id=segment.id, evidence=segment.target))

    for cue in cue_list:
        metrics = cue_metrics(cue)
        for warning in metrics["warnings"]:
            severity = (QASeverity.ERROR if warning in
                        {"missing_timing", "duration_too_short"}
                        else QASeverity.WARNING)
            items.append(_item(project_id, warning, severity,
                               f"Cue readability: {warning}",
                               cue_id=cue.id,
                               evidence=cue.target_text or cue.source_text))
    ordered = sorted(cue_list, key=lambda item: (
        item.start_ms if item.start_ms is not None else 10**15,
        item.order))
    for left, right in zip(ordered, ordered[1:]):
        if (left.end_ms is not None and right.start_ms is not None
                and right.start_ms < left.end_ms):
            items.append(_item(project_id, "timing_overlap",
                               QASeverity.ERROR,
                               "Cue timing overlaps the next cue",
                               cue_id=left.id,
                               evidence=f"{left.end_ms} > {right.start_ms}"))
    return items


def read_qa(path: str | Path) -> list[QAItem]:
    path = Path(path)
    if not path.is_file():
        return []
    items = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        items.append(QAItem(
            id=row["id"], project_id=row["project_id"], kind=row["kind"],
            severity=QASeverity(row.get("severity", "warning")),
            message=row.get("message", ""), segment_id=row.get("segment_id"),
            cue_id=row.get("cue_id"), evidence=row.get("evidence", ""),
            suggestion=row.get("suggestion", ""),
            status=QAStatus(row.get("status", "open")),
            created_at=row.get("created_at"), updated_at=row.get("updated_at"),
        ))
    return items


def write_qa(path: str | Path, items: Iterable[QAItem]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps({
                "id": item.id, "project_id": item.project_id,
                "kind": item.kind, "severity": str(item.severity),
                "message": item.message, "segment_id": item.segment_id,
                "cue_id": item.cue_id, "evidence": item.evidence,
                "suggestion": item.suggestion, "status": str(item.status),
                "created_at": item.created_at, "updated_at": item.updated_at,
            }, ensure_ascii=False) + "\n")
    temporary.replace(path)
    return path


def update_qa(items: Iterable[QAItem], item_id: str,
              status: QAStatus) -> QAItem:
    for item in items:
        if item.id == item_id:
            item.status = status
            item.updated_at = now_iso()
            return item
    raise KeyError(item_id)
