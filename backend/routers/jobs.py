"""Job and cue endpoints."""
from __future__ import annotations

import json
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


class ReviewRequest(BaseModel):
    force: bool = False


class SplitRequest(BaseModel):
    """Split a cue before its ``at``-th word, counting from zero."""
    at: int


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
        cue.cps = round(len(text) / duration_s, 1)
        cue.line_length = max((len(line) for line in text.split('\n')),
                              default=0)
    cue.updated_at = now_ms()
    session.add(cue)
    session.commit()
    session.refresh(cue)
    _announce(job_id, 'cue-edited', cue_id=cue_id)
    return cue


# ----------------------------------------------------------------- review --
#
# The review stage is a gate, not a step: a human reads the transcript and
# fixes both the wording and the cue division before the text is translated.
# The gate is therefore expressed as data this module can write, not as a
# pause: a blocking prompt would hang the worker thread holding the job.

def _job_paths(job: Job):
    if not job.job_dir:
        raise HTTPException(400, 'job has no working directory')
    # Match how the runner builds paths: a URL is only a source when it has
    # already been downloaded, so passing it here would resolve to a path
    # that does not exist.
    return build_paths(job.job_dir)


def _asr_words(job: Job) -> list[dict]:
    """Word timings from the transcribe stage, or an empty list."""
    paths = _job_paths(job)
    path = getattr(paths, 'transcript_json', None)
    if not path or not Path(path).is_file():
        return []
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return (data.get('transcript') or {}).get('words') or []


def _announce(job_id: int, kind: str, **extra: Any) -> None:
    """Tell any connected browser that this job's cues changed.

    The pipeline runner already publishes stage transitions, but cue edits
    happen through HTTP and would otherwise leave every open tab showing a
    stale division. Publishing here means a second window, or another
    machine, updates without a manual reload.
    """
    try:
        runner.announce(job_id, {'kind': kind, **extra})
    except Exception:  # noqa: BLE001 - a notification must never fail a write
        pass


def _renumber(session: Session, cues: list[Cue]) -> None:
    """Rewrite ``index`` from 0 with the unique constraint intact.

    ``(job_id, index)`` is unique, so final numbers cannot be assigned in
    one statement: rows still hold values the new sequence wants. The rows
    are parked in a negative range first and flushed **separately** --
    batching both passes into one commit applies the final values against
    the old ones and trips the constraint.
    """
    for offset, cue in enumerate(cues):
        cue.index = -(offset + 1)
        session.add(cue)
    session.commit()
    for index, cue in enumerate(cues):
        cue.index = index
        session.add(cue)
    session.commit()


def _words_for(words: list[dict], cue: Cue) -> list[dict]:
    return [w for w in words
            if cue.start_ms <= int(w['s']) and int(w['e']) <= cue.end_ms]


@router.get('/{job_id}/review')
def get_review(job_id: int, session: Session = Depends(get_session)) -> dict:
    job = _require_job(session, job_id)
    cues = list(session.exec(
        select(Cue).where(Cue.job_id == job_id).order_by(Cue.index)))
    words = _asr_words(job)
    out = []
    for cue in cues:
        item = cue.model_dump()
        item['words'] = [{'w': w['w'], 's': w['s'], 'e': w['e']}
                         for w in _words_for(words, cue)]
        out.append(item)
    return {
        'cues': out,
        'has_transcript': bool(words),
        'counts': {
            'total': len(cues),
            'draft': sum(1 for c in cues if c.status == 'draft'),
            'edited': sum(1 for c in cues if c.status == 'edited'),
            'approved': sum(1 for c in cues if c.status == 'approved'),
            'needs_timing': sum(1 for c in cues if c.needs_timing),
        },
    }


