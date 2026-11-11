"""Small file-backed undo/redo snapshots for the Web editor."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .index import build_index
from .project import ProjectPaths, read_cues, read_segments, write_cues, write_segments

LIMIT = 20


def _paths(paths: ProjectPaths) -> tuple[Path, Path]:
    directory = paths.root / ".history"
    return directory / "undo.json", directory / "redo.json"


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write(path: Path, stack: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(stack, ensure_ascii=False),
                         encoding="utf-8")
    os.replace(temporary, path)


def _snapshot(paths: ProjectPaths) -> dict[str, Any]:
    return {
        "segments": [asdict(item) for item in read_segments(paths)],
        "cues": [asdict(item) for item in read_cues(paths)],
    }


def _restore(paths: ProjectPaths, snapshot: dict[str, Any]) -> None:
    from .types import Cue, Segment
    write_segments(paths, [Segment(**item)
                           for item in snapshot.get("segments", [])])
    write_cues(paths, [Cue(**item) for item in snapshot.get("cues", [])])
    build_index(paths)


def push(paths: ProjectPaths) -> None:
    undo_path, redo_path = _paths(paths)
    undo = _read(undo_path)
    undo.append(_snapshot(paths))
    _write(undo_path, undo[-LIMIT:])
    _write(redo_path, [])


def available(paths: ProjectPaths) -> dict[str, int]:
    undo_path, redo_path = _paths(paths)
    return {"undo": len(_read(undo_path)), "redo": len(_read(redo_path))}


def undo(paths: ProjectPaths) -> bool:
    undo_path, redo_path = _paths(paths)
    undo_stack = _read(undo_path)
    if not undo_stack:
        return False
    redo_stack = _read(redo_path)
    redo_stack.append(_snapshot(paths))
    snapshot = undo_stack.pop()
    _restore(paths, snapshot)
    _write(undo_path, undo_stack)
    _write(redo_path, redo_stack[-LIMIT:])
    return True


def redo(paths: ProjectPaths) -> bool:
    undo_path, redo_path = _paths(paths)
    redo_stack = _read(redo_path)
    if not redo_stack:
        return False
    undo_stack = _read(undo_path)
    undo_stack.append(_snapshot(paths))
    snapshot = redo_stack.pop()
    _restore(paths, snapshot)
    _write(undo_path, undo_stack[-LIMIT:])
    _write(redo_path, redo_stack)
    return True
