"""Append-only project activity log."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .project import new_id, now_iso
from .workspace import WorkspaceProject

ACTIVITY_FILE = "activity.jsonl"
MAX_EVENTS = 1000


def _path(project: WorkspaceProject) -> Path:
    return project.root / ACTIVITY_FILE


def log_activity(project: WorkspaceProject, event_type: str,
                 **fields: Any) -> dict[str, Any]:
    event = {
        "id": new_id("act"),
        "at": now_iso(),
        "type": event_type,
        **fields,
    }
    path = _path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    _trim(path)
    return event


def _trim(path: Path) -> None:
    try:
        if path.stat().st_size < 256_000:
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= MAX_EVENTS:
            return
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            "\n".join(lines[-MAX_EVENTS:]) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def read_activity(project: WorkspaceProject,
                  limit: int = 50) -> list[dict[str, Any]]:
    path = _path(project)
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return list(reversed(events[-max(1, limit):]))
