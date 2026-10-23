"""FastAPI application.

Binds to port 80 on the LAN. Since port 80 is privileged, the interpreter
is granted ``CAP_NET_BIND_SERVICE`` rather than running the server as root:
``sudo setcap cap_net_bind_service=+ep .venv-app/bin/python``.

Routes are split by concern under ``routers/``; this module owns app
construction, lifespan, the WebSocket endpoint and static hosting.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import jobs as runner
from .db import BASE_DIR, JOBS_DIR, init_db
from .routers import glossary as glossary_router
from .routers import jobs as jobs_router

STATIC_DIR = BASE_DIR / 'static'


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title='NCAT',
    version='0.2.0',
    description="Nick's Computer-Aided Translation suite",
    lifespan=lifespan,
)

app.include_router(jobs_router.router)
app.include_router(glossary_router.router)


@app.get('/health')
def health() -> dict:
    return {'status': 'ok', 'jobs_dir': str(JOBS_DIR)}


@app.get('/api/models')
def models() -> dict:
    """Which model files are actually present.

    Reported rather than assumed: a job can be created with a model missing
    and the failure only surfaces minutes into an ASR run otherwise.
    """
    from .config import model_status
    return {'models': model_status()}


@app.websocket('/ws/jobs/{job_id}')
async def job_events(websocket: WebSocket, job_id: int) -> None:
    """Push job progress to the browser.

    A queue per connection absorbs the burst of stage transitions a run
    produces; without it the handler would have to await each listener
    inline and a slow client would stall the pipeline thread's publisher.
    """
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    # Send current state immediately. A page reload, or a browser that
    # connects after the pipeline already finished, would otherwise show an
    # empty rail until the next stage transition -- which for a fast or
    # already-complete job never comes.
    try:
        from sqlmodel import Session

        from .db import engine
        from .models import Job

        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                await websocket.send_json({'error': f'job {job_id} not found'})
                await websocket.close()
                return
            # Copy the fields out inside the session: once it closes the
            # instance is detached and any attribute read raises.
            snapshot = {
                'stage': job.current_stage or '',
                'status': 'snapshot',
                'job_status': job.status,
                'stages': dict(job.stages or {}),
                'progress': job.progress,
                'error': job.error,
            }
        await websocket.send_json(snapshot)
    except Exception as exc:  # noqa: BLE001 - report and close, never hang
        await websocket.send_json({'error': f'{type(exc).__name__}: {exc}'})
        await websocket.close()
        return

    def on_event(event_job_id: int, payload: dict) -> None:
        if event_job_id != job_id:
            return
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop the oldest rather than block the producer.
            try:
                queue.get_nowait()
                queue.put_nowait(payload)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    unsubscribe = runner.subscribe(on_event)

    async def pump() -> None:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            # Client sends nothing meaningful; this keeps the socket open
            # and surfaces disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()
        pump_task.cancel()


@app.get('/', response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = STATIC_DIR / 'index.html'
    if not page.is_file():
        return HTMLResponse('<h1>NCAT</h1><p>static/index.html is missing</p>',
                            status_code=500)
    return HTMLResponse(page.read_text(encoding='utf-8'))


if STATIC_DIR.is_dir():
    # Mounted at the root, not under /static, so the page can use ordinary
    # relative paths (`css/app.css`) and the tree can be opened from the
    # filesystem without rewriting anything. Registered API routes are
    # matched before a mount, so /api and /health are unaffected.
    app.mount('/', StaticFiles(directory=str(STATIC_DIR), html=True),
              name='static')