@router.post('/{job_id}/review')
def propose_review(job_id: int, payload: ReviewRequest,
                   session: Session = Depends(get_session)) -> dict:
    """Run the cue proposal and persist it as editable cues."""
    job = _require_job(session, job_id)
    words = _asr_words(job)
    if not words:
        raise HTTPException(
            400, 'no transcript yet; run transcribe before review')

    existing = list(session.exec(
        select(Cue).where(Cue.job_id == job_id).order_by(Cue.index)))
    touched = [c for c in existing if c.status in ('edited', 'approved')]
    if touched and not payload.force:
        raise HTTPException(
            409, f'{len(touched)} cues have been edited; pass force=true to '
                  f'discard them and repropose')

    from pipeline.review import propose
    proposed = propose(words)
    if not proposed:
        raise HTTPException(500, 'proposal produced no cues')

    for cue in existing:
        session.delete(cue)
    session.commit()

    rows: list[Cue] = []
    for entry in proposed:
        text = entry['en']
        duration_s = max(0.001, (entry['end_ms'] - entry['start_ms']) / 1000)
        rows.append(Cue(
            job_id=job_id,
            index=entry['index'],
            start_ms=entry['start_ms'],
            end_ms=entry['end_ms'],
            en=text,
            zh='',
            status='draft',
            needs_timing=bool(entry['needs_timing']),
            cps=round(len(text) / duration_s, 1),
            line_length=max((len(x) for x in text.split('\n')), default=0),
        ))
    for row in rows:
        session.add(row)
    session.commit()

    stages = dict(job.stages or {})
    stages['review'] = {'status': 'proposed', 'cues': len(rows),
                        'at': now_ms()}
    job.stages = stages
    job.updated_at = now_ms()
    session.add(job)
    session.commit()
    _announce(job_id, 'cues-replaced', cues=len(rows))
    return {'cues': len(rows)}


@router.post('/{job_id}/review/approve')
def approve_review(job_id: int, session: Session = Depends(get_session)) -> dict:
    """Accept the division, write it where the pipeline reads it, unblock MT.

    Approving has to do more than flip a status. The cue rows live in the
    database, while every stage reads ``job.cues.json`` from disk, so without
    writing the file here the edits a human just made would be silently
    ignored by translate -- the review gate would appear to do nothing.

    The same pass writes ``job.reviewed.txt``: the cue texts joined into one
    continuous run, which is what the translation stage is given. English is
    joined with a single space, Chinese with two (see ``tools.ass_text``).
    """
    job = _require_job(session, job_id)
    cues = list(session.exec(
        select(Cue).where(Cue.job_id == job_id).order_by(Cue.index)))
    if not cues:
        raise HTTPException(400, 'nothing to approve; propose cues first')
    for cue in cues:
        if cue.status == 'draft':
            cue.status = 'approved'
            cue.updated_at = now_ms()
            session.add(cue)
    session.commit()

    paths = _job_paths(job)
    payload = [
        {'index': c.index, 'start_ms': c.start_ms, 'end_ms': c.end_ms,
         'en': c.en or '', 'zh': c.zh or '', 'status': c.status,
         'needs_timing': bool(c.needs_timing)}
        for c in cues
    ]
    paths.require('cues_json', 'reviewed_txt')
    Path(paths.cues_json).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    Path(paths.reviewed_txt).write_text(
        ' '.join(c['en'].strip() for c in payload if c['en'].strip()) + '\n',
        encoding='utf-8')

    stages = dict(job.stages or {})
    stages['review'] = {'status': 'approved', 'cues': len(cues),
                        'at': now_ms()}
    job.stages = stages
    job.updated_at = now_ms()
    session.add(job)
    session.commit()
    _announce(job_id, 'review-approved', cues=len(cues))
    return {'approved': len(cues),
            'cues_json': str(paths.cues_json),
            'reviewed_txt': str(paths.reviewed_txt)}


