"""Workspace project model: projects contain subprojects, termbases and TMs.

A workspace project is a long-lived container. Each subproject is a legacy
NCAT project directory (schema 1) under ``subprojects/`` so the existing
ingest/transcribe/segment/align/render code keeps working unchanged. Legacy
single-video projects are exposed as a workspace project with one subproject.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .project import (
    PROJECT_FILE, ProjectState, create_project, load_project, new_id, now_iso,
)
from .segment import load_bilingual_pair
from .text import normalise_text, source_hash
from .types import (
    Project, Term, TermStatus, TermType, TMUnit,
)

SCHEMA = 2
TERMBASE_DIR = "termbases"
TM_DIR = "tm"
SUBPROJECT_DIR = "subprojects"


def _slug(value: str, fallback: str = "item") -> str:
    text = "".join(char if char.isalnum() or char in "-_." else "-"
                   for char in value).strip("-")
    return text or fallback


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@dataclass
class Subproject:
    id: str
    name: str
    kind: str = "video"
    dir: str = "."
    source_lang: str = "en"
    target_langs: list[str] = field(default_factory=lambda: ["zh-Hans"])
    termbase_ids: list[str] = field(default_factory=list)
    tm_ids: list[str] = field(default_factory=list)
    workflow: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class TermbaseRef:
    id: str
    name: str
    level: str = "project"
    subproject_id: str | None = None
    path: str = ""
    created_at: str | None = None


@dataclass
class TMRef:
    id: str
    name: str
    level: str = "project"
    subproject_id: str | None = None
    path: str = ""
    source_lang: str = "en"
    target_lang: str = "zh-Hans"
    created_at: str | None = None


@dataclass
class WorkspaceProject:
    id: str
    name: str
    root: Path
    client: str = ""
    source_lang: str = "en"
    target_langs: list[str] = field(default_factory=lambda: ["zh-Hans"])
    subprojects: list[Subproject] = field(default_factory=list)
    termbases: list[TermbaseRef] = field(default_factory=list)
    tm: list[TMRef] = field(default_factory=list)
    legacy: bool = False
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def project_file(self) -> Path:
        return self.root / PROJECT_FILE

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "schema": SCHEMA,
            "id": self.id,
            "name": self.name,
            "client": self.client,
            "source_lang": self.source_lang,
            "target_langs": self.target_langs,
            "subprojects": [asdict(item) for item in self.subprojects],
            "termbases": [asdict(item) for item in self.termbases],
            "tm": [asdict(item) for item in self.tm],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        return payload

    def subproject(self, key: str | None = None) -> Subproject:
        if key:
            for item in self.subprojects:
                if item.id == key or item.dir == key or _slug(item.name) == key:
                    return item
        if not self.subprojects:
            raise FileNotFoundError(f"project has no subprojects: {self.root}")
        return self.subprojects[0]

    def subproject_root(self, subproject: Subproject) -> Path:
        return (self.root / subproject.dir).resolve()


def create_workspace(
    root: str | Path,
    name: str,
    *,
    client: str = "",
    source_lang: str = "en",
    target_langs: Iterable[str] = ("zh-Hans",),
    source_path: str | Path | None = None,
    subproject_name: str = "Main",
    kind: str = "video",
    force: bool = False,
) -> WorkspaceProject:
    root = Path(root).expanduser().resolve()
    project_file = root / PROJECT_FILE
    if project_file.exists() and not force:
        raise FileExistsError(f"project already exists: {project_file}")
    root.mkdir(parents=True, exist_ok=True)
    project = WorkspaceProject(
        id=new_id("prj"),
        name=name,
        root=root,
        client=client,
        source_lang=source_lang,
        target_langs=list(target_langs),
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    if source_path is not None or subproject_name:
        add_subproject(
            project, subproject_name, kind=kind,
            source_path=source_path, source_lang=source_lang,
            target_langs=list(target_langs),
        )
    save_workspace(project)
    return project


def add_subproject(
    project: WorkspaceProject,
    name: str,
    *,
    kind: str = "video",
    source_path: str | Path | None = None,
    source_lang: str | None = None,
    target_langs: Iterable[str] | None = None,
) -> Subproject:
    base = _slug(name, "subproject")
    directory = project.root / SUBPROJECT_DIR / base
    counter = 2
    while directory.exists():
        directory = project.root / SUBPROJECT_DIR / f"{base}-{counter}"
        counter += 1
    directory.mkdir(parents=True, exist_ok=True)
    state = create_project(
        directory, name, source_path=source_path,
        source_lang=source_lang or project.source_lang,
        target_langs=target_langs or project.target_langs,
    )
    subproject = Subproject(
        id=new_id("sub"),
        name=name,
        kind=kind,
        dir=str(directory.relative_to(project.root)),
        source_lang=state.project.source_lang,
        target_langs=list(state.project.target_langs),
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    project.subprojects.append(subproject)
    save_workspace(project)
    return subproject


def load_workspace(root: str | Path) -> WorkspaceProject:
    root = Path(root).expanduser().resolve()
    project_file = root / PROJECT_FILE
    if not project_file.is_file():
        raise FileNotFoundError(f"no {PROJECT_FILE} in {root}")
    payload = json.loads(project_file.read_text(encoding="utf-8-sig"))
    if int(payload.get("schema", 1)) < 2:
        state = load_project(root)
        return WorkspaceProject(
            id=state.project.id,
            name=state.project.name,
            root=root,
            source_lang=state.project.source_lang,
            target_langs=list(state.project.target_langs),
            subprojects=[Subproject(
                id=f"sub_{state.project.id}", name=state.project.name, kind="video",
                dir=".", source_lang=state.project.source_lang,
                target_langs=list(state.project.target_langs),
                created_at=state.project.created_at,
                updated_at=state.project.updated_at,
            )],
            legacy=True,
            created_at=state.project.created_at,
            updated_at=state.project.updated_at,
        )
    subprojects = [Subproject(**item)
                   for item in payload.get("subprojects", [])]
    return WorkspaceProject(
        id=payload["id"],
        name=payload["name"],
        root=root,
        client=payload.get("client", ""),
        source_lang=payload.get("source_lang", "en"),
        target_langs=payload.get("target_langs", ["zh-Hans"]),
        subprojects=subprojects,
        termbases=[TermbaseRef(**item)
                   for item in payload.get("termbases", [])],
        tm=[TMRef(**item) for item in payload.get("tm", [])],
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
    )


def save_workspace(project: WorkspaceProject) -> Path:
    project.updated_at = now_iso()
    _atomic_json(project.project_file, project.to_payload())
    return project.project_file


def subproject_state(project: WorkspaceProject,
                     subproject: Subproject | str | None = None) -> ProjectState:
    selected = (subproject if isinstance(subproject, Subproject)
                else project.subproject(subproject))
    return load_project(project.subproject_root(selected))


def update_subproject(project: WorkspaceProject, subproject_id: str,
                      **changes: Any) -> Subproject:
    subproject = project.subproject(subproject_id)
    for key in ("name", "kind", "termbase_ids", "tm_ids"):
        if key in changes:
            setattr(subproject, key, changes[key])
    subproject.updated_at = now_iso()
    save_workspace(project)
    return subproject


def _termbase_path(project: WorkspaceProject, ref: TermbaseRef) -> Path:
    return project.root / ref.path


def _tm_path(project: WorkspaceProject, ref: TMRef) -> Path:
    return project.root / ref.path


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def read_termbase(project: WorkspaceProject,
                  ref: TermbaseRef) -> list[Term]:
    terms: list[Term] = []
    for row in _read_jsonl(_termbase_path(project, ref)):
        try:
            term_type = TermType(row.get("term_type", "term"))
        except ValueError:
            term_type = TermType.TERM
        try:
            status = TermStatus(row.get("status", "suggested"))
        except ValueError:
            status = TermStatus.SUGGESTED
        terms.append(Term(
            id=row.get("id") or new_id("term"),
            source=str(row.get("source", "")),
            target=str(row.get("target", "")),
            source_lang=str(row.get("source_lang") or project.source_lang),
            target_lang=str(row.get("target_lang")
                            or (project.target_langs[0]
                                if project.target_langs else "zh-Hans")),
            term_type=term_type,
            status=status,
            forbidden=bool(row.get("forbidden", False)),
            context=str(row.get("context", "")),
            origin=str(row.get("origin", "")),
            concept_id=row.get("concept_id"),
            usage_count=int(row.get("usage_count", 0)),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        ))
    return terms


def write_termbase(project: WorkspaceProject, ref: TermbaseRef,
                   terms: Iterable[Term]) -> Path:
    path = _termbase_path(project, ref)
    _write_jsonl(path, (asdict(term) for term in terms))
    return path


def add_termbase(project: WorkspaceProject, name: str, terms: Iterable[Term],
                 *, level: str = "project",
                 subproject_id: str | None = None) -> TermbaseRef:
    ref = TermbaseRef(
        id=new_id("tb"), name=name, level=level,
        subproject_id=subproject_id,
        path=f"{TERMBASE_DIR}/{_slug(name, 'termbase')}-{new_id('x')[:8]}.jsonl",
        created_at=now_iso(),
    )
    project.termbases.append(ref)
    write_termbase(project, ref, terms)
    save_workspace(project)
    return ref


def import_termbase(project: WorkspaceProject, path: str | Path, *,
                    name: str | None = None, level: str = "project",
                    subproject_id: str | None = None,
                    source_lang: str | None = None,
                    target_lang: str | None = None) -> TermbaseRef:
    path = Path(path).expanduser()
    rows: list[dict] = []
    if path.suffix.lower() == ".jsonl":
        rows = _read_jsonl(path)
    else:
        delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle, delimiter=delimiter):
                if not row or not row[0].strip():
                    continue
                rows.append({
                    "source": row[0],
                    "target": row[1] if len(row) > 1 else "",
                    "status": row[2] if len(row) > 2 else "suggested",
                })
    sl = source_lang or project.source_lang
    tl = target_lang or (project.target_langs[0]
                         if project.target_langs else "zh-Hans")
    terms: list[Term] = []
    for row in rows:
        source = normalise_text(str(row.get("source", "")))
        target = normalise_text(str(row.get("target", "")))
        if not source:
            continue
        try:
            status = TermStatus(str(row.get("status", "suggested")))
        except ValueError:
            status = TermStatus.SUGGESTED
        terms.append(Term(
            id=row.get("id") or new_id("term"),
            source=source, target=target, source_lang=sl, target_lang=tl,
            status=status, forbidden=bool(row.get("forbidden", False)),
            context=str(row.get("context", "")),
            origin=str(row.get("origin", path.name)),
            created_at=now_iso(), updated_at=now_iso(),
        ))
    return add_termbase(
        project, name or path.stem, terms, level=level,
        subproject_id=subproject_id,
    )


def read_tm(project: WorkspaceProject, ref: TMRef) -> list[TMUnit]:
    units: list[TMUnit] = []
    for row in _read_jsonl(_tm_path(project, ref)):
        units.append(TMUnit(
            id=row.get("id") or new_id("tm"),
            source=str(row.get("source", "")),
            target=str(row.get("target", "")),
            source_lang=str(row.get("source_lang") or ref.source_lang),
            target_lang=str(row.get("target_lang") or ref.target_lang),
            source_hash=str(row.get("source_hash")
                            or source_hash(str(row.get("source", "")))),
            origin_project=row.get("origin_project"),
            origin_segment=row.get("origin_segment"),
            approved_at=row.get("approved_at"),
            usage_count=int(row.get("usage_count", 0)),
            alternatives=row.get("alternatives", []),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        ))
    return units


def write_tm(project: WorkspaceProject, ref: TMRef,
             units: Iterable[TMUnit]) -> Path:
    path = _tm_path(project, ref)
    _write_jsonl(path, (asdict(unit) for unit in units))
    return path


def add_tm(project: WorkspaceProject, name: str, units: Iterable[TMUnit],
           *, level: str = "project", subproject_id: str | None = None,
           source_lang: str | None = None,
           target_lang: str | None = None) -> TMRef:
    sl = source_lang or project.source_lang
    tl = target_lang or (project.target_langs[0]
                         if project.target_langs else "zh-Hans")
    ref = TMRef(
        id=new_id("tm"), name=name, level=level,
        subproject_id=subproject_id,
        path=f"{TM_DIR}/{_slug(name, 'tm')}-{new_id('x')[:8]}.jsonl",
        source_lang=sl, target_lang=tl, created_at=now_iso(),
    )
    project.tm.append(ref)
    write_tm(project, ref, units)
    save_workspace(project)
    return ref


def import_tm_pair(project: WorkspaceProject,
                   source_path: str | Path, target_path: str | Path, *,
                   name: str | None = None, level: str = "project",
                   subproject_id: str | None = None,
                   source_lang: str | None = None,
                   target_lang: str | None = None) -> TMRef:
    pairs = load_bilingual_pair(source_path, target_path)
    sl = source_lang or project.source_lang
    tl = target_lang or (project.target_langs[0]
                         if project.target_langs else "zh-Hans")
    units = [TMUnit(
        id=new_id("tm"), source=source, target=target,
        source_lang=sl, target_lang=tl, source_hash=source_hash(source),
        origin_project=project.id, created_at=now_iso(), updated_at=now_iso(),
    ) for source, target in pairs if source.strip()]
    return add_tm(
        project, name or f"{Path(source_path).stem} / {Path(target_path).stem}",
        units, level=level, subproject_id=subproject_id,
        source_lang=sl, target_lang=tl,
    )


def effective_terms(project: WorkspaceProject,
                    subproject: Subproject | str | None = None) -> list[Term]:
    selected = (subproject if isinstance(subproject, Subproject)
                else project.subproject(subproject))
    refs = [ref for ref in project.termbases
            if ref.level == "project"
            or ref.subproject_id == selected.id]
    if selected.termbase_ids:
        refs = [ref for ref in refs if ref.id in selected.termbase_ids]
    merged: dict[str, Term] = {}
    for ref in refs:
        for term in read_termbase(project, ref):
            merged[term.source.casefold()] = term
    return list(merged.values())


def effective_tm(project: WorkspaceProject,
                 subproject: Subproject | str | None = None) -> list[TMUnit]:
    selected = (subproject if isinstance(subproject, Subproject)
                else project.subproject(subproject))
    refs = [ref for ref in project.tm
            if ref.level == "project" or ref.subproject_id == selected.id]
    if selected.tm_ids:
        refs = [ref for ref in refs if ref.id in selected.tm_ids]
    units: dict[str, TMUnit] = {}
    for ref in refs:
        for unit in read_tm(project, ref):
            units[unit.source_hash] = unit
    return list(units.values())


def workspace_payload(project: WorkspaceProject,
                      state: ProjectState | None = None) -> dict[str, Any]:
    payload = project.to_payload()
    payload["root"] = str(project.root)
    payload["legacy"] = project.legacy
    if state is not None:
        payload["legacy_state"] = state.to_payload()
    return payload


def find_workspace_for(path: str | Path) -> WorkspaceProject | None:
    """Find a schema-2 workspace whose subproject root is ``path``."""
    target = Path(path).expanduser().resolve()
    for parent in [target, *target.parents]:
        project_file = parent / PROJECT_FILE
        if not project_file.is_file():
            continue
        try:
            payload = json.loads(project_file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if int(payload.get("schema", 1)) < 2:
            continue
        project = load_workspace(parent)
        for subproject in project.subprojects:
            if project.subproject_root(subproject) == target:
                return project
    return None
