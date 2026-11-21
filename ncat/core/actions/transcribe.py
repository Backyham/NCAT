"""Transcribe action: project ASR run, finalize, and save outputs.

The ASR backend may import torch/NeMo, so callers that live in the web venv
must execute this action in an ASR worker process.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..asr import (
    ASRRequest, describe_backends, get_backend, load_plugins,
)
from ..project import ProjectState, save_project
from ..runs import finish_run, finish_step, save_run, start_step
from ..transcript import (
    finalize_transcript, save_transcript, write_transcript_text,
)
from ..types import ProjectStage, RunStatus, StepStatus
from .common import ActionError, begin_run, fail_run_action


def _transcribe_attempt(audio: Path, language: str, backend_name: str,
                        options: dict[str, Any]):
    backend = get_backend(backend_name)
    return backend.transcribe(ASRRequest(
        audio_path=str(audio), language=language, options=options,
    ))


def run_transcribe(state: ProjectState, *, backend: str | None = None,
                   model: str | None = None, language: str | None = None,
                   options: dict[str, Any] | None = None,
                   offset_ms: int = 0, chunk_ms: int = 0,
                   pause_after_transcript: bool | None = None,
                   fallback: dict[str, Any] | None = None) -> dict:
    """Run ASR for a project's ingested audio and persist the transcript."""
    if state.source is None or not state.source.audio_path:
        raise ActionError("missing_audio",
                          "project has no audio; run ingest first")
    audio = Path(state.source.audio_path)
    if not audio.is_file():
        raise ActionError("not_found", f"audio not found: {audio}")

    load_plugins()
    selected_language = (language or state.source.language
                         or state.project.source_lang)
    run = begin_run(state, "transcribe")
    try:
        backend_options = dict(options or {})
        if model and "model" not in backend_options:
            backend_options["model"] = model
        backend_options.setdefault("source_id", state.source.id)
        backend_name = backend or os.environ.get("NCAT_ASR_BACKEND", "")
        if not backend_name:
            raise ActionError(
                "asr_not_configured",
                "no ASR backend selected; register a custom backend with "
                "NCAT_ASR_PLUGINS=name=module:factory and pass --backend name "
                "(or set NCAT_ASR_BACKEND)",
            )
        step = start_step(run, "asr")
        save_run(state.paths, run)
        fallback_used = False
        used_options = backend_options
        try:
            transcript = _transcribe_attempt(
                audio, selected_language, backend_name, backend_options)
            finish_step(run, step, StepStatus.SUCCEEDED,
                        message=f"{len(transcript.words)} words")
        except Exception as primary_error:
            finish_step(run, step, StepStatus.FAILED,
                        message="primary ASR failed",
                        error=str(primary_error))
            if not fallback:
                raise
            fallback_backend = (fallback.get("backend")
                                or backend_name)
            fallback_options = dict(fallback.get("options") or {})
            fallback_model = fallback.get("model")
            if fallback_model and "model" not in fallback_options:
                fallback_options["model"] = fallback_model
            fallback_options.setdefault("source_id", state.source.id)
            step = start_step(run, "asr_fallback")
            save_run(state.paths, run)
            try:
                transcript = _transcribe_attempt(
                    audio, selected_language, fallback_backend,
                    fallback_options)
                finish_step(run, step, StepStatus.SUCCEEDED,
                            message=f"{len(transcript.words)} words")
                fallback_used = True
                used_options = fallback_options
            except Exception as fallback_error:
                finish_step(run, step, StepStatus.FAILED,
                            message="fallback ASR failed",
                            error=str(fallback_error))
                raise ActionError(
                    "asr_error",
                    f"primary: {primary_error}; fallback: {fallback_error}",
                ) from fallback_error

        step = start_step(run, "finalize")
        save_run(state.paths, run)
        finalize_transcript(
            transcript, offset_ms=offset_ms, chunk_ms=chunk_ms,
        )
        finish_step(run, step, StepStatus.SUCCEEDED,
                    message=f"offset={offset_ms}ms chunk={chunk_ms}ms")

        step = start_step(run, "write")
        save_run(state.paths, run)
        json_path = state.paths.asr / "transcript.json"
        text_path = state.paths.asr / "transcript.txt"
        save_transcript(json_path, transcript)
        write_transcript_text(text_path, transcript)
        finish_step(run, step, StepStatus.SUCCEEDED,
                    artifacts=[str(json_path), str(text_path)])

        state.asr = {
            "backend": transcript.backend,
            "model": transcript.model,
            "language": transcript.language,
            "path": str(json_path),
            "text_path": str(text_path),
            "words": len(transcript.words),
            "chunks": len(transcript.chunks),
            "offset_ms": offset_ms,
            "chunk_ms": chunk_ms,
            "options": used_options,
            "fallback_used": fallback_used,
            "run_id": run.id,
        }
        state.project.stage = ProjectStage.DRAFTING
        save_project(state)

        paused = (state.project.pause_for_transcript_review
                  if pause_after_transcript is None
                  else bool(pause_after_transcript))
        finish_run(run, RunStatus.PAUSED if paused else RunStatus.SUCCEEDED)
        save_run(state.paths, run)
        return {
            "project": state.project.id,
            "backend": transcript.backend,
            "model": transcript.model,
            "language": transcript.language,
            "words": len(transcript.words),
            "chunks": len(transcript.chunks),
            "offset_ms": offset_ms,
            "chunk_ms": chunk_ms,
            "json": str(json_path),
            "text": str(text_path),
            "run_id": run.id,
            "run_status": str(run.status),
            "paused": paused,
            "fallback_used": fallback_used,
        }
    except Exception as error:
        fail_run_action(state, run, error)
        raise


def list_backends() -> dict:
    """Load plugins and describe registered ASR backends."""
    load_plugins()
    return {"backends": describe_backends()}
