"""Minimal file-backed Run/RunStep persistence.

Runs are the audit trail for CLI work: one JSON file per run, written
atomically. No database is involved, and only successful commands update the
project file; failures remain visible in ``runs/``.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .project import ProjectPaths, new_id, now_iso
from .types import Run, RunStatus, RunStep, StepStatus


def runs_dir(paths: ProjectPaths) -> Path:
    directory = paths.root / "runs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def new_run(project_id: str, *, recipe: str = "standard") -> Run:
    return Run(
        id=new_id("run"),
        project_id=project_id,
        status=RunStatus.QUEUED,
        recipe=recipe,
        created_at=now_iso(),
    )


def run_path(paths: ProjectPaths, run_id: str) -> Path:
    return runs_dir(paths) / f"{run_id}.json"


def save_run(paths: ProjectPaths, run: Run) -> Path:
    path = run_path(paths, run.id)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(asdict(run), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def load_run(paths: ProjectPaths, run_id: str) -> Run:
    payload = json.loads(run_path(paths, run_id).read_text(encoding="utf-8-sig"))
    return _run_from_payload(payload)


def list_runs(paths: ProjectPaths) -> list[Run]:
    directory = runs_dir(paths)
    runs: list[Run] = []
    for path in directory.glob("*.json"):
        try:
            runs.append(_run_from_payload(
                json.loads(path.read_text(encoding="utf-8-sig"))))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(runs, key=lambda run: (run.created_at or "", run.id))


def latest_run(paths: ProjectPaths) -> Run | None:
    runs = list_runs(paths)
    return runs[-1] if runs else None


def start_step(run: Run, name: str) -> RunStep:
    step = RunStep(name=name, status=StepStatus.RUNNING, started_at=now_iso())
    run.steps.append(step)
    if run.status == RunStatus.QUEUED:
        run.status = RunStatus.RUNNING
        run.started_at = now_iso()
    return step


def finish_step(run: Run, step: RunStep, status: StepStatus, *,
                message: str = "", artifacts: Iterable[str] = (),
                error: str | None = None) -> RunStep:
    step.status = status
    step.finished_at = now_iso()
    step.message = message
    step.artifacts = list(artifacts)
    step.error = error
    if status in {StepStatus.SUCCEEDED, StepStatus.SKIPPED}:
        step.progress = 1.0
    return step


def finish_run(run: Run, status: RunStatus, *,
               error: str | None = None) -> Run:
    run.status = status
    run.finished_at = now_iso()
    run.error = error
    return run


def fail_run(run: Run, error: str) -> Run:
    for step in run.steps:
        if step.status == StepStatus.RUNNING:
            finish_step(run, step, StepStatus.FAILED, error=error)
    return finish_run(run, RunStatus.FAILED, error=error)


def _run_from_payload(payload: dict) -> Run:
    steps = [
        RunStep(
            name=item["name"],
            status=StepStatus(item.get("status", "pending")),
            started_at=item.get("started_at"),
            finished_at=item.get("finished_at"),
            progress=float(item.get("progress", 0.0)),
            message=item.get("message", ""),
            artifacts=list(item.get("artifacts") or []),
            error=item.get("error"),
        )
        for item in payload.get("steps", [])
    ]
    return Run(
        id=payload["id"],
        project_id=payload["project_id"],
        status=RunStatus(payload.get("status", "queued")),
        recipe=payload.get("recipe", "standard"),
        steps=steps,
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        created_at=payload.get("created_at"),
        error=payload.get("error"),
        metrics=dict(payload.get("metrics") or {}),
    )
