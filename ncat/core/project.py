"""Project directory format and JSONL persistence."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .types import Cue, Project, Segment, Source

SCHEMA = 1
PROJECT_FILE = "project.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass
class ProjectPaths:
    root: Path
    project_file: Path
    media: Path
    asr: Path
    text: Path
    assets: Path
    renders: Path
    segments: Path
    cues: Path
    qa: Path

    @classmethod
    def for_root(cls, root: str | Path) -> "ProjectPaths":
        root = Path(root)
        return cls(
            root=root,
            project_file=root / PROJECT_FILE,
            media=root / "media",
            asr=root / "asr",
            text=root / "text",
            assets=root / "assets",
            renders=root / "renders",
            segments=root / "segments.jsonl",
            cues=root / "cues.jsonl",
            qa=root / "qa.jsonl",
        )

    def create(self) -> None:
        for directory in (self.root, self.media, self.asr, self.text,
                          self.assets, self.renders):
            directory.mkdir(parents=True, exist_ok=True)
        for path in (self.segments, self.cues, self.qa):
            path.touch(exist_ok=True)


@dataclass
class ProjectState:
    project: Project
    source: Source | None
    asr: dict = field(default_factory=dict)
    paths: ProjectPaths | None = None

    def to_payload(self) -> dict:
        return {
            "schema": SCHEMA,
            "project": asdict(self.project),
            "source": asdict(self.source) if self.source else None,
            "asr": self.asr,
        }


def create_project(root: str | Path, name: str, *,
                   source_path: str | Path | None = None,
                   source_lang: str = "en",
                   target_langs: Iterable[str] = ("zh-Hans",),
                   force: bool = False) -> ProjectState:
    paths = ProjectPaths.for_root(root)
    if paths.project_file.exists() and not force:
        raise FileExistsError(f"project already exists: {paths.project_file}")
    paths.create()
    project_id = new_id("prj")
    source = None
    if source_path:
        source = Source(
            id=new_id("src"),
            project_id=project_id,
            path=str(Path(source_path).expanduser().resolve()),
        )
    project = Project(
        id=project_id,
        name=name,
        source_id=source.id if source else None,
        source_lang=source_lang,
        target_langs=list(target_langs),
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    state = ProjectState(project=project, source=source, paths=paths)
    save_project(state)
    return state


def load_project(root: str | Path) -> ProjectState:
    paths = ProjectPaths.for_root(root)
    if not paths.project_file.is_file():
        raise FileNotFoundError(f"no {PROJECT_FILE} in {paths.root}")
    payload = json.loads(paths.project_file.read_text(encoding="utf-8-sig"))
    project = Project(**payload["project"])
    source = Source(**payload["source"]) if payload.get("source") else None
    return ProjectState(project=project, source=source,
                        asr=payload.get("asr", {}), paths=paths)


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def save_project(state: ProjectState) -> Path:
    if state.paths is None:
        state.paths = ProjectPaths.for_root(Path(state.project.id))
    state.project.updated_at = now_iso()
    _atomic_write(
        state.paths.project_file,
        json.dumps(state.to_payload(), ensure_ascii=False, indent=2) + "\n",
    )
    return state.paths.project_file


def _write_jsonl(path: Path, rows: Iterable) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
    os.replace(temporary, path)
    return path


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def write_segments(paths: ProjectPaths, segments: Iterable[Segment]) -> Path:
    return _write_jsonl(paths.segments, segments)


def read_segments(paths: ProjectPaths) -> list[Segment]:
    return [Segment(**row) for row in _read_jsonl(paths.segments)]


def write_cues(paths: ProjectPaths, cues: Iterable[Cue]) -> Path:
    return _write_jsonl(paths.cues, cues)


def read_cues(paths: ProjectPaths) -> list[Cue]:
    return [Cue(**row) for row in _read_jsonl(paths.cues)]
