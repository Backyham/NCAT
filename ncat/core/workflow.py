"""Project workflow: derive a Kanban stage from artifacts, with manual override.

The board is a *view*: stages are computed from files and segment statuses.
Only ``Subproject.workflow`` holds human decisions (override, archive, due,
priority, labels, blocked). Nothing else is stored for the board.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .project import now_iso
from .qa import read_qa
from .types import QAStatus
from .workspace import WorkspaceProject, save_workspace, subproject_state

STAGES: list[tuple[str, str]] = [
    ("intake", "待素材"),
    ("transcribe", "待转写"),
    ("segment", "待分段"),
    ("translate", "翻译中"),
    ("review", "待审校"),
    ("export", "待导出"),
    ("delivered", "已交付"),
    ("archived", "已归档"),
]
STAGE_KEYS = [key for key, _ in STAGES]
STAGE_LABELS = dict(STAGES)

STATUS_ORDER = [
    "empty", "mt_suggested", "tm_suggested", "translated", "reviewed",
    "approved", "rejected",
]
STATUS_LABELS = {
    "empty": "空",
    "mt_suggested": "机器翻译建议",
    "tm_suggested": "记忆库建议",
    "translated": "已翻译",
    "reviewed": "已审阅",
    "approved": "已确认",
    "rejected": "已退回",
    "drawing": "绘图 / 非翻译",
}

WORKFLOW_DEFAULTS: dict[str, Any] = {
    "stage_override": None,
    "archived": False,
    "due": None,
    "priority": "normal",
    "labels": [],
    "blocked": {"reason": "", "since": None},
    "owner": "",
    "waiting_on": "",
    "revision": 0,
    "updated_at": None,
}


def workflow_of(sub) -> dict:
    data = dict(WORKFLOW_DEFAULTS)
    data.update(getattr(sub, "workflow", None) or {})
    if not isinstance(data.get("blocked"), dict):
        data["blocked"] = dict(WORKFLOW_DEFAULTS["blocked"])
    if not isinstance(data.get("labels"), list):
        data["labels"] = []
    return data


def _mtime(path: Path | None) -> float:
    try:
        return path.stat().st_mtime if path and path.is_file() else 0.0
    except OSError:
        return 0.0


def _iso(timestamp: float) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _content_mtime(paths) -> float:
    return max(_mtime(paths.cues), _mtime(paths.segments), _mtime(paths.qa))


def _export_mtime(paths) -> float:
    candidates = []
    for pattern in ("*.ass", "*.mp4", "*.mkv"):
        candidates.extend(paths.renders.glob(pattern))
    return max((_mtime(item) for item in candidates), default=0.0)


def _last_activity(paths) -> str | None:
    timestamps = [_content_mtime(paths), _export_mtime(paths),
                  _mtime(paths.root / "audit.jsonl")]
    return _iso(max(timestamps)) if any(timestamps) else None


def counts(segments) -> dict[str, int]:
    counter = Counter(str(getattr(segment.status, "value", segment.status))
                      for segment in segments)
    return {key: counter.get(key, 0) for key in STATUS_ORDER}


def derive(project: WorkspaceProject, sub, state) -> dict:
    """Return the derived stage plus the evidence used to reach it."""
    paths = state.paths
    segments = _read_segments(paths)
    cues = _read_cues(paths)
    by_status = counts(segments)
    total = len(segments)
    approved = by_status.get("approved", 0)
    empty = by_status.get("empty", 0)
    pending_suggestion = (by_status.get("mt_suggested", 0)
                          + by_status.get("tm_suggested", 0))
    source = state.source
    has_media = bool(source and (source.proxy_path or source.audio_path))
    transcript = (paths.asr / "transcript.json").is_file()
    content_mtime = _content_mtime(paths)
    export_mtime = _export_mtime(paths)
    qa_items = read_qa(paths.qa)
    qa_open = sum(1 for item in qa_items if item.status == QAStatus.OPEN)

    if not has_media:
        stage = "intake"
    elif not transcript:
        stage = "transcribe"
    elif not cues or total == 0:
        stage = "segment"
    elif approved == total and total > 0:
        stage = "delivered" if export_mtime >= content_mtime else "export"
    elif empty or pending_suggestion:
        stage = "translate"
    else:
        stage = "review"

    evidence = {
        "has_media": has_media,
        "has_transcript": transcript,
        "cues": len(cues),
        "segments": total,
        "approved": approved,
        "empty": empty,
        "suggested": pending_suggestion,
        "qa_open": qa_open,
        "content_at": _iso(content_mtime),
        "export_at": _iso(export_mtime),
        "last_activity": _last_activity(paths),
    }
    return {"stage": stage, "evidence": evidence,
            "status_counts": by_status}


def _read_segments(paths):
    from .project import read_segments
    return read_segments(paths) if paths.segments.is_file() else []


def _read_cues(paths):
    from .project import read_cues
    return read_cues(paths) if paths.cues.is_file() else []


def card(project: WorkspaceProject, sub, state) -> dict:
    derived = derive(project, sub, state)
    workflow = workflow_of(sub)
    override = workflow.get("stage_override")
    if workflow.get("archived"):
        stage = "archived"
        source = "manual"
    elif override in STAGE_KEYS:
        stage = override
        source = "manual"
    else:
        stage = derived["stage"]
        source = "auto"
    return {
        "project_id": project.id,
        "project_name": project.name,
        "subproject_id": sub.id,
        "name": sub.name,
        "kind": sub.kind,
        "langs": [sub.source_lang, *(sub.target_langs or [])],
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "derived_stage": derived["stage"],
        "derived_label": STAGE_LABELS.get(derived["stage"], ""),
        "stage_source": source,
        "evidence": derived["evidence"],
        "status_counts": derived["status_counts"],
        "workflow": {key: value for key, value in workflow.items()
                     if key not in {"revision", "updated_at"}},
        "revision": workflow.get("revision", 0),
        "updated_at": sub.updated_at,
        "root": str(project.subproject_root(sub)),
    }


def board(project: WorkspaceProject) -> dict:
    cards = []
    for sub in project.subprojects:
        try:
            state = subproject_state(project, sub)
        except Exception:
            continue
        cards.append(card(project, sub, state))
    return {
        "project_id": project.id,
        "project_name": project.name,
        "generated_at": now_iso(),
        "columns": [{"key": key, "label": label} for key, label in STAGES],
        "cards": cards,
    }


def apply_change(project: WorkspaceProject, sub, changes: dict,
                 *, reason: str = "") -> dict:
    """Merge a validated change set into ``Subproject.workflow``."""
    workflow = workflow_of(sub)
    for key in ("stage_override", "archived", "due", "priority", "labels",
                "blocked", "owner", "waiting_on"):
        if key in changes:
            workflow[key] = changes[key]
    if workflow.get("stage_override") == "":
        workflow["stage_override"] = None
    workflow["revision"] = int(workflow.get("revision") or 0) + 1
    workflow["updated_at"] = now_iso()
    sub.workflow = workflow
    sub.updated_at = now_iso()
    save_workspace(project)
    return {"workflow": workflow, "reason": reason}
