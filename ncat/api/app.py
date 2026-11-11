"""Local Starlette API for the NCAT Web editor.

Workspace projects contain subprojects, termbases and translation memories.
Long-running operations use the same CLI path as the terminal; standalone
transcription runs in a queue backed by the ASR venv worker.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ncat.core.assets.tm import find_matches, read_units
from ncat.core.assets.termbase import match_terms, read_terms
from ncat.core.history import (
    available as history_available, push as history_push,
    redo as history_redo, undo as history_undo,
)
from ncat.core.index import build_index, search_index
from ncat.core.media import probe_media
from ncat.core.qa import (
    read_qa, run_qa, update_qa, write_qa,
)
from ncat.core.project import (
    load_project, new_id, now_iso, read_cues, read_segments, save_project,
    write_cues, write_segments,
)
from ncat.core.runs import list_runs
from ncat.core.settings import (
    CLOUD_ASR_URL, CLOUD_MT_URL, LOCAL_ASR_URL, LOCAL_MT_URL,
    apply_action_defaults, load_settings, public_settings, save_settings,
)
from ncat.core.segment import segments_from_pairs
from ncat.core.subs.ass import parse_ass
from ncat.core.subs.model import (
    cues_from_ass, cues_from_segments, merge_cues, split_cue,
)
from ncat.core.subs.rules import apply_all, cue_metrics
from ncat.core.text import source_hash
from ncat.core.types import (
    Cue, ProjectStage, QAStatus, Segment, SegmentStatus,
)
from ncat.core.workspace import (
    TermbaseRef, TMRef, WorkspaceProject, add_subproject, add_termbase,
    add_tm, create_workspace, effective_terms, effective_tm, import_termbase,
    import_tm_pair, load_workspace, read_tm, read_termbase, save_workspace,
    subproject_state, update_subproject,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "static"
DEFAULT_PLUGINS = ",".join([
    "parakeet=ncat.test.asr_backend.parakeet:make_backend",
    "canary=ncat.test.asr_backend.canary:make_backend",
    "nemotron=ncat.test.asr_backend.nemotron:make_backend",
    "qwen3_asr=ncat.test.asr_backend.qwen3_asr:make_backend",
    "openai=ncat.core.asr.openai:make_backend",
])


def workspace_root(value: str | Path | None = None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    configured = os.environ.get("NCAT_WORKSPACE")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / "NCAT").resolve()


def _action_python(backend: str | None = None) -> str:
    configured = os.environ.get("NCAT_ASR_PYTHON")
    if configured:
        return configured
    if backend == "nemotron":
        candidate = ROOT / ".venv-nemotron" / "bin" / "python"
        if candidate.is_file():
            return str(candidate)
    candidate = ROOT / ".venv-asr" / "bin" / "python"
    return str(candidate) if candidate.is_file() else sys.executable


def _run_cli(arguments: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    backend = None
    if arguments and arguments[0] == "transcribe":
        try:
            backend = arguments[arguments.index("--backend") + 1]
        except (ValueError, IndexError):
            backend = None
    env["NCAT_ASR_PLUGINS"] = (env.get("NCAT_ASR_PLUGINS")
                               or DEFAULT_PLUGINS)
    command = [_action_python(backend), "-m", "ncat", *arguments]
    if "--json" not in command:
        command.append("--json")
    process = subprocess.run(
        command, cwd=str(ROOT), capture_output=True, text=True, env=env,
    )
    if process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip()
        try:
            payload = json.loads(message.splitlines()[-1])
            error = payload.get("error", {})
            raise HTTPException(400, detail={
                "code": error.get("code", "action_failed"),
                "message": error.get("message", message)})
        except (json.JSONDecodeError, IndexError):
            raise HTTPException(400, detail={
                "code": "action_failed", "message": message})
    output = process.stdout.strip()
    if not output:
        return {}
    try:
        return json.loads(output.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"output": output}


def _slug(value: str) -> str:
    text = "".join(char if char.isalnum() or char in "-_." else "-"
                   for char in value).strip("-")
    return text or "project"


def _has_project(path: Path) -> bool:
    try:
        return (path / "project.json").is_file()
    except OSError:
        return False


def _children(root: Path):
    try:
        return sorted(root.iterdir())
    except OSError:
        return []


def _find_workspace(root: Path, key: str) -> WorkspaceProject:
    if not root.is_dir():
        raise HTTPException(404, detail={
            "code": "not_found", "message": f"workspace not found: {root}"})
    direct = root / key
    if _has_project(direct):
        project = load_workspace(direct)
        if project.id == key or direct.name == key:
            return project
    for child in _children(root):
        if not _has_project(child):
            continue
        try:
            project = load_workspace(child)
        except Exception:
            continue
        if project.id == key or child.name == key:
            return project
    raise HTTPException(404, detail={
        "code": "not_found", "message": f"project not found: {key}"})


def _resolve_sub(project: WorkspaceProject, sub_key: str | None = None):
    sub = project.subproject(sub_key)
    state = subproject_state(project, sub)
    return sub, state


def _file_url(project: WorkspaceProject, sub, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        relative = path.resolve().relative_to(
            project.subproject_root(sub).resolve())
    except ValueError:
        return None
    return (f"/api/v1/projects/{project.id}/subprojects/{sub.id}/files/"
            f"{relative.as_posix()}")


def _sub_payload(project: WorkspaceProject, sub, state) -> dict[str, Any]:
    files: dict[str, str] = {}
    for key, value in (
        ("proxy", state.source.proxy_path if state.source else None),
        ("audio", state.source.audio_path if state.source else None),
    ):
        url = _file_url(project, sub, Path(value) if value else None)
        if url:
            files[key] = url
    for key, path in (
        ("segments", state.paths.segments),
        ("cues", state.paths.cues),
        ("transcript", state.paths.asr / "transcript.json"),
        ("ass", state.paths.renders / f"{_slug(sub.name)}.ass"),
    ):
        if path.is_file():
            url = _file_url(project, sub, path)
            if url:
                files[key] = url
    return {
        **asdict(sub),
        "state": {
            "source": asdict(state.source) if state.source else None,
            "asr": state.asr,
            "ingest": state.ingest,
        },
        "counts": {
            "segments": len(read_segments(state.paths)),
            "cues": len(read_cues(state.paths)),
        },
        "files": files,
        "runs": [
            {"id": run.id, "status": str(run.status), "recipe": run.recipe,
             "started_at": run.started_at, "finished_at": run.finished_at,
             "error": run.error}
            for run in list_runs(state.paths)[-8:]
        ],
    }


def _project_payload(project: WorkspaceProject) -> dict[str, Any]:
    subs = []
    for sub in project.subprojects:
        try:
            state = subproject_state(project, sub)
        except Exception:
            continue
        subs.append(_sub_payload(project, sub, state))
    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "client": project.client,
            "source_lang": project.source_lang,
            "target_langs": project.target_langs,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "legacy": project.legacy,
            "root": str(project.root),
        },
        "subprojects": subs,
        "termbases": [_termbase_payload(project, ref)
                      for ref in project.termbases],
        "tm": [_tm_payload(project, ref) for ref in project.tm],
        "counts": {
            "subprojects": len(subs),
            "segments": sum(item["counts"]["segments"] for item in subs),
            "cues": sum(item["counts"]["cues"] for item in subs),
        },
    }


def _termbase_payload(project: WorkspaceProject, ref: TermbaseRef) -> dict:
    return {**asdict(ref), "terms": len(read_termbase(project, ref))}


def _tm_payload(project: WorkspaceProject, ref: TMRef) -> dict:
    return {**asdict(ref), "units": len(read_tm(project, ref))}


async def _json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, detail={
            "code": "invalid_json", "message": str(exc)}) from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, detail={
            "code": "invalid_body", "message": "JSON object required"})
    return payload


# ---------------------------------------------------------------- workspace
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "version": "0.1.0"})


async def workspace(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    projects = []
    if root.is_dir():
        for child in _children(root):
            if not _has_project(child):
                continue
            try:
                project = load_workspace(child)
                payload = _project_payload(project)
            except Exception:
                continue
            projects.append({
                "id": payload["project"]["id"],
                "name": payload["project"]["name"],
                "dir": child.name,
                "client": payload["project"]["client"],
                "source_lang": payload["project"]["source_lang"],
                "target_langs": payload["project"]["target_langs"],
                "legacy": payload["project"]["legacy"],
                "counts": payload["counts"],
            })
    return JSONResponse({"workspace": str(root), "projects": projects})


async def projects(request: Request) -> JSONResponse:
    if request.method == "GET":
        return await workspace(request)
    root = workspace_root(request.app.state.workspace)
    body = await _json(request)
    name = str(body.get("name") or "untitled")
    directory = Path(str(body.get("path") or "")).expanduser()
    if not directory.is_absolute():
        directory = root / (_slug(name) if not body.get("path") else str(body["path"]))
    directory = directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise HTTPException(409, detail={
            "code": "already_exists", "message": str(directory)})
    source = body.get("source")
    source_path = Path(source).expanduser().resolve() if source else None
    if source_path is not None and not source_path.is_file():
        raise HTTPException(400, detail={
            "code": "not_found", "message": f"source not found: {source_path}"})
    project = create_workspace(
        directory, name,
        client=str(body.get("client") or ""),
        source_lang=str(body.get("source_lang") or "en"),
        target_langs=body.get("target_langs") or ["zh-Hans"],
        source_path=source_path,
        subproject_name=str(body.get("subproject_name") or "Main"),
        kind=str(body.get("kind") or "video"),
    )
    return JSONResponse(_project_payload(project), status_code=201)


async def project_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    return JSONResponse(_project_payload(project))


# -------------------------------------------------------------- subprojects
async def subprojects(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    if request.method == "GET":
        return JSONResponse([
            _sub_payload(project, sub, subproject_state(project, sub))
            for sub in project.subprojects
        ])
    body = await _json(request)
    source = body.get("source_path") or body.get("source")
    source_path = Path(source).expanduser().resolve() if source else None
    if source_path is not None and not source_path.is_file():
        raise HTTPException(400, detail={
            "code": "not_found", "message": f"source not found: {source_path}"})
    sub = add_subproject(
        project, str(body.get("name") or "Subproject"),
        kind=str(body.get("kind") or "video"),
        source_path=source_path,
        source_lang=body.get("source_lang"),
        target_langs=body.get("target_langs"),
    )
    state = subproject_state(project, sub)
    return JSONResponse(_sub_payload(project, sub, state), status_code=201)


async def subproject_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    if request.method == "PATCH":
        body = await _json(request)
        sub = update_subproject(
            project, sub.id,
            **{key: body[key] for key in
               ("name", "kind", "termbase_ids", "tm_ids") if key in body})
    state = subproject_state(project, sub)
    return JSONResponse(_sub_payload(project, sub, state))


# ------------------------------------------------------------------- actions
def _action_args(action: str, directory: Path, body: dict) -> list[str]:
    if action == "ingest":
        args = ["ingest", str(directory)]
        args += ["--height", str(body.get("height", 720))]
        if body.get("encoder"):
            args += ["--encoder", str(body["encoder"])]
        if body.get("force"):
            args.append("--force")
        return args
    if action == "transcribe":
        args = ["transcribe", str(directory)]
        if body.get("backend"):
            args += ["--backend", str(body["backend"])]
        if body.get("model"):
            args += ["--model", str(body["model"])]
        if body.get("language"):
            args += ["--language", str(body["language"])]
        if body.get("offset_ms"):
            args += ["--offset-ms", str(int(body["offset_ms"]))]
        if body.get("chunk_ms") is not None:
            args += ["--chunk-ms", str(int(body["chunk_ms"]))]
        for key, value in (body.get("options") or {}).items():
            args += ["--option", f"{key}={json.dumps(value)}"]
        args.append("--no-pause-after-transcript")
        return args
    if action == "segment":
        args = ["segment", str(directory)]
        if body.get("propose", True):
            args.append("--propose")
        elif body.get("import_path"):
            args += ["--import", str(body["import_path"])]
            if body.get("target"):
                args += ["--target", str(body["target"])]
        if body.get("force"):
            args.append("--force")
        return args
    if action == "align":
        args = ["align", str(directory)]
        if body.get("offset_ms"):
            args += ["--offset-ms", str(int(body["offset_ms"]))]
        return args
    if action == "translate":
        args = ["translate", str(directory)]
        if body.get("backend"):
            args += ["--backend", str(body["backend"])]
        if body.get("base_url"):
            args += ["--base-url", str(body["base_url"])]
        if body.get("model"):
            args += ["--model", str(body["model"])]
        if body.get("target_lang"):
            args += ["--target-lang", str(body["target_lang"])]
        if body.get("brief"):
            args += ["--brief", str(body["brief"])]
        if body.get("style"):
            args += ["--style", str(body["style"])]
        if body.get("limit"):
            args += ["--limit", str(int(body["limit"]))]
        for key, value in (body.get("options") or {}).items():
            args += ["--option", f"{key}={json.dumps(value)}"]
        return args
    if action == "style":
        args = ["style", str(directory)]
        if body.get("template"):
            args += ["--template", str(body["template"])]
        if body.get("bilingual") is False:
            args.append("--no-bilingual")
        return args
    if action == "render":
        args = ["render", str(directory)]
        if body.get("ass"):
            args += ["--ass", str(body["ass"])]
        if body.get("preset"):
            args += ["--preset", str(body["preset"])]
        if body.get("encoder"):
            args += ["--encoder", str(body["encoder"])]
        if body.get("crf"):
            args += ["--crf", str(int(body["crf"]))]
        return args
    raise HTTPException(404, detail={
        "code": "not_found", "message": f"unknown action: {action}"})


async def _run_action(project, sub, body: dict, action: str) -> JSONResponse:
    state = subproject_state(project, sub)
    args = _action_args(action, state.paths.root, body)
    history_push(state.paths)
    result = await asyncio.to_thread(_run_cli, args)
    build_index(state.paths)
    return JSONResponse(result)


async def project_actions(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, state = _resolve_sub(project)
    body = await _json(request) if request.method == "POST" else {}
    action = request.path_params["action"]
    if action in {"transcribe", "translate"}:
        body = apply_action_defaults(root, action, body)
    return await _run_action(project, sub, body, action)


async def subproject_actions(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    body = await _json(request) if request.method == "POST" else {}
    action = request.path_params["action"]
    if action in {"transcribe", "translate"}:
        body = apply_action_defaults(root, action, body)
    return await _run_action(project, sub, body, action)


# ------------------------------------------------------ subproject content
async def _segments(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    if request.method == "GET":
        return JSONResponse([
            asdict(item) for item in sorted(
                read_segments(state.paths), key=lambda item: item.order)
        ])
    body = await _json(request)
    segments = read_segments(state.paths)
    source = str(body.get("source") or "")
    target = str(body.get("target") or "")
    segment = Segment(
        id=body.get("id") or new_id("seg"), project_id=state.project.id,
        order=int(body.get("order", len(segments))),
        source=source, target=target, source_hash=source_hash(source),
        status=SegmentStatus(body.get("status") or "empty"),
    )
    history_push(state.paths)
    segments.append(segment)
    write_segments(state.paths, segments)
    build_index(state.paths)
    return JSONResponse(asdict(segment), status_code=201)


async def _cues(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    if request.method == "GET":
        cues = read_cues(state.paths)
        apply_all(cues)
        write_cues(state.paths, cues)
        return JSONResponse([
            {**asdict(item), "metrics": cue_metrics(item)}
            for item in sorted(cues, key=lambda cue: (
                cue.start_ms if cue.start_ms is not None else 10**15,
                cue.order, cue.id))
        ])
    body = await _json(request)
    cues = read_cues(state.paths)
    start_ms = body.get("start_ms")
    end_ms = body.get("end_ms")
    cue = Cue(
        id=body.get("id") or new_id("cue"), project_id=state.project.id,
        segment_id=body.get("segment_id"),
        order=int(body.get("order", len(cues))),
        start_ms=None if start_ms in (None, "") else int(start_ms),
        end_ms=None if end_ms in (None, "") else int(end_ms),
        source_text=str(body.get("source_text") or ""),
        target_text=str(body.get("target_text") or ""),
        style=str(body.get("style") or "Default"),
        locked=bool(body.get("locked", False)),
    )
    history_push(state.paths)
    apply_all([cue])
    cues.append(cue)
    write_cues(state.paths, cues)
    build_index(state.paths)
    return JSONResponse({**asdict(cue), "metrics": cue_metrics(cue)},
                        status_code=201)


async def project_segments(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _segments(project, sub, request)


async def subproject_segments(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _segments(project, sub, request)


async def project_cues(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _cues(project, sub, request)


async def subproject_cues(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _cues(project, sub, request)


async def _segment_detail(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    segments = read_segments(state.paths)
    segment_id = request.path_params["segment_id"]
    found = next((item for item in segments if item.id == segment_id), None)
    if found is None:
        raise HTTPException(404, {"code": "not_found",
                                  "message": "segment not found"})
    if request.method == "DELETE":
        history_push(state.paths)
        write_segments(state.paths, [item for item in segments
                                     if item.id != segment_id])
        build_index(state.paths)
        return JSONResponse({"deleted": segment_id})
    body = await _json(request)
    for key in ("source", "target", "notes"):
        if key in body:
            setattr(found, key, str(body[key]))
    if "source" in body:
        found.source_hash = source_hash(found.source)
    if "status" in body:
        found.status = SegmentStatus(str(body["status"]))
    if "locked" in body:
        found.locked = bool(body["locked"])
    history_push(state.paths)
    found.revision += 1
    found.updated_at = now_iso()
    write_segments(state.paths, segments)
    build_index(state.paths)
    return JSONResponse(asdict(found))


async def segment_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _segment_detail(project, sub, request)


async def subproject_segment_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _segment_detail(project, sub, request)


def _override_tags(text: str) -> set[str]:
    return set(re.findall(r"\{[^}]*\}", text or ""))


async def _cue_detail(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    cues = read_cues(state.paths)
    cue_id = request.path_params["cue_id"]
    found = next((item for item in cues if item.id == cue_id), None)
    if found is None:
        raise HTTPException(404, {"code": "not_found",
                                  "message": "cue not found"})
    if request.method == "DELETE":
        history_push(state.paths)
        write_cues(state.paths, [item for item in cues if item.id != cue_id])
        build_index(state.paths)
        return JSONResponse({"deleted": cue_id})
    body = await _json(request)
    if body.get("protect_tags", True):
        old_tags = _override_tags(found.target_text) | _override_tags(
            found.source_text)
        new_tags = _override_tags(str(body.get("target_text", found.target_text))) | _override_tags(
            str(body.get("source_text", found.source_text)))
        missing = old_tags - new_tags
        if missing:
            raise HTTPException(400, detail={
                "code": "tag_protection",
                "message": "override tags removed: " + " ".join(sorted(missing)),
            })
    for key in ("source_text", "target_text", "style"):
        if key in body:
            setattr(found, key, str(body[key]))
    for key in ("start_ms", "end_ms", "order"):
        if key in body:
            value = body[key]
            setattr(found, key, None if value is None else int(value))
    if "locked" in body:
        found.locked = bool(body["locked"])
    if (found.start_ms is not None and found.end_ms is not None
            and found.end_ms < found.start_ms):
        raise HTTPException(400, detail={
            "code": "invalid_timing",
            "message": "end_ms must be >= start_ms"})
    history_push(state.paths)
    found.revision += 1
    apply_all([found])
    write_cues(state.paths, cues)
    build_index(state.paths)
    if found.segment_id and "target_text" in body:
        segments = read_segments(state.paths)
        segment = next((item for item in segments
                        if item.id == found.segment_id), None)
        if segment is not None:
            segment.target = found.target_text
            segment.status = (SegmentStatus.TRANSLATED
                              if found.target_text.strip()
                              else SegmentStatus.EMPTY)
            segment.revision += 1
            segment.updated_at = now_iso()
            write_segments(state.paths, segments)
    return JSONResponse({**asdict(found), "metrics": cue_metrics(found)})


async def cue_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _cue_detail(project, sub, request)


async def subproject_cue_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _cue_detail(project, sub, request)


async def _cue_split(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    cues = read_cues(state.paths)
    cue_id = request.path_params["cue_id"]
    found = next((item for item in cues if item.id == cue_id), None)
    if found is None:
        raise HTTPException(404, {"code": "not_found",
                                  "message": "cue not found"})
    body = await _json(request)
    left, right = split_cue(
        found, at_ms=body.get("at_ms"), ratio=float(body.get("ratio", 0.5)))
    cues = [left if item.id == cue_id else item for item in cues]
    cues.append(right)
    for index, cue in enumerate(sorted(cues, key=lambda item: (
            item.start_ms if item.start_ms is not None else 10**15,
            item.order))):
        cue.order = index
    history_push(state.paths)
    apply_all(cues)
    write_cues(state.paths, cues)
    build_index(state.paths)
    return JSONResponse({"cues": [asdict(left), asdict(right)]})


async def cue_split(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _cue_split(project, sub, request)


async def subproject_cue_split(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _cue_split(project, sub, request)


async def _cue_merge(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    cues = read_cues(state.paths)
    cue_id = request.path_params["cue_id"]
    body = await _json(request)
    other_id = body.get("other_id")
    first = next((item for item in cues if item.id == cue_id), None)
    second = next((item for item in cues if item.id == other_id), None)
    if first is None or second is None:
        raise HTTPException(404, detail={
            "code": "not_found", "message": "both cues are required"})
    merged = merge_cues(first, second)
    cues = [item for item in cues if item.id not in {first.id, second.id}]
    cues.append(merged)
    for index, cue in enumerate(sorted(cues, key=lambda item: (
            item.start_ms if item.start_ms is not None else 10**15,
            item.order))):
        cue.order = index
    history_push(state.paths)
    apply_all(cues)
    write_cues(state.paths, cues)
    build_index(state.paths)
    return JSONResponse(asdict(merged))


async def cue_merge(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _cue_merge(project, sub, request)


async def subproject_cue_merge(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _cue_merge(project, sub, request)


async def _import_ass(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    body = await _json(request)
    path = Path(str(body.get("path") or "")).expanduser()
    if not path.is_file():
        raise HTTPException(400, detail={
            "code": "not_found", "message": f"ASS not found: {path}"})
    history_push(state.paths)
    document = parse_ass(path)
    cues = cues_from_ass(document, state.project.id,
                         bilingual_only=bool(body.get("bilingual_only", False)))
    apply_all(cues)
    write_cues(state.paths, cues)
    segments = segments_from_pairs(
        [(cue.source_text, cue.target_text) for cue in cues],
        state.project.id,
    )
    write_segments(state.paths, segments)
    build_index(state.paths)
    state.project.stage = ProjectStage.DRAFTING
    save_project(state)
    return JSONResponse({"cues": len(cues), "segments": len(segments)})


async def import_ass(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _import_ass(project, sub, request)


async def subproject_import_ass(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _import_ass(project, sub, request)


async def _transcript(project, sub) -> JSONResponse:
    state = subproject_state(project, sub)
    path = state.paths.asr / "transcript.json"
    if not path.is_file():
        return JSONResponse({"transcript": None})
    return JSONResponse(json.loads(path.read_text(encoding="utf-8-sig")))


async def transcript(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _transcript(project, sub)


async def subproject_transcript(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _transcript(project, sub)


async def _files(project, sub, request: Request) -> FileResponse:
    state = subproject_state(project, sub)
    target = (state.paths.root / request.path_params["path"]).resolve()
    if state.paths.root.resolve() not in target.parents:
        raise HTTPException(403, detail={
            "code": "forbidden", "message": "path escapes project"})
    if not target.is_file():
        raise HTTPException(404, detail={
            "code": "not_found", "message": str(target)})
    return FileResponse(target)


async def project_files(request: Request) -> FileResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _files(project, sub, request)


async def subproject_files(request: Request) -> FileResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _files(project, sub, request)


async def _media_info(project, sub) -> JSONResponse:
    state = subproject_state(project, sub)
    source = state.source.path if state.source else None
    if not source or not Path(source).is_file():
        return JSONResponse({"info": None})
    info = await asyncio.to_thread(probe_media, source)
    return JSONResponse({"info": asdict(info)})


async def media_info(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _media_info(project, sub)


async def subproject_media_info(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _media_info(project, sub)


async def _runs(project, sub) -> JSONResponse:
    state = subproject_state(project, sub)
    return JSONResponse([asdict(run) for run in list_runs(state.paths)])


async def project_runs(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _runs(project, sub)


async def subproject_runs(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _runs(project, sub)


# ---------------------------------------------------------------- termbases
async def termbases(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    if request.method == "GET":
        return JSONResponse([_termbase_payload(project, ref)
                             for ref in project.termbases])
    body = await _json(request)
    ref = await asyncio.to_thread(
        import_termbase, project, str(body.get("path") or ""),
        name=body.get("name"), level=str(body.get("level") or "project"),
        subproject_id=body.get("subproject_id"),
        source_lang=body.get("source_lang"),
        target_lang=body.get("target_lang"),
    )
    return JSONResponse(_termbase_payload(project, ref), status_code=201)


async def termbase_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    ref = next((item for item in project.termbases
                if item.id == request.path_params["termbase_id"]), None)
    if ref is None:
        raise HTTPException(404, detail={
            "code": "not_found", "message": "termbase not found"})
    if request.method == "DELETE":
        project.termbases = [item for item in project.termbases
                             if item.id != ref.id]
        save_workspace(project)
        return JSONResponse({"deleted": ref.id})
    return JSONResponse({
        **_termbase_payload(project, ref),
        "entries": [asdict(term) for term in read_termbase(project, ref)],
    })


async def effective_terms_view(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    terms = await asyncio.to_thread(effective_terms, project, sub)
    return JSONResponse({
        "subproject": asdict(sub),
        "termbases": [asdict(ref) for ref in project.termbases],
        "terms": [asdict(term) for term in terms],
    })


# ------------------------------------------------------------- translation memory
async def tms(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    if request.method == "GET":
        return JSONResponse([_tm_payload(project, ref) for ref in project.tm])
    body = await _json(request)
    ref = await asyncio.to_thread(
        import_tm_pair, project,
        str(body.get("source_path") or ""),
        str(body.get("target_path") or ""),
        name=body.get("name"), level=str(body.get("level") or "project"),
        subproject_id=body.get("subproject_id"),
        source_lang=body.get("source_lang"),
        target_lang=body.get("target_lang"),
    )
    return JSONResponse(_tm_payload(project, ref), status_code=201)


async def tm_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    ref = next((item for item in project.tm
                if item.id == request.path_params["tm_id"]), None)
    if ref is None:
        raise HTTPException(404, detail={
            "code": "not_found", "message": "TM not found"})
    if request.method == "DELETE":
        project.tm = [item for item in project.tm if item.id != ref.id]
        save_workspace(project)
        return JSONResponse({"deleted": ref.id})
    return JSONResponse({
        **_tm_payload(project, ref),
        "units": [asdict(unit) for unit in read_tm(project, ref)],
    })


async def effective_tm_view(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    units = await asyncio.to_thread(effective_tm, project, sub)
    return JSONResponse({
        "subproject": asdict(sub),
        "tm": [asdict(ref) for ref in project.tm],
        "units": [asdict(unit) for unit in units],
    })


# ------------------------------------------------------------ transcribe jobs
@dataclass
class TranscribeJob:
    id: str
    audio_path: str
    backend: str
    model: str = ""
    aligner: str = ""
    language: str = ""
    options: dict = field(default_factory=dict)
    status: str = "queued"
    stage: str = "queued"
    progress: float = 0.0
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result: dict | None = None
    output_dir: str = ""
    process: Any = None


_JOBS: dict[str, TranscribeJob] = {}
_JOB_ORDER: list[str] = []
_JOB_LOCK = threading.Lock()
_EXECUTOR: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(max_workers=1,
                                       thread_name_prefix="ncat-asr")
    return _EXECUTOR


def _job_payload(job: TranscribeJob) -> dict:
    return {
        item.name: getattr(job, item.name)
        for item in fields(job)
        if item.name != "process"
    }


def _run_job(job: TranscribeJob, out: Path) -> None:
    job.status = "running"
    job.stage = "loading_backend"
    job.started_at = now_iso()
    command = [
        _action_python(job.backend), "-m", "ncat.test.transcribe_worker",
        "--audio", job.audio_path, "--backend", job.backend,
        "--out", str(out),
    ]
    if job.model:
        command += ["--model", job.model]
    if job.aligner:
        command += ["--aligner", job.aligner]
    if job.language:
        command += ["--language", job.language]
    for key, value in job.options.items():
        command += ["--option", f"{key}={json.dumps(value)}"]
    env = os.environ.copy()
    env["NCAT_ASR_PLUGINS"] = (env.get("NCAT_ASR_PLUGINS")
                               or DEFAULT_PLUGINS)
    log_path = out / "worker.log"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command, cwd=str(ROOT), stdout=subprocess.PIPE,
                stderr=log, text=True, bufsize=1, env=env,
            )
            job.process = process
            assert process.stdout is not None
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stage = event.get("stage")
                if stage:
                    job.stage = stage
                    job.progress = {
                        "queued": 0.0, "preparing_audio": 0.03, "loading_backend": 0.05,
                        "loading_model": 0.15, "transcribing": 0.45,
                        "finalizing": 0.9, "done": 1.0,
                        "failed": 1.0,
                    }.get(stage, job.progress)
                if stage == "done":
                    job.result = event
                    job.status = "done"
                if stage == "failed":
                    job.error = event.get("error") or "worker failed"
                    job.status = "failed"
            code = process.wait()
            job.process = None
            if code != 0 and job.status != "failed":
                job.status = "failed"
                job.error = f"worker exited with {code}"
            if job.status != "failed":
                job.status = "done"
                job.stage = "done"
                job.progress = 1.0
    except Exception as error:
        job.status = "failed"
        job.stage = "failed"
        job.error = str(error)
    finally:
        job.finished_at = now_iso()


async def transcribe_jobs(request: Request) -> JSONResponse:
    if request.method == "GET":
        with _JOB_LOCK:
            jobs = [_job_payload(_JOBS[job_id]) for job_id in _JOB_ORDER]
        return JSONResponse(jobs)
    body = await _json(request)
    root = workspace_root(request.app.state.workspace)
    body = apply_action_defaults(root, "transcribe", body)
    audio = Path(str(body.get("audio") or "")).expanduser()
    if not audio.is_file():
        raise HTTPException(400, detail={
            "code": "not_found", "message": f"audio not found: {audio}"})
    backend = str(body.get("backend") or "")
    if not backend:
        raise HTTPException(400, detail={
            "code": "invalid_argument", "message": "backend is required"})
    job = TranscribeJob(
        id=new_id("job"),
        audio_path=str(audio.resolve()),
        backend=backend,
        model=str(body.get("model") or ""),
        aligner=str(body.get("aligner") or ""),
        language=str(body.get("language") or ""),
        options=body.get("options") or {},
    )
    root = workspace_root(request.app.state.workspace)
    out = root / "transcribe" / job.id
    out.mkdir(parents=True, exist_ok=True)
    job.output_dir = str(out)
    with _JOB_LOCK:
        _JOBS[job.id] = job
        _JOB_ORDER.append(job.id)
    _executor().submit(_run_job, job, out)
    return JSONResponse(_job_payload(job), status_code=202)


async def job_events(request: Request) -> StreamingResponse:
    job_id = request.path_params["job_id"]
    if job_id not in _JOBS:
        raise HTTPException(404, detail={
            "code": "not_found", "message": "job not found"})

    async def events():
        previous = None
        while True:
            job = _JOBS.get(job_id)
            if job is None:
                yield "event: error\ndata: {}\n\n"
                break
            payload = json.dumps(_job_payload(job), ensure_ascii=False)
            if payload != previous:
                yield f"data: {payload}\n\n"
                previous = payload
            if job.status in {"done", "failed", "cancelled"}:
                break
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream")


async def transcribe_job(request: Request) -> JSONResponse:
    job_id = request.path_params["job_id"]
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail={
            "code": "not_found", "message": "job not found"})
    if request.method == "DELETE":
        process = job.process
        if process is not None and process.poll() is None:
            process.terminate()
        job.status = "cancelled"
        job.stage = "cancelled"
        job.finished_at = now_iso()
        return JSONResponse(_job_payload(job))
    return JSONResponse(_job_payload(job))


def _system_payload() -> dict:
    gpu = {}
    try:
        output = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.used,memory.total,utilization.gpu,"
             "temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if output:
            parts = [part.strip() for part in output.split(",")]
            gpu = {
                "name": parts[0], "memory_used_mb": int(parts[1]),
                "memory_total_mb": int(parts[2]),
                "utilization": int(parts[3]),
                "temperature_c": int(parts[4]),
            }
    except Exception:
        gpu = {}
    memory = {}
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.strip().split()[0])
        memory = {
            "total_mb": info.get("MemTotal", 0) // 1024,
            "available_mb": info.get("MemAvailable", 0) // 1024,
        }
    except Exception:
        pass
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = None
    try:
        disk = shutil.disk_usage(ROOT)
        disk_payload = {
            "total_gb": round(disk.total / 1e9, 1),
            "used_gb": round(disk.used / 1e9, 1),
            "free_gb": round(disk.free / 1e9, 1),
        }
    except OSError:
        disk_payload = {}
    models = []
    model_root = ROOT / "models"
    if model_root.is_dir():
        for path in sorted(model_root.iterdir()):
            if path.suffix == ".nemo":
                name = path.stem.lower()
                backend = ("parakeet" if "parakeet" in name else
                           "canary" if "canary" in name else "nemo")
                models.append({"name": path.stem, "path": str(path),
                               "backend": backend, "kind": "nemo"})
            elif path.is_dir() and (path / "config.json").is_file():
                try:
                    config = json.loads(
                        (path / "config.json").read_text(
                            encoding="utf-8-sig"))
                except Exception:
                    continue
                model_type = str(config.get("model_type", ""))
                name = path.name
                if "ForcedAligner" in name:
                    continue
                backend = ""
                if model_type == "qwen3_asr":
                    backend = "qwen3_asr"
                elif model_type == "nemo_speechlm":
                    backend = "nemotron"
                elif model_type == "qwen3_5" or model_type == "qwen3_5_moe":
                    backend = "mt"
                if backend:
                    models.append({"name": name, "path": str(path),
                                   "backend": backend,
                                   "kind": "hf", "model_type": model_type})
    return {
        "gpu": gpu, "cpu": {"load1": load1, "load5": load5,
                            "load15": load15},
        "memory": memory, "disk": disk_payload,
        "models": models,
    }


async def system_info(request: Request) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(_system_payload))


async def transcribe_backends(request: Request) -> JSONResponse:
    payload = await asyncio.to_thread(
        _run_cli, ["transcribe", "--list-backends"])
    return JSONResponse(payload)


async def _styles(project, sub) -> JSONResponse:
    state = subproject_state(project, sub)
    candidates = [
        state.paths.renders / f"{_slug(sub.name)}.ass",
        state.paths.assets / "template.ass",
    ]
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        return JSONResponse({"styles": [], "path": None})
    document = parse_ass(path)
    return JSONResponse({
        "path": str(path),
        "styles": [
            {"name": style.name, **style.fields}
            for style in document.styles
        ],
    })


async def project_styles(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _styles(project, sub)


async def subproject_styles(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _styles(project, sub)


async def _search(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    query = request.query_params.get("q", "")
    if not state.paths.index.is_file():
        build_index(state.paths)
    results = search_index(state.paths, query,
                           limit=int(request.query_params.get("limit", 50)))
    return JSONResponse({"query": query, "results": results})


async def search(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _search(project, sub, request)


async def subproject_search(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _search(project, sub, request)


def _qa_terms(project, sub, state):
    terms = effective_terms(project, sub)
    if not terms:
        terms = read_terms(state.paths.assets / "terms.jsonl")
    return terms


async def _qa(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    if request.method == "GET":
        items = read_qa(state.paths.qa)
        return JSONResponse([asdict(item) for item in items])
    if request.method == "PATCH":
        body = await _json(request)
        items = read_qa(state.paths.qa)
        item_id = request.path_params.get("qa_id")
        try:
            item = update_qa(items, item_id,
                             QAStatus(str(body.get("status", "accepted"))))
        except KeyError:
            raise HTTPException(404, detail={
                "code": "not_found", "message": "QA item not found"})
        write_qa(state.paths.qa, items)
        return JSONResponse(asdict(item))
    items = run_qa(state.project.id, read_segments(state.paths),
                   read_cues(state.paths), _qa_terms(project, sub, state))
    write_qa(state.paths.qa, items)
    return JSONResponse([asdict(item) for item in items])


async def project_qa(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _qa(project, sub, request)


async def subproject_qa(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _qa(project, sub, request)


async def _suggest(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    query = request.query_params.get("q", "").strip()
    terms = _qa_terms(project, sub, state)
    units = effective_tm(project, sub)
    if not units:
        units = read_units(state.paths.assets / "tm.jsonl")
    return JSONResponse({
        "query": query,
        "tm": [item.__dict__ for item in find_matches(query, units)],
        "terms": [item.__dict__ for item in match_terms(
            query, terms, confirmed_only=False)],
    })


async def project_suggest(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _suggest(project, sub, request)


async def subproject_suggest(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _suggest(project, sub, request)


async def _history(project, sub, request: Request) -> JSONResponse:
    state = subproject_state(project, sub)
    if request.method == "POST":
        body = await _json(request)
        action = str(body.get("action") or "undo")
        if action == "undo":
            changed = history_undo(state.paths)
        elif action == "redo":
            changed = history_redo(state.paths)
        else:
            raise HTTPException(400, detail={
                "code": "invalid_argument", "message": "action must be undo/redo"})
        return JSONResponse({"changed": changed,
                             **history_available(state.paths)})
    return JSONResponse(history_available(state.paths))


async def project_history(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _history(project, sub, request)


async def subproject_history(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _history(project, sub, request)


def _all_termbases(root: Path, project_id: str | None = None) -> list[dict]:
    result = []
    for child in _children(root):
        if not _has_project(child):
            continue
        try:
            project = load_workspace(child)
        except Exception:
            continue
        if project_id and project.id != project_id:
            continue
        for ref in project.termbases:
            result.append({
                "project_id": project.id,
                "project_name": project.name,
                "termbase": asdict(ref),
                "entries": [asdict(term)
                            for term in read_termbase(project, ref)],
            })
    return result


def _all_tm(root: Path, project_id: str | None = None) -> list[dict]:
    result = []
    for child in _children(root):
        if not _has_project(child):
            continue
        try:
            project = load_workspace(child)
        except Exception:
            continue
        if project_id and project.id != project_id:
            continue
        for ref in project.tm:
            result.append({
                "project_id": project.id,
                "project_name": project.name,
                "tm": asdict(ref),
                "units": [asdict(unit) for unit in read_tm(project, ref)],
            })
    return result


async def all_termbases(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project_id = request.query_params.get("project_id") or None
    return JSONResponse(await asyncio.to_thread(
        _all_termbases, root, project_id))


async def all_tm(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project_id = request.query_params.get("project_id") or None
    return JSONResponse(await asyncio.to_thread(_all_tm, root, project_id))


async def settings_view(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    if request.method == "GET":
        return JSONResponse(public_settings(root))
    body = await _json(request)
    save_settings(root, body)
    return JSONResponse(public_settings(root))


def _service_status(name: str, url: str) -> dict:
    if not url:
        return {"name": name, "url": "", "state": "not_configured"}
    health_url = url.rstrip("/") + "/health"
    try:
        import urllib.request
        with urllib.request.urlopen(health_url, timeout=2) as response:
            return {"name": name, "url": url, "state": "ok",
                    "http": response.status}
    except Exception as exc:
        return {"name": name, "url": url, "state": "unreachable",
                "detail": str(exc)[:200]}


def _health_status(root: Path) -> dict:
    system = _system_payload()
    settings = load_settings(root)
    services = [{"name": "ncat-api", "url": "", "state": "ok"}]
    asr_url = (os.environ.get(CLOUD_ASR_URL) or ""
               if settings["asr"].get("mode") == "cloud"
               else os.environ.get(LOCAL_ASR_URL,
                                   "http://127.0.0.1:8081"))
    mt_url = (os.environ.get(CLOUD_MT_URL) or ""
              if settings["mt"].get("mode") == "cloud"
              else os.environ.get(LOCAL_MT_URL, "http://127.0.0.1:8080"))
    services.append(_service_status("ASR", asr_url))
    services.append(_service_status("MT", mt_url))
    return {
        "cpu": system["cpu"], "gpu": system["gpu"],
        "memory": system["memory"], "disk": system["disk"],
        "services": services,
    }


async def health_status(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    return JSONResponse(await asyncio.to_thread(_health_status, root))


# ---------------------------------------------------------------- routes
def _routes() -> list:
    return [
        Route("/api/v1/health", health),
        Route("/api/v1/health/status", health_status),
        Route("/api/v1/settings", settings_view, methods=["GET", "PATCH"]),
        Route("/api/v1/termbases", all_termbases),
        Route("/api/v1/tm", all_tm),
        Route("/api/v1/settings", settings_view, methods=["GET", "PATCH"]),
        Route("/api/v1/system", system_info),
        Route("/api/v1/workspace", workspace),
        Route("/api/v1/projects", projects, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}", project_detail),
        Route("/api/v1/projects/{project_id}/actions/{action}",
              project_actions, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/segments",
              project_segments, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/segments/{segment_id}",
              segment_detail, methods=["PATCH", "DELETE"]),
        Route("/api/v1/projects/{project_id}/cues",
              project_cues, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/cues/{cue_id}",
              cue_detail, methods=["PATCH", "DELETE"]),
        Route("/api/v1/projects/{project_id}/cues/{cue_id}/split",
              cue_split, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/cues/{cue_id}/merge",
              cue_merge, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/import/ass",
              import_ass, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/transcript", transcript),
        Route("/api/v1/projects/{project_id}/runs", project_runs),
        Route("/api/v1/projects/{project_id}/media-info", media_info),
        Route("/api/v1/projects/{project_id}/files/{path:path}",
              project_files),
        Route("/api/v1/projects/{project_id}/termbases",
              termbases, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/termbases/{termbase_id}",
              termbase_detail, methods=["GET", "DELETE"]),
        Route("/api/v1/projects/{project_id}/tm",
              tms, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/tm/{tm_id}",
              tm_detail, methods=["GET", "DELETE"]),
        Route("/api/v1/projects/{project_id}/subprojects",
              subprojects, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}",
              subproject_detail, methods=["GET", "PATCH"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/actions/{action}",
              subproject_actions, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/segments",
              subproject_segments, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/segments/{segment_id}",
              subproject_segment_detail, methods=["PATCH", "DELETE"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/cues",
              subproject_cues, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/cues/{cue_id}",
              subproject_cue_detail, methods=["PATCH", "DELETE"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/cues/{cue_id}/split",
              subproject_cue_split, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/cues/{cue_id}/merge",
              subproject_cue_merge, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/import/ass",
              subproject_import_ass, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/transcript",
              subproject_transcript),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/runs",
              subproject_runs),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/media-info",
              subproject_media_info),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/files/{path:path}",
              subproject_files),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/terms",
              effective_terms_view),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/tm",
              effective_tm_view),
        Route("/api/v1/projects/{project_id}/search", search),
        Route("/api/v1/projects/{project_id}/qa", project_qa,
              methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/suggest", project_suggest),
        Route("/api/v1/projects/{project_id}/history", project_history,
              methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/styles",
              subproject_styles),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/search",
              subproject_search),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/qa",
              subproject_qa, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/qa/{qa_id}",
              subproject_qa, methods=["PATCH"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/suggest",
              subproject_suggest),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/history",
              subproject_history, methods=["GET", "POST"]),
        Route("/api/v1/transcribe/backends", transcribe_backends),
        Route("/api/v1/transcribe/jobs", transcribe_jobs,
              methods=["GET", "POST"]),
        Route("/api/v1/transcribe/jobs/{job_id}/events", job_events),
        Route("/api/v1/transcribe/jobs/{job_id}", transcribe_job,
              methods=["GET", "DELETE"]),
    ]


async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    payload = detail if isinstance(detail, dict) else {
        "code": "http_error", "message": str(detail)}
    return JSONResponse({"error": payload}, status_code=exc.status_code)


def create_app(*, workspace: str | Path | None = None) -> Starlette:
    app = Starlette(routes=_routes())
    app.add_exception_handler(HTTPException, _http_error)
    app.state.workspace = str(workspace_root(workspace))
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True),
                  name="static")
    return app
