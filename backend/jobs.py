"""Job orchestration: runs pipeline stages and publishes progress.

The runner executes stages in a worker thread so a 30-minute pipeline does
not block the HTTP request that started it. Progress reaches the browser
two ways: written to the ``jobs.stages`` JSON column (so a page reload
resumes where it left off) and pushed over a WebSocket (so an open page
updates live).

Stage functions are resolved lazily by name. Importing ``pipeline`` at
module scope would drag in yt-dlp and friends on every request; instead the
runner imports a stage module only when it is about to run it.
"""
from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from sqlmodel import Session

from .db import JOBS_DIR, engine
from .models import Job, now_ms

# Subscribers get (job_id, payload) for every progress event.
Listener = Callable[[int, dict[str, Any]], None]

_lock = threading.Lock()
_listeners: list[Listener] = []
_threads: dict[int, threading.Thread] = {}


def subscribe(listener: Listener) -> Callable[[], None]:
    with _lock:
        _listeners.append(listener)

    def unsubscribe() -> None:
        with _lock:
            if listener in _listeners:
                _listeners.remove(listener)

    return unsubscribe


def announce(job_id: int, payload: dict[str, Any]) -> None:
    """Publish an event from outside the pipeline.

    HTTP endpoints that mutate job state use this so open browsers stay in
    step without polling.
    """
    _publish(job_id, payload)


def _publish(job_id: int, payload: dict[str, Any]) -> None:
    with _lock:
        current = list(_listeners)
    for listener in current:
        try:
            listener(job_id, payload)
        except Exception:
            # A broken subscriber must not kill the pipeline.
            traceback.print_exc()


def _set_stage(job_id: int, stage: str, status: str, **extra: Any) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        stages = dict(job.stages or {})
        entry = dict(stages.get(stage) or {})
        entry.update(status=status, updated_at=now_ms(), **extra)
        stages[stage] = entry
        job.stages = stages
        job.current_stage = stage if status == 'running' else job.current_stage
        job.updated_at = now_ms()
        session.add(job)
        session.commit()
        snapshot = {
            'stage': stage,
            'status': status,
            'current_stage': job.current_stage,
            'stages': stages,
            'job_status': job.status,
            **extra,
        }
    _publish(job_id, snapshot)


def _make_reporter(job_id: int, stage: str) -> Callable[[str], None]:
    def report(message: str) -> None:
        _publish(job_id, {'stage': stage, 'status': 'running',
                          'message': message})
    return report


def _resolve(stage: str):
    """Delegate to pipeline.runner so both entry points share one table."""
    from pipeline import runner
    return runner.run_stage


def run_job(job_id: int, stages: list[str], context: dict[str, Any]) -> None:
    """Execute ``stages`` for job ``job_id``. Runs in a worker thread.

    Any stage failure stops the sequence and records the traceback on the
    job rather than propagating, because the caller is a thread with
    nothing useful to do with an exception.
    """
    try:
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.status = 'running'
            job.current_stage = stages[0] if stages else ''
            job.error = ''
            job.updated_at = now_ms()
            session.add(job)
            session.commit()

        for index, stage in enumerate(stages):
            _set_stage(job_id, stage, 'running',
                       progress=round(index * 100 / max(1, len(stages))))
            report = _make_reporter(job_id, stage)
            try:
                _resolve(stage)(stage, job_id, context, report)
            except Exception as exc:
                _set_stage(job_id, stage, 'error',
                           error=f'{type(exc).__name__}: {exc}')
                raise
            _set_stage(job_id, stage, 'done',
                       progress=round((index + 1) * 100 / max(1, len(stages))))

        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = 'done'
                job.current_stage = ''
                job.progress = 100
                job.updated_at = now_ms()
                session.add(job)
                session.commit()
        _publish(job_id, {'stage': '', 'status': 'all-done',
                          'job_status': 'done', 'progress': 100})

    except Exception as exc:
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = 'error'
                job.error = f'{type(exc).__name__}: {exc}'
                job.updated_at = now_ms()
        session.add(job)
        session.commit()
        _publish(job_id, {'stage': '', 'status': 'error',
                          'job_status': 'error',
                          'error': f'{type(exc).__name__}: {exc}',
                          'traceback': traceback.format_exc()})
    finally:
        _threads.pop(job_id, None)


def start_job(job_id: int, stages: list[str], context: dict[str, Any]) -> None:
    """Spawn the worker thread for ``job_id``."""
    thread = threading.Thread(
        target=run_job, args=(job_id, stages, context),
        name=f'ncat-job-{job_id}', daemon=True,
    )
    _threads[job_id] = thread
    thread.start()


def is_running(job_id: int) -> bool:
    return job_id in _threads and _threads[job_id].is_alive()


def cancel(job_id: int) -> bool:
    """Request cancellation by flipping the job status.

    Python cannot kill a thread safely, so stages poll ``job_status``
    between chunks via ``pipeline.contracts.check_cancelled``.
    """
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None or job.status not in ('running', 'pending'):
            return False
        job.status = 'cancelled'
        job.updated_at = now_ms()
        session.add(job)
        session.commit()
    _publish(job_id, {'stage': '', 'status': 'cancelled',
                      'job_status': 'cancelled'})
    return True


def job_dir(job_id: int) -> Path:
    d = JOBS_DIR / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d
