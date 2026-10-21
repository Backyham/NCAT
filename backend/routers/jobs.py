"""Job and cue endpoints."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import jobs as runner
from ..db import get_session
from pipeline.paths import STAGES, build_paths
from ..models import Cue, Job, PunchItem, now_ms

router = APIRouter(prefix='/api/jobs', tags=['jobs'])


class JobCreate(BaseModel):
    source_url: str = ''
    title: str = ''
    glossary_id: int | None = None
    # Which stages to run. Defaults to everything; the UI sends a prefix
    # when the user is re-running from the middle of the pipeline.
    stages: list[str] = Field(default_factory=lambda: list(STAGES))
    # Optional upload path, when media came in through the browser rather
    # than a URL.
    source_path: str | None = None
    # Set false to create a job record without launching the pipeline, e.g.
    # when the browser wants to configure stages first.
    auto_start: bool = True


class JobPatch(BaseModel):
    title: str | None = None
    glossary_id: int | None = None


class CuePatch(BaseModel):
    zh: str | None = None
    en: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    status: str | None = None
    needs_timing: bool | None = None


def _require_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, f'job {job_id} not found')
    return job


def _job_payload(job: Job) -> dict[str, Any]:
    return {
        'id': job.id,
        'title': job.title,
        'source_url': job.source_url,
        'status': job.status,
        'current_stage': job.current_stage,
        'stages': job.stages or {},
        'error': job.error,
        'job_dir': job.job_dir,
        'glossary_id': job.glossary_id,
        'progress': job.progress,
        'created_at': job.created_at,
        'updated_at': job.updated_at,
    }


@router.get('')
def list_jobs(limit: int = 50,
              session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    statement = select(Job).order_by(Job.created_at.desc()).limit(limit)
    return [_job_payload(job) for job in session.exec(statement)]


@router.post('', status_code=201)
def create_job(payload: JobCreate,
               session: Session = Depends(get_session)) -> dict[str, Any]:
    job = Job(
        title=payload.title or (payload.source_url or 'untitled')[:120],
        source_url=payload.source_url,
        glossary_id=payload.glossary_id,
        status='pending',
        stages={stage: {'status': 'pending'} for stage in payload.stages},
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    directory = runner.job_dir(job.id)
    job.job_dir = str(directory)
    session.add(job)
    session.commit()

    # Auto-start unless the caller opted out with auto_start=false. A job
    # created without a URL is still valid: it can run from media already
    # sitting in the job directory, or just hold cues for editing.
    if payload.auto_start and payload.stages:
        runner.start_job(job.id, payload.stages,
                         _context_for(job, payload))
    return _job_payload(job)


def _context_for(job: Job, payload: JobCreate) -> dict[str, Any]:
    paths = build_paths(job.job_dir or str(runner.job_dir(job.id)), source=(
        payload.source_path or job.source_url or None))
    return {
        'paths': paths,
        'job_id': job.id,
        'glossary_id': job.glossary_id,
    }


@router.get('/{job_id}')
def get_job(job_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = _require_job(session, job_id)
    payload = _job_payload(job)
    payload['running'] = runner.is_running(job_id)
    return payload


@router.patch('/{job_id}')
def patch_job(job_id: int, payload: JobPatch,
              session: Session = Depends(get_session)) -> dict[str, Any]:
    job = _require_job(session, job_id)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(job, field, value)
    job.updated_at = now_ms()
    session.add(job)
    session.commit()
    session.refresh(job)
    return _job_payload(job)


@router.post('/{job_id}/cancel')
def cancel_job(job_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    _require_job(session, job_id)
    ok = runner.cancel(job_id)
    if not ok:
        raise HTTPException(409, 'job is not running')
    return {'cancelled': True}


@router.post('/{job_id}/rerun')
def rerun_job(job_id: int,
              stages: list[str] | None = None,
              session: Session = Depends(get_session)) -> dict[str, Any]:
    """Restart the pipeline for an existing job, optionally from a subset."""
    job = _require_job(session, job_id)
    if runner.is_running(job_id):
        raise HTTPException(409, 'job is already running')
    selected = stages or list(STAGES)
    job.status = 'pending'
    job.error = ''
    job.stages = {stage: {'status': 'pending'} for stage in selected}
    job.updated_at = now_ms()
    session.add(job)
    session.commit()

    paths = build_paths(job.job_dir or str(runner.job_dir(job.id)),
                        source=job.source_url or None)
    runner.start_job(job.id, selected, {
        'paths': paths, 'job_id': job.id, 'glossary_id': job.glossary_id,
    })
    return _job_payload(job)


@router.delete('/{job_id}', status_code=204)
def delete_job(job_id: int, session: Session = Depends(get_session)) -> Response:
    if runner.is_running(job_id):
        raise HTTPException(409, 'cannot delete a running job')
    job = _require_job(session, job_id)
    for cue in session.exec(select(Cue).where(Cue.job_id == job_id)):
        session.delete(cue)
    for item in session.exec(select(PunchItem).where(PunchItem.job_id == job_id)):
        session.delete(item)
    session.delete(job)
    session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------- cues --

@router.get('/{job_id}/cues')
def list_cues(job_id: int,
              session: Session = Depends(get_session)) -> list[Cue]:
    _require_job(session, job_id)
    return list(session.exec(
        select(Cue).where(Cue.job_id == job_id).order_by(Cue.index)))


@router.patch('/{job_id}/cues/{cue_id}')
def patch_cue(job_id: int, cue_id: int, payload: CuePatch,
              session: Session = Depends(get_session)) -> Cue:
    _require_job(session, job_id)
    cue = session.get(Cue, cue_id)
    if cue is None or cue.job_id != job_id:
        raise HTTPException(404, 'cue not found in this job')
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(cue, field, value)
    if payload.zh is not None or payload.en is not None:
        cue.status = 'edited'
        # Recompute the reading-speed metric so the editor's flags stay honest.
        text = cue.zh or cue.en
        duration_s = max(0.001, (cue.end_ms - cue.start_ms) / 1000)
        cue.cps = len(text) / duration_s
        cue.line_length = max((len(line) for line in text.split('\n')),
                              default=0)
    cue.updated_at = now_ms()
    session.add(cue)
    session.commit()
    session.refresh(cue)
    return cue


# ------------------------------------------------------------- artifacts --

@router.get('/{job_id}/files')
def list_files(job_id: int, session: Session = Depends(get_session)) -> list[str]:
    job = _require_job(session, job_id)
    if not job.job_dir:
        return []
    directory = Path(job.job_dir)
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file())


@router.get('/{job_id}/files/{name}')
def get_file(job_id: int, name: str,
             session: Session = Depends(get_session)) -> FileResponse:
    job = _require_job(session, job_id)
    if not job.job_dir:
        raise HTTPException(404, 'job has no directory')
    # Resolve and confirm containment: prevents ../ traversal out of the
    # job directory, which is reachable from the LAN.
    target = (Path(job.job_dir) / name).resolve()
    root = Path(job.job_dir).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(400, 'invalid file name')
    if not target.is_file():
        raise HTTPException(404, 'file not found')
    return FileResponse(target)
