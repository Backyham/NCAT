"""NCAT command-line entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ncat import __version__
from ncat.core.asr import ASRRequest, available_backends, get_backend
from ncat.core.media import transcode_source
from ncat.core.project import (
    create_project, load_project, save_project,
)
from ncat.core.transcript import save_transcript

COMMANDS = [
    ("init", "create a project"),
    ("ingest", "transcode proxy/audio"),
    ("transcribe", "run ASR"),
    ("correct", "import corrected transcript"),
    ("segment", "propose/import segments"),
    ("translate", "MT/TM/term suggestions"),
    ("align", "align segments/cues to ASR words"),
    ("style", "export ASS"),
    ("qa", "run QA rules"),
    ("render", "burn subtitles"),
    ("tm", "translation-memory commands"),
    ("term", "term-base commands"),
    ("corpus", "mine TM/terms/test data"),
    ("reindex", "rebuild the local index"),
    ("status", "show project status"),
]


def _cmd_init(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser() if args.source else None
    if source is not None and not source.is_file():
        print(f"source not found: {source}", file=sys.stderr)
        return 2
    state = create_project(
        args.path,
        args.name or Path(args.path).name,
        source_path=source,
        source_lang=args.source_lang,
        target_langs=args.target or ["zh-Hans"],
        force=args.force,
    )
    print(f"created {state.paths.project_file}")
    print(f"project {state.project.id} ({state.project.name})")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    if state.source is None or not state.source.path:
        print("project has no source; run init --source", file=sys.stderr)
        return 2
    source = Path(state.source.path)
    if not source.is_file():
        print(f"source not found: {source}", file=sys.stderr)
        return 2
    result = transcode_source(
        source, state.paths.media, stem="source",
        height=args.height, encoder=args.encoder,
    )
    state.source.proxy_path = str(result.proxy)
    state.source.audio_path = str(result.wav)
    state.source.duration_ms = result.info.duration_ms
    state.source.width = result.info.width
    state.source.height = result.info.height
    state.source.fps = result.info.fps
    state.source.status = "ready"
    save_project(state)
    print(f"proxy: {result.proxy}")
    print(f"audio: {result.wav}")
    print(f"encoder: {result.encoder}")
    return 0


def _cmd_transcribe(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    if state.source is None or not state.source.audio_path:
        print("project has no audio; run ingest first", file=sys.stderr)
        return 2
    audio = Path(state.source.audio_path)
    if not audio.is_file():
        print(f"audio not found: {audio}", file=sys.stderr)
        return 2
    backend = get_backend(args.backend)
    options = {"source_id": state.source.id}
    if args.model:
        options["model"] = args.model
    transcript = backend.transcribe(ASRRequest(
        audio_path=str(audio),
        language=args.language or state.source.language
        or state.project.source_lang,
        options=options,
    ))
    path = state.paths.asr / "transcript.json"
    save_transcript(path, transcript)
    state.asr = {
        "backend": transcript.backend,
        "model": transcript.model,
        "language": transcript.language,
        "path": str(path),
        "words": len(transcript.words),
    }
    save_project(state)
    print(f"transcript: {path}")
    print(f"words: {len(transcript.words)}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    print(f"project: {state.project.id}  {state.project.name}")
    print(f"stage:   {state.project.stage}")
    print(f"source:  {state.source.path if state.source else '-'}")
    if state.source:
        print(f"proxy:   {state.source.proxy_path or '-'}")
        print(f"audio:   {state.source.audio_path or '-'}")
    print(f"asr:     {state.asr or '-'}")
    return 0


def _todo(args: argparse.Namespace) -> int:
    print(f"{args.command}: not implemented yet", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ncat")
    parser.add_argument("--version", action="version", version=f"ncat {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="create a project")
    init.add_argument("path", help="project directory")
    init.add_argument("--name")
    init.add_argument("--source", help="source video/audio path")
    init.add_argument("--source-lang", default="en")
    init.add_argument("--target", action="append",
                      help="target language (repeatable; default zh-Hans)")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_cmd_init)

    ingest = subparsers.add_parser("ingest", help="transcode proxy/audio")
    ingest.add_argument("project", help="project directory")
    ingest.add_argument("--height", type=int, default=720)
    ingest.add_argument("--encoder", help="h264 encoder; default auto")
    ingest.set_defaults(func=_cmd_ingest)

    transcribe = subparsers.add_parser("transcribe", help="run ASR")
    transcribe.add_argument("project", help="project directory")
    transcribe.add_argument("--backend", default="whisper",
                            choices=available_backends())
    transcribe.add_argument("--model")
    transcribe.add_argument("--language")
    transcribe.set_defaults(func=_cmd_transcribe)

    status = subparsers.add_parser("status", help="show project status")
    status.add_argument("project", help="project directory")
    status.set_defaults(func=_cmd_status)

    for name, help_text in COMMANDS:
        if name in {"init", "ingest", "transcribe", "status"}:
            continue
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("project", nargs="?")
        command.set_defaults(func=_todo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ncat: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
