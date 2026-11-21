"""Internal standalone-audio ASR worker for the Transcribe tab.

Writes stage lines to stdout so the API can expose task progress without
loading torch in the web process.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ncat.core.asr import get_backend, load_plugins
from ncat.core.asr.base import ASRRequest
from ncat.core.transcript import (
    save_transcript, transcript_to_payload, write_transcript_text,
)


def emit(**payload) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ncat-asr-audio-worker")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model")
    parser.add_argument("--aligner")
    parser.add_argument("--language")
    parser.add_argument("--option", action="append", default=[])
    args = parser.parse_args(argv)

    options = {}
    for item in args.option:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        try:
            options[key] = json.loads(value)
        except json.JSONDecodeError:
            options[key] = value
    if args.model:
        options.setdefault("model", args.model)
    if args.aligner:
        options.setdefault("aligner", args.aligner)
    options.setdefault("source_id", "standalone")

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        emit(stage="preparing_audio", source=args.audio)
        prepared = out / "audio-16k-mono.wav"
        conversion = subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", args.audio,
            "-vn", "-ac", "1", "-ar", "16000", str(prepared),
        ], capture_output=True, text=True)
        audio_path = str(prepared) if conversion.returncode == 0 else args.audio
        load_plugins()
        emit(stage="loading_backend", backend=args.backend)
        backend = get_backend(args.backend)
        emit(stage="loading_model", backend=args.backend,
             model=options.get("model"))
        emit(stage="transcribing", backend=args.backend)
        transcript = backend.transcribe(ASRRequest(
            audio_path=audio_path,
            language=args.language,
            options=options,
        ))
        emit(stage="finalizing", backend=args.backend,
             words=len(transcript.words))
        json_path = out / "transcript.json"
        text_path = out / "transcript.txt"
        save_transcript(json_path, transcript)
        write_transcript_text(text_path, transcript)
        payload = transcript_to_payload(transcript)
        emit(
            stage="done",
            words=len(transcript.words),
            text=transcript.text,
            language=transcript.language,
            backend=transcript.backend,
            model=transcript.model,
            duration_ms=transcript.duration_ms,
            transcript_json=str(json_path),
            transcript_text=str(text_path),
            token_confidence=len(
                (payload.get("metadata") or {}).get(
                    "asr_token_confidence", [])),
        )
        return 0
    except Exception as error:
        emit(stage="failed", error=str(error))
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