@router.post('/{job_id}/cues/{cue_id}/split')
def split_cue(job_id: int, cue_id: int, payload: SplitRequest,
              session: Session = Depends(get_session)) -> list[Cue]:
    """Split a cue before its ``at``-th word, keeping real timings.

    The split point is a word index rather than a character offset because
    the ASR timings are per word. Cutting mid-word would produce a fragment
    with no defensible start or end.
    """
    job = _require_job(session, job_id)
    cue = session.get(Cue, cue_id)
    if cue is None or cue.job_id != job_id:
        raise HTTPException(404, 'cue not found in this job')

    words = _words_for(_asr_words(job), cue)
    if not words:
        raise HTTPException(400, 'no word timings inside this cue')
    if not 1 <= payload.at < len(words):
        raise HTTPException(
            400, f'split point out of range: cue has {len(words)} words, '
                 f'asked to split before word {payload.at}')

    left, right = words[:payload.at], words[payload.at:]
    first_text = ' '.join(w['w'] for w in left)
    second_text = ' '.join(w['w'] for w in right)

    others = [c for c in session.exec(
        select(Cue).where(Cue.job_id == job_id).order_by(Cue.index))
        if c.id != cue_id]

    session.delete(cue)
    session.commit()

    def make(text: str, ws: list[dict], index: int) -> Cue:
        start, end = int(ws[0]['s']), int(ws[-1]['e'])
        duration_s = max(0.001, (end - start) / 1000)
        return Cue(job_id=job_id, index=index, start_ms=start, end_ms=end,
                   en=text, zh='', status='edited', needs_timing=False,
                   cps=round(len(text) / duration_s, 1),
                   line_length=max((len(x) for x in text.split('\n')),
                                   default=0))

    # Place both halves at the old cue's position in the running order.
    rebuilt: list[Cue] = []
    before = [c for c in others if c.index < cue.index]
    after = [c for c in others if c.index > cue.index]
    rebuilt.extend(before)
    rebuilt.append(make(first_text, left, 0))
    rebuilt.append(make(second_text, right, 0))
    rebuilt.extend(after)
    _renumber(session, rebuilt)
    for row in rebuilt:
        session.add(row)
    session.commit()
    _announce(job_id, 'cues-changed', reason='split')
    return list(session.exec(
        select(Cue).where(Cue.job_id == job_id).order_by(Cue.index)))


@router.post('/{job_id}/cues/{cue_id}/merge')
def merge_cue(job_id: int, cue_id: int,
              session: Session = Depends(get_session)) -> list[Cue]:
    """Merge a cue with the one after it."""
    job = _require_job(session, job_id)
    cue = session.get(Cue, cue_id)
    if cue is None or cue.job_id != job_id:
        raise HTTPException(404, 'cue not found in this job')

    others = list(session.exec(
        select(Cue).where(Cue.job_id == job_id).order_by(Cue.index)))
    position = next((i for i, c in enumerate(others) if c.id == cue_id), -1)
    if position < 0 or position + 1 >= len(others):
        raise HTTPException(400, 'nothing after this cue to merge into')

    nxt = others[position + 1]
    merged = ' '.join(x for x in (cue.en.strip(), nxt.en.strip()) if x)
    duration_s = max(0.001, (nxt.end_ms - cue.start_ms) / 1000)
    cue.en = merged
    cue.start_ms = min(cue.start_ms, nxt.start_ms)
    cue.end_ms = max(cue.end_ms, nxt.end_ms)
    cue.status = 'edited'
    cue.cps = round(len(merged) / duration_s, 1)
    cue.line_length = max((len(x) for x in merged.split('\n')), default=0)
    cue.updated_at = now_ms()

    remaining = [c for c in others if c.id != nxt.id]
    session.delete(nxt)
    session.commit()          # free the index before renumbering
    _renumber(session, remaining)
    for row in remaining:
        session.add(row)
    session.add(cue)
    session.commit()
    _announce(job_id, 'cues-changed', reason='merge')
    return list(session.exec(
        select(Cue).where(Cue.job_id == job_id).order_by(Cue.index)))


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
