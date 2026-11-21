"""Internal project-ASR worker.

The API invokes this module in the ASR virtualenv, passing a project root and
options. It emits one JSON payload on stdout so the API can keep model imports
out of the web process.
"""
from __future__ import annotations

import argparse
import json
import sys

from ncat.core.actions import (
    ActionError, list_backends, parse_options, run_transcribe,
)
from ncat.core.asr.base import ASRError
from ncat.core.project import load_project


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _error_payload(error: Exception) -> dict:
    if isinstance(error, ActionError):
        code, message = error.code, error.message
    elif isinstance(error, ASRError):
        code, message = "asr_error", str(error)
    elif isinstance(error, FileNotFoundError):
        code, message = "not_found", str(error)
    elif isinstance(error, ValueError):
        code, message = "invalid_argument", str(error)
    else:
        code, message = "action_failed", str(error)
    return {"error": {"code": code, "message": message}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ncat-asr-worker")
    parser.add_argument("project", nargs="?")
    parser.add_argument("--backend")
    parser.add_argument("--model")
    parser.add_argument("--language")
    parser.add_argument("--option", action="append", default=[])
    parser.add_argument("--fallback-backend")
    parser.add_argument("--fallback-model")
    parser.add_argument("--fallback-option", action="append", default=[])
    parser.add_argument("--offset-ms", type=int, default=0)
    parser.add_argument("--chunk-ms", type=int, default=0)
    parser.add_argument("--no-pause-after-transcript", action="store_true")
    parser.add_argument("--list-backends", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.list_backends:
            _emit(list_backends())
            return 0
        if not args.project:
            raise ActionError(
                "invalid_argument",
                "transcribe requires a project (or --list-backends)")
        state = load_project(args.project)
        fallback = None
        if args.fallback_backend:
            fallback = {
                "backend": args.fallback_backend,
                "model": args.fallback_model,
                "options": parse_options(args.fallback_option),
            }
        payload = run_transcribe(
            state, backend=args.backend, model=args.model,
            language=args.language, options=parse_options(args.option),
            offset_ms=args.offset_ms, chunk_ms=args.chunk_ms,
            pause_after_transcript=(False if args.no_pause_after_transcript
                                    else None),
            fallback=fallback,
        )
        _emit(payload)
        return 0
    except Exception as error:
        print(json.dumps(_error_payload(error), ensure_ascii=False),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
