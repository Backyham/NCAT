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
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import (
    FileResponse, JSONResponse, Response, StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ncat import __version__
from ncat.core import workflow as project_workflow
from ncat.core.actions import (
    ActionError, run_align, run_ingest, run_render, run_segment,
    run_style, run_translate,
)
from ncat.core.assets.tm import (
    find_matches, read_tmx, read_units, write_tmx, write_units,
)
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
from ncat.core.mt import get_backend
from ncat.core.mt.base import MTRequest, SegmentInput, TMMatch
from ncat.core.project import (
    load_project, new_id, now_iso, read_cues, read_segments, save_project,
    write_cues, write_segments,
)
from ncat.core.runs import list_runs
from ncat.core.api import api_endpoint, resolve_api_key
from ncat.core.providers import ProviderError, fetch_models
from ncat.core.settings import (
    ProviderNotConfigured, apply_action_defaults, load_settings,
    public_settings, save_settings,
)
from ncat.core.segment import segments_from_pairs
from ncat.core.subs.ass import parse_ass
from ncat.core.subs.model import (
    cues_from_ass, cues_from_segments, merge_cues, split_cue,
)
from ncat.core.subs.rules import apply_all, cue_metrics
from ncat.core.subs import styles as ass_styles
from ncat.core.subs.tags import cue_kind, tag_count, visible_text
from ncat.core.text import normalise_key, source_hash
from ncat.core.types import (
    Cue, ProjectStage, QAStatus, Segment, SegmentStatus, TMUnit,
)
from ncat.core.activity import log_activity, read_activity
from ncat.core.workspace import (
    Attachment, AutomationRule, Comment, ExternalLink, InternalLink,
    Subtask, TermbaseRef, TMRef, WorkspaceProject, add_category,
    add_lane, add_subproject, add_termbase, add_tm, category_of,
    create_workspace, delete_category, delete_lane, delete_subproject,
    effective_terms, effective_tm, import_termbase, import_tm_pair,
    lane_of, load_workspace, read_tm, read_termbase, save_workspace,
    subproject_state, update_category, update_lane, update_subproject,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "static"
DEFAULT_PLUGINS = ",".join([
    "parakeet=ncat.test.asr_backend.parakeet:make_backend",
    "canary=ncat.test.asr_backend.canary:make_backend",
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
    candidate = ROOT / ".venv-asr" / "bin" / "python"
    return str(candidate) if candidate.is_file() else sys.executable


def _run_asr_worker(arguments: list[str], *,
                    backend: str | None = None) -> dict[str, Any]:
    """Run an internal ASR worker in the model virtualenv."""
    env = os.environ.copy()
    env["NCAT_ASR_PLUGINS"] = (env.get("NCAT_ASR_PLUGINS")
                               or DEFAULT_PLUGINS)
    command = [
        _action_python(backend), "-m",
        "ncat.worker.transcribe_project", *arguments,
    ]
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


def _transcribe_worker_arguments(project_root: Path, body: dict) -> list[str]:
    arguments = [str(project_root)]
    if body.get("backend"):
        arguments += ["--backend", str(body["backend"])]
    if body.get("model"):
        arguments += ["--model", str(body["model"])]
    if body.get("language"):
        arguments += ["--language", str(body["language"])]
    arguments += ["--offset-ms", str(_int_option(body.get("offset_ms"), 0))]
    arguments += ["--chunk-ms", str(_int_option(body.get("chunk_ms"), 0))]
    for key, value in (body.get("options") or {}).items():
        arguments += ["--option", f"{key}={json.dumps(value)}"]
    fallback = body.get("fallback") or {}
    if fallback:
        if fallback.get("backend"):
            arguments += ["--fallback-backend", str(fallback["backend"])]
        if fallback.get("model"):
            arguments += ["--fallback-model", str(fallback["model"])]
        for key, value in (fallback.get("options") or {}).items():
            arguments += ["--fallback-option", f"{key}={json.dumps(value)}"]
    arguments.append("--no-pause-after-transcript")
    return arguments


def _run_transcribe_worker(project_root: Path, body: dict) -> dict[str, Any]:
    return _run_asr_worker(
        _transcribe_worker_arguments(project_root, body),
        backend=body.get("backend"),
    )


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
    payload = asdict(sub)
    payload.pop("workflow", None)
    return {
        **payload,
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
        "categories": [asdict(item) for item in project.categories],
        "features": dict(project.features),
        "automation": [asdict(item) for item in project.automation],
        "subprojects": subs,
        "termbases": [_termbase_payload(project, ref)
                      for ref in project.termbases],
        "tm": [_tm_payload(project, ref) for ref in project.tm],
        "counts": {
            "subprojects": len(subs),
            "done": sum(1 for item in subs if item.get("done")),
            "open": sum(1 for item in subs if not item.get("done")),
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
    return JSONResponse({"ok": True, "version": __version__})


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
    subproject_name = str(body.get("subproject_name") or "")
    if source_path is not None and not subproject_name:
        subproject_name = "Main"
    layout = str(body.get("layout") or "default")
    if layout not in {"default", "empty"}:
        raise HTTPException(400, detail={
            "code": "invalid_argument", "message": "layout must be default or empty"})
    if source_path is not None:
        layout = "default"
    project = create_workspace(
        directory, name,
        client=str(body.get("client") or ""),
        source_lang=str(body.get("source_lang") or "en"),
        target_langs=body.get("target_langs") or ["zh-Hans"],
        source_path=source_path,
        subproject_name=subproject_name,
        kind=str(body.get("kind") or "video"),
        layout=layout,
    )
    return JSONResponse(_project_payload(project), status_code=201)


async def project_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    if request.method == "PATCH":
        body = await _json(request)
        if body.get("name") is not None:
            project.name = str(body["name"]).strip() or project.name
            save_workspace(project)
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
    try:
        sub = add_subproject(
            project, str(body.get("name") or "新项目"),
            kind=str(body.get("kind") or "video"),
            source_path=source_path,
            source_lang=body.get("source_lang"),
            target_langs=body.get("target_langs"),
            category_id=str(body.get("category_id") or ""),
            lane_id=str(body.get("lane_id") or ""),
        )
    except ValueError as error:
        raise HTTPException(400, detail={
            "code": "layout_required" if str(error) == "layout_required"
            else "invalid_argument",
            "message": "请先在看板设置中创建分类和泳道"
            if str(error) == "layout_required" else str(error),
        }) from error
    log_activity(project, "subproject_added", subproject_id=sub.id,
                 name=sub.name, category_id=sub.category_id,
                 lane_id=sub.lane_id)
    state = subproject_state(project, sub)
    return JSONResponse(_sub_payload(project, sub, state), status_code=201)


async def subproject_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    subproject_id = request.path_params["subproject_id"]
    if request.method == "DELETE":
        try:
            deleted = delete_subproject(project, subproject_id)
        except FileNotFoundError as error:
            raise HTTPException(404, detail={
                "code": "not_found", "message": str(error)}) from error
        except ValueError as error:
            raise HTTPException(400, detail={
                "code": "invalid_argument", "message": str(error)}) from error
        for remaining in project.subprojects:
            remaining.internal_links = [
                link for link in remaining.internal_links
                if link.target_subproject_id != deleted.id
            ]
        save_workspace(project)
        log_activity(project, "subproject_deleted", subproject_id=deleted.id,
                     name=deleted.name, category_id=deleted.category_id,
                     lane_id=deleted.lane_id)
        return JSONResponse({"ok": True, "deleted": deleted.id})
    sub = project.subproject(subproject_id)
    if request.method == "PATCH":
        body = await _json(request)
        previous = (sub.category_id, sub.lane_id)
        try:
            sub = update_subproject(
                project, sub.id,
                **{key: body[key] for key in
                   ("name", "description", "category_id", "lane_id", "due",
                    "done") if key in body})
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(400, detail={
                "code": "invalid_argument", "message": str(error)}) from error
        if previous != (sub.category_id, sub.lane_id):
            log_activity(project, "subproject_moved", subproject_id=sub.id,
                         name=sub.name, from_category_id=previous[0],
                         from_lane_id=previous[1],
                         to_category_id=sub.category_id,
                         to_lane_id=sub.lane_id,
                         category_id=sub.category_id, lane_id=sub.lane_id)
    state = subproject_state(project, sub)
    return JSONResponse(_sub_payload(project, sub, state))


async def board_project_view(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    return JSONResponse(await asyncio.to_thread(project_workflow.board, project))


async def project_overview(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    board = await asyncio.to_thread(project_workflow.board, project)
    today = now_iso()[:10]
    categories = []
    total = done_count = overdue = 0
    for category in board["categories"]:
        lanes = []
        category_total = category_done = 0
        for lane in category["lanes"]:
            lane_total = len(lane["cards"])
            lane_done = sum(1 for card in lane["cards"] if card["done"])
            lane_overdue = sum(
                1 for card in lane["cards"]
                if not card["done"] and card.get("due")
                and str(card["due"]) < today)
            total += lane_total
            done_count += lane_done
            overdue += lane_overdue
            category_total += lane_total
            category_done += lane_done
            lanes.append({
                "id": lane["id"], "name": lane["name"],
                "total": lane_total, "done": lane_done,
                "overdue": lane_overdue,
            })
        categories.append({
            "id": category["id"], "name": category["name"],
            "total": category_total, "done": category_done,
            "lanes": lanes,
        })
    return JSONResponse({
        "project": {
            "id": project.id, "name": project.name,
            "features": dict(project.features),
        },
        "totals": {
            "subprojects": total, "done": done_count,
            "open": total - done_count, "overdue": overdue,
        },
        "categories": categories,
        "activity": read_activity(project, limit=50),
    })


async def project_activity(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    try:
        limit = int(request.query_params.get("limit") or 50)
    except ValueError:
        limit = 50
    return JSONResponse({"events": read_activity(project, limit=limit)})


async def categories_view(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    body = await _json(request)
    category = add_category(project, str(body.get("name") or "新分类"))
    save_workspace(project)
    log_activity(project, "category_added", category_id=category.id,
                 name=category.name)
    return JSONResponse(asdict(category), status_code=201)


async def category_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    category_id = request.path_params["category_id"]
    if request.method == "DELETE":
        try:
            category = delete_category(project, category_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(400, detail={
                "code": "invalid_argument", "message": str(error)}) from error
        log_activity(project, "category_deleted", category_id=category.id,
                     name=category.name)
        return JSONResponse({"ok": True})
    body = await _json(request)
    try:
        category = update_category(
            project, category_id,
            name=body.get("name"), order=body.get("order"))
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(400, detail={
            "code": "invalid_argument", "message": str(error)}) from error
    return JSONResponse(asdict(category))


async def lanes_view(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    category = category_of(project, request.path_params["category_id"])
    body = await _json(request)
    lane = add_lane(project, category, str(body.get("name") or "新泳道"))
    save_workspace(project)
    log_activity(project, "lane_added", category_id=category.id,
                 lane_id=lane.id, name=lane.name)
    return JSONResponse(asdict(lane), status_code=201)


async def lane_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    category_id = request.path_params["category_id"]
    lane_id = request.path_params["lane_id"]
    if request.method == "DELETE":
        try:
            lane = delete_lane(project, category_id, lane_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(400, detail={
                "code": "invalid_argument", "message": str(error)}) from error
        log_activity(project, "lane_deleted", category_id=category_id,
                     lane_id=lane.id, name=lane.name)
        return JSONResponse({"ok": True})
    body = await _json(request)
    try:
        lane = update_lane(
            project, category_id, lane_id,
            name=body.get("name"), order=body.get("order"))
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(400, detail={
            "code": "invalid_argument", "message": str(error)}) from error
    return JSONResponse(asdict(lane))


async def project_features(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    body = await _json(request)
    for key in project.features:
        if key in body:
            project.features[key] = bool(body[key])
    save_workspace(project)
    return JSONResponse({"features": dict(project.features)})


async def project_automation(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    if request.method == "GET":
        return JSONResponse({"rules": [asdict(item) for item in project.automation]})
    body = await _json(request)
    title = str(body.get("subtask_title") or "").strip()
    category_id = str(body.get("target_category_id") or "")
    lane_id = str(body.get("target_lane_id") or "")
    if not title or not category_id or not lane_id:
        raise HTTPException(400, detail={
            "code": "invalid_argument", "message": "subtask title and target lane are required"})
    category = category_of(project, category_id)
    lane_of(project, category, lane_id)
    rule = AutomationRule(
        id=new_id("rule"), subtask_title=title,
        target_category_id=category_id, target_lane_id=lane_id,
        category_scope_id=body.get("category_scope_id") or None,
        enabled=bool(body.get("enabled", True)),
        order=len(project.automation),
    )
    project.automation.append(rule)
    save_workspace(project)
    return JSONResponse(asdict(rule), status_code=201)


async def automation_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    rule = next((item for item in project.automation
                 if item.id == request.path_params["rule_id"]), None)
    if rule is None:
        raise HTTPException(404, detail={"code": "not_found",
                                         "message": "rule not found"})
    if request.method == "DELETE":
        project.automation = [item for item in project.automation
                              if item.id != rule.id]
        save_workspace(project)
        return JSONResponse({"ok": True})
    body = await _json(request)
    if body.get("subtask_title") is not None:
        rule.subtask_title = str(body["subtask_title"]).strip()
    if body.get("target_category_id") is not None:
        rule.target_category_id = str(body["target_category_id"])
    if body.get("target_lane_id") is not None:
        rule.target_lane_id = str(body["target_lane_id"])
    if "enabled" in body:
        rule.enabled = bool(body["enabled"])
    if "category_scope_id" in body:
        rule.category_scope_id = body["category_scope_id"] or None
    save_workspace(project)
    return JSONResponse(asdict(rule))


async def subproject_subtasks(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = next((item for item in project.subprojects
                if item.id == request.path_params["subproject_id"]), None)
    if sub is None:
        raise HTTPException(404, detail={"code": "not_found",
                                         "message": "subproject not found"})
    body = await _json(request)
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, detail={"code": "invalid_argument",
                                         "message": "title required"})
    task = Subtask(id=new_id("task"), title=title, order=len(sub.subtasks),
                   created_at=now_iso())
    sub.subtasks.append(task)
    sub.updated_at = now_iso()
    save_workspace(project)
    log_activity(project, "subtask_added", subproject_id=sub.id,
                 name=sub.name, subtask_id=task.id, title=title,
                 category_id=sub.category_id, lane_id=sub.lane_id)
    return JSONResponse(asdict(task), status_code=201)


async def subtask_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = next((item for item in project.subprojects
                if item.id == request.path_params["subproject_id"]), None)
    if sub is None:
        raise HTTPException(404, detail={"code": "not_found",
                                         "message": "subproject not found"})
    task = next((item for item in sub.subtasks
                 if item.id == request.path_params["task_id"]), None)
    if task is None:
        raise HTTPException(404, detail={"code": "not_found",
                                         "message": "subtask not found"})
    if request.method == "DELETE":
        sub.subtasks = [item for item in sub.subtasks if item.id != task.id]
        sub.updated_at = now_iso()
        save_workspace(project)
        log_activity(project, "subtask_deleted", subproject_id=sub.id,
                     name=sub.name, subtask_id=task.id, title=task.title,
                     category_id=sub.category_id, lane_id=sub.lane_id)
        return JSONResponse({"ok": True})
    body = await _json(request)
    if body.get("title") is not None:
        task.title = str(body["title"]).strip() or task.title
    if "order" in body:
        task.order = int(body["order"])
    if "done" in body:
        done = bool(body["done"])
        changed = done != task.done
        task.done = done
        task.completed_at = now_iso() if done else None
        if changed:
            log_activity(
                project,
                "subtask_completed" if done else "subtask_uncompleted",
                subproject_id=sub.id, name=sub.name, subtask_id=task.id,
                title=task.title, category_id=sub.category_id,
                lane_id=sub.lane_id,
            )
            if done:
                project_workflow.apply_subtask_automation(
                    project, sub, task.title)
    sub.updated_at = now_iso()
    save_workspace(project)
    state = subproject_state(project, sub)
    return JSONResponse(_sub_payload(project, sub, state))


def _subproject_for(project: WorkspaceProject, subproject_id: str):
    sub = next((item for item in project.subprojects
                if item.id == subproject_id), None)
    if sub is None:
        raise HTTPException(404, detail={"code": "not_found",
                                         "message": "subproject not found"})
    return sub


async def internal_links(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = _subproject_for(project, request.path_params["subproject_id"])
    body = await _json(request)
    target = str(body.get("target_subproject_id") or "")
    if target == sub.id or not any(item.id == target for item in project.subprojects):
        raise HTTPException(400, detail={"code": "invalid_argument",
                                         "message": "invalid target subproject"})
    link = InternalLink(id=new_id("link"), target_subproject_id=target,
                        label=str(body.get("label") or ""))
    sub.internal_links.append(link)
    sub.updated_at = now_iso()
    save_workspace(project)
    return JSONResponse(asdict(link), status_code=201)


async def internal_link_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = _subproject_for(project, request.path_params["subproject_id"])
    link_id = request.path_params["link_id"]
    sub.internal_links = [item for item in sub.internal_links
                          if item.id != link_id]
    sub.updated_at = now_iso()
    save_workspace(project)
    return JSONResponse({"ok": True})


async def external_links(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = _subproject_for(project, request.path_params["subproject_id"])
    body = await _json(request)
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, detail={"code": "invalid_argument",
                                         "message": "url required"})
    link = ExternalLink(id=new_id("link"), label=str(body.get("label") or ""),
                        url=url)
    sub.external_links.append(link)
    sub.updated_at = now_iso()
    save_workspace(project)
    return JSONResponse(asdict(link), status_code=201)


async def external_link_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = _subproject_for(project, request.path_params["subproject_id"])
    sub.external_links = [item for item in sub.external_links
                          if item.id != request.path_params["link_id"]]
    sub.updated_at = now_iso()
    save_workspace(project)
    return JSONResponse({"ok": True})


async def subproject_comments(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = _subproject_for(project, request.path_params["subproject_id"])
    body = await _json(request)
    text = str(body.get("body") or "").strip()
    if not text:
        raise HTTPException(400, detail={"code": "invalid_argument",
                                         "message": "comment body required"})
    comment = Comment(id=new_id("cmt"), body=text, created_at=now_iso())
    sub.comments.append(comment)
    sub.updated_at = now_iso()
    save_workspace(project)
    return JSONResponse(asdict(comment), status_code=201)


async def comment_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = _subproject_for(project, request.path_params["subproject_id"])
    sub.comments = [item for item in sub.comments
                    if item.id != request.path_params["comment_id"]]
    sub.updated_at = now_iso()
    save_workspace(project)
    return JSONResponse({"ok": True})


async def subproject_attachments(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = _subproject_for(project, request.path_params["subproject_id"])
    if request.method == "GET":
        return JSONResponse({"attachments": [asdict(item) for item in sub.attachments]})
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(400, detail={"code": "invalid_argument",
                                         "message": "file required"})
    directory = project.subproject_root(sub) / "attachments"
    directory.mkdir(parents=True, exist_ok=True)
    attachment_id = new_id("att")
    safe_name = Path(getattr(upload, "filename", "attachment")).name or "attachment"
    target = directory / f"{attachment_id}-{safe_name}"
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            handle.write(chunk)
    attachment = Attachment(
        id=attachment_id, name=safe_name, size=target.stat().st_size,
        content_type=getattr(upload, "content_type", "") or "",
        created_at=now_iso(),
        path=str(target.relative_to(project.root)),
    )
    sub.attachments.append(attachment)
    sub.updated_at = now_iso()
    save_workspace(project)
    return JSONResponse(asdict(attachment), status_code=201)


async def attachment_detail(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = _subproject_for(project, request.path_params["subproject_id"])
    attachment = next((item for item in sub.attachments
                       if item.id == request.path_params["attachment_id"]), None)
    if attachment is None:
        raise HTTPException(404, detail={"code": "not_found",
                                         "message": "attachment not found"})
    if request.method == "DELETE":
        try:
            (project.root / attachment.path).unlink(missing_ok=True)
        except OSError:
            pass
        sub.attachments = [item for item in sub.attachments
                           if item.id != attachment.id]
        sub.updated_at = now_iso()
        save_workspace(project)
        return JSONResponse({"ok": True})
    return FileResponse(project.root / attachment.path,
                        filename=attachment.name,
                        media_type=attachment.content_type or None)


# ------------------------------------------------------------------- actions
CORE_ACTIONS = frozenset({
    "ingest", "segment", "align", "translate", "style", "render",
})


def _int_option(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _run_core_action(action: str, state, project, body: dict) -> dict:
    if action == "ingest":
        return run_ingest(
            state, height=_int_option(body.get("height"), 720),
            encoder=body.get("encoder") or None,
            force=bool(body.get("force")))
    if action == "segment":
        return run_segment(
            state, propose=bool(body.get("propose", True)),
            import_path=body.get("import_path"),
            target=body.get("target"), force=bool(body.get("force")))
    if action == "align":
        return run_align(
            state, offset_ms=_int_option(body.get("offset_ms"), 0))
    if action == "translate":
        return run_translate(
            state, backend=body.get("backend") or "llama_cpp",
            base_url=body.get("base_url"),
            model=body.get("model") or "local",
            target_lang=body.get("target_lang"),
            brief=body.get("brief") or "", style=body.get("style") or "",
            genre=body.get("genre") or "字幕",
            limit=_int_option(body.get("limit"), 0),
            options=body.get("options") or {},
            fallback=body.get("fallback"))
    if action == "style":
        return run_style(
            state, template=body.get("template"), output=body.get("output"),
            bilingual=body.get("bilingual") is not False)
    if action == "render":
        return run_render(
            state, ass=body.get("ass"),
            preset=body.get("preset") or "original",
            encoder=body.get("encoder") or "libx264",
            crf=_int_option(body.get("crf"), 20),
            output=body.get("output"))
    raise ActionError("not_found", f"unknown action: {action}")


ACTION_FEATURES = {
    "ingest": "ffmpeg",
    "transcribe": "asr",
    "segment": "segment",
    "align": "timing",
    "translate": "mt",
    "style": "subtitle_edit",
    "render": "export",
}


async def _run_action(project, sub, body: dict, action: str) -> JSONResponse:
    required = ACTION_FEATURES.get(action)
    if required and not project.features.get(required, True):
        raise HTTPException(400, detail={
            "code": "feature_disabled",
            "message": f"feature disabled: {required}",
        })
    state = subproject_state(project, sub)
    history_push(state.paths)
    try:
        if action == "transcribe":
            result = await asyncio.to_thread(
                _run_transcribe_worker, state.paths.root, body)
        elif action in CORE_ACTIONS:
            result = await asyncio.to_thread(
                _run_core_action, action, state, project, body)
        else:
            raise HTTPException(404, detail={
                "code": "not_found",
                "message": f"unknown action: {action}"})
    except ActionError as error:
        detail = {"code": error.code, "message": error.message}
        if error.fields:
            detail["fields"] = error.fields
        raise HTTPException(400, detail=detail) from error
    build_index(state.paths)
    return JSONResponse(result)


async def project_actions(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, state = _resolve_sub(project)
    body = await _json(request) if request.method == "POST" else {}
    action = request.path_params["action"]
    if action in {"transcribe", "translate"}:
        try:
            body = apply_action_defaults(root, action, body)
        except ProviderNotConfigured as error:
            raise HTTPException(400, detail={
                "code": "provider_not_configured",
                "message": str(error)}) from error
    return await _run_action(project, sub, body, action)


async def subproject_actions(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    body = await _json(request) if request.method == "POST" else {}
    action = request.path_params["action"]
    if action in {"transcribe", "translate"}:
        try:
            body = apply_action_defaults(root, action, body)
        except ProviderNotConfigured as error:
            raise HTTPException(400, detail={
                "code": "provider_not_configured",
                "message": str(error)}) from error
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
            {
                **asdict(item),
                "metrics": cue_metrics(item),
                "kind": cue_kind(item.source_text, item.target_text),
                "tag_count": tag_count(item.target_text),
            }
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
    if "status" in body:
        _audit(state, "status_change", segment_id=found.id,
               status=found.status.value)
        if found.status == SegmentStatus.APPROVED:
            _remember_translation(state, found.source, found.target,
                                  segment_id=found.id)
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


def _audit(state, kind: str, **data) -> None:
    """Append one audit record; failures never break the request."""
    row = {"at": now_iso(), "kind": kind, **data}
    path = state.paths.root / "audit.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _local_tm_path(state) -> Path:
    return state.paths.assets / "tm.jsonl"


def _local_tm_units(state) -> list[TMUnit]:
    return read_units(_local_tm_path(state))


def _remember_translation(state, source: str, target: str, *,
                          segment_id: str | None = None) -> bool:
    """Write an approved pair into the subproject TM (confirmed only)."""
    source_text = visible_text(source).strip()
    target_text = visible_text(target).strip()
    if not source_text or not target_text:
        return False
    units = _local_tm_units(state)
    key = source_hash(source_text)
    units = [unit for unit in units if unit.source_hash != key]
    project = state.project
    targets = list(getattr(project, "target_langs", None) or ["zh-Hans"])
    units.append(TMUnit(
        id=new_id("tm"), source=source_text, target=target_text,
        source_lang=getattr(project, "source_lang", "") or "en",
        target_lang=targets[0],
        source_hash=key, origin_project=project.id,
        origin_segment=segment_id, approved_at=now_iso(),
        created_at=now_iso(),
    ))
    write_units(_local_tm_path(state), units)
    _audit(state, "tm_writeback", segment_id=segment_id,
           source=source_text, target=target_text)
    return True


def _propagate(state, cues: list[Cue], origin: Cue) -> int:
    """Copy a changed translation to identical source cues that are unconfirmed."""
    key = normalise_key(visible_text(origin.source_text))
    if not key or not origin.target_text.strip():
        return 0
    segments = {item.id: item for item in read_segments(state.paths)}
    changed = 0
    for cue in cues:
        if cue.id == origin.id or cue.locked:
            continue
        if normalise_key(visible_text(cue.source_text)) != key:
            continue
        segment = segments.get(cue.segment_id or "")
        if segment is not None and segment.status not in {
                SegmentStatus.EMPTY, SegmentStatus.MT_SUGGESTED,
                SegmentStatus.TM_SUGGESTED}:
            continue
        if cue.target_text == origin.target_text:
            continue
        cue.target_text = origin.target_text
        cue.revision += 1
        apply_all([cue])
        if segment is not None:
            segment.target = cue.target_text
            segment.status = SegmentStatus.TRANSLATED
            segment.revision += 1
            segment.updated_at = now_iso()
        changed += 1
    if changed:
        write_cues(state.paths, cues)
        write_segments(state.paths, list(segments.values()))
        build_index(state.paths)
        _audit(state, "auto_propagate", cue_id=origin.id, count=changed)
    return changed


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
    editable = {"source_text", "target_text", "style", "start_ms", "end_ms",
                "order"}
    if found.locked and (set(body) & editable):
        raise HTTPException(409, detail={
            "code": "locked",
            "message": "cue is locked; unlock it before editing",
        })
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
        if segment is not None and segment.target != found.target_text:
            # 只有译文真的变了才动 Segment：编辑器是自动保存的，未改动的
            # 保存不能把 REVIEWED/APPROVED 抹成 TRANSLATED。
            segment.target = found.target_text
            segment.status = (SegmentStatus.TRANSLATED
                              if found.target_text.strip()
                              else SegmentStatus.EMPTY)
            segment.revision += 1
            segment.updated_at = now_iso()
            write_segments(state.paths, segments)
            _audit(state, "cue_edit", cue_id=found.id,
                   segment_id=segment.id, status=segment.status.value)
    propagated = 0
    if "target_text" in body:
        propagated = _propagate(state, cues, found)
    return JSONResponse({
        **asdict(found),
        "metrics": cue_metrics(found),
        "kind": cue_kind(found.source_text, found.target_text),
        "propagated": propagated,
    })


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
    text_cues = [item for item in cues
                 if cue_kind(item.source_text, item.target_text) == "text"]
    segments = segments_from_pairs(
        [(item.source_text, item.target_text) for item in text_cues],
        state.project.id,
    )
    for cue, segment in zip(text_cues, segments):
        cue.segment_id = segment.id
    write_cues(state.paths, cues)
    write_segments(state.paths, segments)
    build_index(state.paths)
    state.project.stage = ProjectStage.DRAFTING
    save_project(state)
    return JSONResponse({
        "cues": len(cues),
        "segments": len(segments),
        "drawings": len(cues) - len(text_cues),
    })


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
        _action_python(job.backend), "-m", "ncat.worker.transcribe_audio",
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
        _run_asr_worker, ["--list-backends"])
    return JSONResponse(payload)


async def _styles(project, sub, request: Request | None = None) -> JSONResponse:
    state = subproject_state(project, sub)
    if request is not None and request.method == "PATCH":
        body = await _json(request)
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, detail={
                "code": "missing_name", "message": "style name is required"})
        values = body.get("values") or {}
        if not isinstance(values, dict):
            raise HTTPException(400, detail={
                "code": "invalid_values", "message": "values must be an object"})
        payload = await asyncio.to_thread(
            ass_styles.update_style, state.paths, name, values)
        _audit(state, "style_update", style=name)
        return JSONResponse(payload)
    return JSONResponse(await asyncio.to_thread(ass_styles.payload,
                                                state.paths))


async def project_styles(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub, _ = _resolve_sub(project)
    return await _styles(project, sub, request)


async def subproject_styles(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    return await _styles(project, sub, request)


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
    local = _local_tm_units(state)
    if local:
        seen = {unit.source_hash for unit in units}
        units = list(units) + [unit for unit in local
                               if unit.source_hash not in seen]
    return JSONResponse({
        "query": query,
        "tm": [item.__dict__ for item in find_matches(query, units)],
        "terms": [item.__dict__ for item in match_terms(
            query, terms, confirmed_only=False)],
    })


def _mt_options(root: Path) -> tuple[str, str, dict]:
    """Resolve backend, model and options from the configured MT provider."""
    routed = apply_action_defaults(root, "translate", {})
    backend = str(routed.get("backend") or "")
    model = str(routed.get("model") or "")
    options = dict(routed.get("options") or {})
    if routed.get("base_url"):
        options["base_url"] = routed["base_url"]
    options["model"] = model
    return backend, model, options


async def subproject_cue_mt(request: Request) -> JSONResponse:
    """One-cue MT suggestion; never writes to the cue, never touches locked rows."""
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    state = subproject_state(project, sub)
    cues = read_cues(state.paths)
    cue = next((item for item in cues
                if item.id == request.path_params["cue_id"]), None)
    if cue is None:
        raise HTTPException(404, detail={"code": "not_found",
                                         "message": "cue not found"})
    if cue.locked:
        raise HTTPException(409, detail={"code": "locked",
                                         "message": "cue is locked"})
    source = visible_text(cue.source_text) or visible_text(cue.target_text)
    if not source.strip():
        raise HTTPException(400, detail={
            "code": "empty_source", "message": "cue has no source text"})
    try:
        backend_name, _model, options = _mt_options(root)
    except ProviderNotConfigured as error:
        raise HTTPException(400, detail={
            "code": "provider_not_configured",
            "message": str(error)}) from error
    terms = _qa_terms(project, sub, state)
    units = list(effective_tm(project, sub)) + _local_tm_units(state)
    matches = [
        TMMatch(unit_id=hit.unit_id, source=hit.source,
                target=hit.target, score=hit.score,
                match_type=hit.match_type)
        for hit in find_matches(source, units, limit=3)
    ]
    hits = match_terms(source, terms, confirmed_only=False)
    segments = [SegmentInput(id=cue.id, source=source,
                              tm_matches=matches, term_hits=hits)]

    def build_request(request_options: dict) -> MTRequest:
        return MTRequest(
            source_lang=state.project.source_lang,
            target_lang=(state.project.target_langs or ["zh-Hans"])[0],
            segments=segments,
            options=request_options,
        )

    try:
        result = await asyncio.to_thread(
            get_backend(backend_name).translate, build_request(options))
    except Exception as error:
        raise HTTPException(502, detail={
            "code": "mt_failed", "message": str(error)[:300]}) from error
    target = result.segments[0].target if result.segments else ""
    _audit(state, "mt_suggestion", cue_id=cue.id, model=result.model,
           provider=result.provider)
    return JSONResponse({
        "target": target,
        "model": result.model,
        "provider": result.provider,
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
        },
    })


async def tm_export(request: Request) -> Response:
    root = workspace_root(request.app.state.workspace)
    groups = await asyncio.to_thread(_all_tm, root, None)
    units: dict[str, TMUnit] = {}
    for group in groups:
        for row in group.get("units", []):
            try:
                unit = TMUnit(**row)
            except TypeError:
                continue
            units[unit.source_hash] = unit
    if not units:
        raise HTTPException(404, detail={
            "code": "empty_tm", "message": "no translation units to export"})
    directory = tempfile.mkdtemp(prefix="ncat-tmx-")
    path = Path(directory) / "ncat-memory.tmx"
    write_tmx(units.values(), path)
    return FileResponse(
        path, media_type="application/xml", filename="ncat-memory.tmx",
        background=BackgroundTask(shutil.rmtree, directory,
                                  ignore_errors=True))


async def subproject_audit(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    state = subproject_state(project, sub)
    path = state.paths.root / "audit.jsonl"
    limit = max(1, min(500, int(request.query_params.get("limit", 100))))
    rows: list[dict] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return JSONResponse(rows)


def _projects_for_board(root: Path, project_id: str | None):
    if project_id:
        return [_find_workspace(root, project_id)]
    projects = []
    for child in _children(root):
        if not _has_project(child):
            continue
        try:
            projects.append(load_workspace(child))
        except Exception:
            continue
    return projects


async def board_view(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project_id = request.query_params.get("project_id") or None

    def build():
        categories: list[dict] = []
        cards: list[dict] = []
        for project in _projects_for_board(root, project_id):
            data = project_workflow.board(project)
            for category in data["categories"]:
                category = {**category, "project_id": project.id,
                            "project_name": project.name}
                categories.append(category)
                for lane in category["lanes"]:
                    cards.extend(lane["cards"])
        return {
            "generated_at": now_iso(),
            "categories": categories,
            "cards": cards,
        }

    return JSONResponse(await asyncio.to_thread(build))


async def subproject_workflow_view(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    project = _find_workspace(root, request.path_params["project_id"])
    sub = project.subproject(request.path_params["subproject_id"])
    if request.method == "GET":
        return JSONResponse(project_workflow.card(project, sub))
    body = await _json(request)
    category_id = str(body.get("category_id") or sub.category_id)
    lane_id = str(body.get("lane_id") or sub.lane_id)
    try:
        card = await asyncio.to_thread(
            project_workflow.move_card, project, sub, category_id, lane_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(400, detail={
            "code": "invalid_argument", "message": str(error)}) from error
    return JSONResponse(card)


async def board_move(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    body = await _json(request)
    moves = body.get("moves")
    if not isinstance(moves, list) or not moves:
        raise HTTPException(400, detail={
            "code": "empty_moves", "message": "moves must be a non-empty list"})
    cards = []
    for move in moves:
        if not isinstance(move, dict):
            continue
        project = _find_workspace(root, str(move.get("project_id") or ""))
        sub = next((item for item in project.subprojects
                    if item.id == str(move.get("subproject_id") or "")), None)
        if sub is None:
            raise HTTPException(404, detail={
                "code": "not_found", "message": "subproject not found"})
        try:
            card = await asyncio.to_thread(
                project_workflow.move_card, project, sub,
                str(move.get("category_id") or ""),
                str(move.get("lane_id") or ""))
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(400, detail={
                "code": "invalid_argument", "message": str(error)}) from error
        cards.append(card)
    return JSONResponse({"cards": cards})


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
        for sub in project.subprojects:
            try:
                state = subproject_state(project, sub)
            except Exception:
                continue
            local = state.paths.assets / "tm.jsonl"
            if not local.is_file():
                continue
            units = read_units(local)
            if not units:
                continue
            result.append({
                "project_id": project.id,
                "project_name": project.name,
                "tm": {
                    "id": f"local-{sub.id}",
                    "name": f"{sub.name} · 本机确认记忆",
                    "level": "subproject",
                    "path": str(local),
                    "subproject_id": sub.id,
                },
                "units": [asdict(unit) for unit in units],
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
    try:
        save_settings(root, body)
    except ValueError as error:
        raise HTTPException(400, detail={
            "code": "invalid_argument", "message": str(error)}) from error
    return JSONResponse(public_settings(root))


async def provider_models(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    body = await _json(request)
    service = str(body.get("service") or "")
    if service not in {"asr", "mt"}:
        raise HTTPException(400, detail={
            "code": "invalid_argument", "message": "service must be asr or mt"})
    settings = load_settings(root)
    provider_id = str(body.get("id") or body.get("provider_id") or "")
    provider = next((item for item in settings["providers"]
                     if item["id"] == provider_id), None)
    base_url = str(body.get("base_url")
                   or (provider or {}).get("base_url") or "")
    default_type = "openai_audio" if service == "asr" else "openai_chat"
    api_type = str(body.get("api_type")
                   or (provider or {}).get("api_type") or default_type)
    api_key = str(body.get("api_key")
                  or (provider or {}).get("api_key") or "")
    api_key = api_key.strip() or resolve_api_key(None, provider=service)
    headers = (body.get("headers") if isinstance(body.get("headers"), dict)
               else (provider or {}).get("headers") or {})
    try:
        models = await asyncio.to_thread(
            fetch_models, base_url, api_type,
            api_key=api_key, headers=headers,
        )
    except ProviderError as error:
        raise HTTPException(400, detail={
            "code": error.code, "message": str(error)}) from error
    return JSONResponse({
        "service": service,
        "base_url": base_url,
        "api_type": api_type,
        "models": models,
    })


def _parse_models_payload(payload: dict) -> dict:
    """Normalise ASR (``loaded``/``configured``) and MT router (``status``) shapes."""
    data = payload.get("data") if isinstance(payload, dict) else None
    data = data if isinstance(data, list) else []
    loaded: list[str] = []
    available: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("id") or item.get("name")
        status = item.get("status")
        state = status.get("value") if isinstance(status, dict) else None
        if not name:
            continue
        if state == "loaded":
            loaded.append(str(name))
        else:
            available.append(str(name))
    current = payload.get("loaded") if isinstance(payload, dict) else None
    if isinstance(current, str) and current:
        loaded.append(current)
    configured = payload.get("configured") if isinstance(payload, dict) else None
    if isinstance(configured, dict) and configured.get("model"):
        if not loaded:
            loaded.append(str(configured["model"]))
    return {
        "loaded": sorted(set(loaded)),
        "available": sorted(set(available)),
        "count": len(data),
    }


def _health_request(url: str) -> "urllib.request.Request":
    import urllib.request
    return urllib.request.Request(
        url, headers={"User-Agent": f"NCAT/{__version__}"})


def _service_root(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _service_models(url: str) -> dict | None:
    if not url:
        return None
    try:
        import json as _json
        import urllib.request
        with urllib.request.urlopen(
                _health_request(api_endpoint(url, "/models")),
                timeout=1.5) as response:
            payload = _json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return None
    return _parse_models_payload(payload)


def _service_hardware(url: str) -> dict:
    """CPU/GPU/memory as reported by the service itself."""
    if not url:
        return {}
    try:
        import json as _json
        import urllib.request
        with urllib.request.urlopen(
                _health_request(_service_root(url) + "/hardware"),
                timeout=2) as response:
            payload = _json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "cpu": payload.get("cpu") or {},
        "gpu": payload.get("gpu") or {},
        "memory": payload.get("memory") or {},
    }


def _service_status(name: str, url: str, provider: dict | None = None) -> dict:
    base = {
        "name": name,
        "url": url,
        "provider_id": (provider or {}).get("id", ""),
        "provider_name": (provider or {}).get("provider_name", ""),
    }
    if not url:
        return {**base, "state": "not_configured"}
    if provider and provider.get("ignore_health"):
        return {
            **base, "state": "ignored",
            "detail": "已忽略运行状态检查",
            "health_supported": True,
        }
    health_url = _service_root(url) + "/health"
    try:
        import urllib.request
        with urllib.request.urlopen(_health_request(health_url),
                                    timeout=2) as response:
            status = response.status
    except Exception as exc:
        models = _service_models(url)
        if models is not None:
            return {
                **base, "state": "ok", "health_supported": False,
                "detail": "该服务不支持 /health 检查；已通过 /v1/models 确认可用",
                "models": models, "hardware": {},
            }
        return {**base, "state": "unreachable",
                "detail": str(exc)[:200], "health_supported": True}
    return {
        **base, "state": "ok", "health_supported": True, "http": status,
        "models": _service_models(url) or {
            "loaded": [], "available": [], "count": 0},
        "hardware": _service_hardware(url),
    }


def _health_status(root: Path) -> dict:
    settings = load_settings(root)

    def active_provider(service: str) -> dict | None:
        ref = settings[service]["active_provider_id"]
        provider = next((item for item in settings["providers"]
                         if item["id"] == ref and item["service"] == service),
                        None)
        if provider is None:
            provider = next((item for item in settings["providers"]
                             if item["service"] == service), None)
        return provider

    services = []
    for service, label in (("asr", "ASR"), ("mt", "MT")):
        provider = active_provider(service)
        services.append(_service_status(
            label, str((provider or {}).get("base_url") or ""), provider))
    return {"collected_at": now_iso(), "services": services}


async def health_status(request: Request) -> JSONResponse:
    root = workspace_root(request.app.state.workspace)
    return JSONResponse(await asyncio.to_thread(_health_status, root))


# ---------------------------------------------------------------- routes
def _routes() -> list:
    return [
        Route("/api/v1/health", health),
        Route("/api/v1/health/status", health_status),
        Route("/api/v1/settings", settings_view, methods=["GET", "PATCH"]),
        Route("/api/v1/settings/models", provider_models, methods=["POST"]),
        Route("/api/v1/termbases", all_termbases),
        Route("/api/v1/tm", all_tm),
        Route("/api/v1/tm/export.tmx", tm_export),
        Route("/api/v1/board", board_view),
        Route("/api/v1/board/move", board_move, methods=["POST"]),
        Route("/api/v1/system", system_info),
        Route("/api/v1/workspace", workspace),
        Route("/api/v1/projects", projects, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}", project_detail,
              methods=["GET", "PATCH"]),
        Route("/api/v1/projects/{project_id}/board", board_project_view),
        Route("/api/v1/projects/{project_id}/overview", project_overview),
        Route("/api/v1/projects/{project_id}/activity", project_activity),
        Route("/api/v1/projects/{project_id}/features",
              project_features, methods=["PATCH"]),
        Route("/api/v1/projects/{project_id}/automation",
              project_automation, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/automation/{rule_id}",
              automation_detail, methods=["PATCH", "DELETE"]),
        Route("/api/v1/projects/{project_id}/categories",
              categories_view, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/categories/{category_id}",
              category_detail, methods=["PATCH", "DELETE"]),
        Route("/api/v1/projects/{project_id}/categories/{category_id}/lanes",
              lanes_view, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/categories/{category_id}/lanes/{lane_id}",
              lane_detail, methods=["PATCH", "DELETE"]),
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
              subproject_detail, methods=["GET", "PATCH", "DELETE"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/actions/{action}",
              subproject_actions, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/subtasks",
              subproject_subtasks, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/subtasks/{task_id}",
              subtask_detail, methods=["PATCH", "DELETE"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/internal-links",
              internal_links, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/internal-links/{link_id}",
              internal_link_detail, methods=["DELETE"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/external-links",
              external_links, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/external-links/{link_id}",
              external_link_detail, methods=["DELETE"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/comments",
              subproject_comments, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/comments/{comment_id}",
              comment_detail, methods=["DELETE"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/attachments",
              subproject_attachments, methods=["GET", "POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/attachments/{attachment_id}",
              attachment_detail, methods=["GET", "DELETE"]),
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
              subproject_styles, methods=["GET", "PATCH"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/cues/{cue_id}/suggest-mt",
              subproject_cue_mt, methods=["POST"]),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/audit",
              subproject_audit),
        Route("/api/v1/projects/{project_id}/subprojects/{subproject_id}/workflow",
              subproject_workflow_view, methods=["GET", "PATCH"]),
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


class _NoCacheStatic(StaticFiles):
    """本地单用户工具：静态资源不缓存，改完刷新就生效。"""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


def create_app(*, workspace: str | Path | None = None) -> Starlette:
    app = Starlette(routes=_routes())
    app.add_exception_handler(HTTPException, _http_error)
    app.state.workspace = str(workspace_root(workspace))
    if STATIC_DIR.is_dir():
        app.mount("/", _NoCacheStatic(directory=str(STATIC_DIR), html=True),
                  name="static")
    return app
