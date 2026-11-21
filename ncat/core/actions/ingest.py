"""Ingest action: checksum, proxy/audio transcode, and run records."""
from __future__ import annotations

from pathlib import Path

from ..media import file_checksum
from ..media_backend import get_media_backend
from ..project import ProjectState, save_project
from ..runs import finish_run, finish_step, save_run, start_step
from ..types import RunStatus, StepStatus
from .common import ActionError, begin_run, fail_run_action


def run_ingest(state: ProjectState, *, height: int = 720,
               encoder: str | None = None, force: bool = False) -> dict:
    """Transcode a project source into proxy and 16 kHz mono WAV."""
    if state.source is None or not state.source.path:
        raise ActionError("missing_source",
                          "project has no source; run init --source")
    source = Path(state.source.path)
    if not source.is_file():
        raise ActionError("not_found", f"source not found: {source}")
    backend = get_media_backend()

    run = begin_run(state, "ingest")
    try:
        step = start_step(run, "checksum")
        save_run(state.paths, run)
        checksum = file_checksum(source)
        finish_step(run, step, StepStatus.SUCCEEDED,
                    message=checksum, artifacts=[str(source)])

        proxy = state.paths.media / f"source-{height}p.mp4"
        wav = state.paths.media / "source.wav"
        settings = state.ingest or {}
        reuse = bool(
            not force
            and state.source.checksum == checksum
            and settings.get("height") == height
            and settings.get("encoder")
            and proxy.is_file()
            and wav.is_file()
        )
        if reuse:
            step = start_step(run, "reuse")
            save_run(state.paths, run)
            info = backend.probe(source)
            encoder_name = str(settings.get("encoder", ""))
            finish_step(run, step, StepStatus.SUCCEEDED,
                        message="checksum and settings match",
                        artifacts=[str(proxy), str(wav)])
        else:
            step = start_step(run, "transcode")
            save_run(state.paths, run)
            result = backend.transcode(
                source, state.paths.media, stem="source",
                height=height, encoder=encoder,
            )
            info = result.info
            encoder_name = result.encoder
            finish_step(run, step, StepStatus.SUCCEEDED,
                        message=encoder_name,
                        artifacts=[str(result.proxy), str(result.wav)])

        state.source.proxy_path = str(proxy)
        state.source.audio_path = str(wav)
        state.source.duration_ms = info.duration_ms
        state.source.width = info.width
        state.source.height = info.height
        state.source.fps = info.fps
        state.source.checksum = checksum
        state.source.status = "ready"
        state.ingest = {"height": height, "encoder": encoder_name}
        save_project(state)

        finish_run(run, RunStatus.SUCCEEDED)
        save_run(state.paths, run)
        return {
            "project": state.project.id,
            "proxy": str(proxy),
            "audio": str(wav),
            "encoder": encoder_name,
            "reused": reuse,
            "checksum": checksum,
            "run_id": run.id,
            "run_status": str(run.status),
        }
    except Exception as error:
        fail_run_action(state, run, error)
        raise
