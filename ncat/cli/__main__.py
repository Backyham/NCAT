"""NCAT command-line entry point."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ncat import __version__
from ncat.core.actions import (
    ActionError, list_backends, parse_options,
    project_terms_tm as _project_terms_tm, run_align, run_ingest,
    run_render, run_segment, run_style, run_transcribe, run_translate,
)
from ncat.core.assets.tm import (
    find_matches, read_units, write_tmx, write_units,
)
from ncat.core.assets.termbase import (
    import_table, match_terms, read_terms, write_tbx, write_terms,
)
from ncat.core.corpus import build_and_write
from ncat.core.qa import read_qa, run_qa, update_qa, write_qa
from ncat.core.asr.base import ASRError
from ncat.core.index import build_index, search_index
from ncat.core.media import MediaError
from ncat.core.project import (
    create_project, load_project, new_id, read_cues, read_segments,
    save_project, write_cues, write_segments,
)
from ncat.core.segment import load_bilingual_pair
from ncat.core.subs.model import merge_cues, split_cue
from ncat.core.subs.rules import apply_all
from ncat.core.runs import latest_run
from ncat.core.text import source_hash
from ncat.core.types import Cue, QAStatus, SegmentStatus

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
    ("serve", "start the local Web editor"),
    ("status", "show project status"),
]


class CLIError(ActionError):
    """Backwards-compatible name for CLI-side action errors."""


def _json_mode(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _emit(args: argparse.Namespace, payload: dict[str, Any],
          lines: list[str]) -> None:
    if _json_mode(args):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for line in lines:
        print(line)


def _emit_error(args: argparse.Namespace, code: str, message: str,
                fields: dict[str, Any] | None = None) -> None:
    if _json_mode(args):
        error: dict[str, Any] = {"code": code, "message": message}
        if fields:
            error["fields"] = fields
        print(json.dumps({"error": error}, ensure_ascii=False, sort_keys=True),
              file=sys.stderr)
        return
    print(f"ncat: {message}", file=sys.stderr)


def _cmd_init(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser() if args.source else None
    if source is not None and not source.is_file():
        raise CLIError("not_found", f"source not found: {source}")
    state = create_project(
        args.path,
        args.name or Path(args.path).name,
        source_path=source,
        source_lang=args.source_lang,
        target_langs=args.target or ["zh-Hans"],
        force=args.force,
    )
    if args.pause_after_transcript is not None:
        state.project.pause_for_transcript_review = args.pause_after_transcript
        save_project(state)
    payload = {
        "project": state.project.id,
        "name": state.project.name,
        "path": str(state.paths.project_file),
        "source": state.source.path if state.source else None,
        "source_lang": state.project.source_lang,
        "target_langs": state.project.target_langs,
        "pause_for_transcript_review":
            state.project.pause_for_transcript_review,
    }
    _emit(args, payload, [
        f"created {state.paths.project_file}",
        f"project {state.project.id} ({state.project.name})",
    ])
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    payload = run_ingest(
        state, height=args.height, encoder=args.encoder, force=args.force)
    _emit(args, payload, [
        f"proxy: {payload['proxy']}",
        f"audio: {payload['audio']}",
        f"encoder: {payload['encoder']}",
        f"run: {payload['run_id']} ({payload['run_status']})",
    ])
    return 0


def _cmd_transcribe(args: argparse.Namespace) -> int:
    if args.list_backends:
        payload = list_backends()
        backends = payload["backends"]
        lines = [
            f"{item['name']}: " + ", ".join(
                f"{key}={value}" for key, value in item["capabilities"].items()
                if value not in (False, None, "")
            )
            for item in backends
        ]
        if not backends and not _json_mode(args):
            lines.append(
                "no ASR backends registered; set "
                "NCAT_ASR_PLUGINS=name=module:factory")
        _emit(args, payload, lines)
        return 0

    if not args.project:
        raise CLIError("invalid_argument",
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
        pause_after_transcript=args.pause_after_transcript,
        fallback=fallback,
    )
    lines = [
        f"transcript: {payload['json']}",
        f"text: {payload['text']}",
        f"words: {payload['words']}",
        f"run: {payload['run_id']} ({payload['run_status']})",
    ]
    if payload["paused"]:
        lines.append("paused for transcript review; "
                     "use --no-pause-after-transcript to skip")
    _emit(args, payload, lines)
    return 0


def _cmd_reindex(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    if args.search:
        result = search_index(state.paths, args.search, limit=args.limit)
        _emit(args, {"project": state.project.id, "query": args.search,
                     "results": result}, [str(item) for item in result])
        return 0
    status = build_index(state.paths)
    _emit(args, {
        "project": state.project.id,
        "index": status.path,
        "segments": status.segments,
        "cues": status.cues,
        "built_at": status.built_at,
    }, [f"index: {status.path}", f"segments: {status.segments}",
        f"cues: {status.cues}"])
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    segments = read_segments(state.paths)
    cues = read_cues(state.paths)

    if args.export_path:
        lines = [f"{item.source}\t{item.target}"
                 for item in sorted(segments, key=lambda item: item.order)]
        Path(args.export_path).expanduser().write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        _emit(args, {"project": state.project.id,
                     "export": str(args.export_path), "rows": len(lines)},
              [f"exported {len(lines)} rows to {args.export_path}"])
        return 0

    if args.import_path:
        rows = [line for line in Path(args.import_path).expanduser()
                .read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        for index, line in enumerate(rows):
            if index >= len(segments):
                break
            source, target = (line.split("\t", 1) + [""])[:2]
            segments[index].source = source.strip()
            segments[index].target = target.strip()
            segments[index].source_hash = source_hash(segments[index].source)
            segments[index].status = (
                SegmentStatus.TRANSLATED if segments[index].target
                else SegmentStatus.EMPTY)
            segments[index].revision += 1
        write_segments(state.paths, segments)
        build_index(state.paths)
        _emit(args, {"project": state.project.id, "imported": len(rows)},
              [f"imported {len(rows)} rows"])
        return 0

    changed = False
    if args.split_cue:
        found = next((item for item in cues if item.id == args.split_cue), None)
        if found is None:
            raise CLIError("not_found", f"cue not found: {args.split_cue}")
        left, right = split_cue(found, at_ms=args.at_ms,
                                ratio=args.ratio)
        cues = [left if item.id == found.id else item for item in cues]
        cues.append(right)
        changed = True
    if args.merge_cue:
        first = next((item for item in cues if item.id == args.merge_cue), None)
        second = next((item for item in cues
                       if item.id == args.other_cue), None)
        if first is None or second is None:
            raise CLIError("not_found", "both --merge-cue and --other-cue "
                                        "must reference existing cues")
        merged = merge_cues(first, second)
        cues = [item for item in cues
                if item.id not in {first.id, second.id}] + [merged]
        changed = True
    if args.insert_cue:
        index = next((i for i, item in enumerate(cues)
                      if item.id == args.insert_cue), len(cues) - 1)
        cue = Cue(
            id=new_id("cue"),
            project_id=state.project.id, segment_id=None,
            order=index + 1 if index >= 0 else len(cues),
            start_ms=args.start_ms, end_ms=args.end_ms,
            source_text=args.source_text or "",
            target_text=args.target_text or "",
            style=args.style or "Default",
        )
        cues.insert(index + 1 if index >= 0 else len(cues), cue)
        changed = True
    if args.delete_cue:
        before = len(cues)
        cues = [item for item in cues if item.id != args.delete_cue]
        if len(cues) == before:
            raise CLIError("not_found", f"cue not found: {args.delete_cue}")
        changed = True
    if args.lock_cue:
        found = next((item for item in cues if item.id == args.lock_cue), None)
        if found is None:
            raise CLIError("not_found", f"cue not found: {args.lock_cue}")
        found.locked = True
        changed = True
    if args.unlock_cue:
        found = next((item for item in cues
                      if item.id == args.unlock_cue), None)
        if found is None:
            raise CLIError("not_found", f"cue not found: {args.unlock_cue}")
        found.locked = False
        changed = True
    if args.segment_status:
        found = next((item for item in segments
                      if item.id == args.segment_status), None)
        if found is None:
            raise CLIError("not_found",
                           f"segment not found: {args.segment_status}")
        try:
            found.status = SegmentStatus(args.status)
        except ValueError as exc:
            raise CLIError("invalid_argument",
                           f"unknown segment status: {args.status}") from exc
        found.revision += 1
        write_segments(state.paths, segments)
        changed = True

    if changed:
        for index, cue in enumerate(sorted(cues, key=lambda item: (
                item.start_ms if item.start_ms is not None else 10**15,
                item.order))):
            cue.order = index
        apply_all(cues)
        write_cues(state.paths, cues)
        build_index(state.paths)
    _emit(args, {"project": state.project.id, "cues": len(cues),
                 "segments": len(segments), "changed": changed},
          [f"cues: {len(cues)}", f"segments: {len(segments)}"])
    return 0


def _cmd_tm(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    path = state.paths.assets / "tm.jsonl"
    units = read_units(path)
    if args.import_source:
        from ncat.core.segment import load_bilingual_pair
        pairs = load_bilingual_pair(args.import_source, args.import_target)
        from ncat.core.corpus import units_from_pairs
        new_units = units_from_pairs(
            pairs, source_lang=state.project.source_lang,
            target_lang=(state.project.target_langs[0]
                         if state.project.target_langs else "zh-Hans"),
            origin_project=state.project.id)
        existing = {unit.source_hash for unit in units}
        units.extend(unit for unit in new_units
                     if unit.source_hash not in existing)
        write_units(path, units)
        _emit(args, {"units": len(units), "added": len(new_units)},
              [f"TM units: {len(units)}"])
        return 0
    if args.export_tmx:
        write_tmx(units, args.export_tmx)
        _emit(args, {"units": len(units), "tmx": args.export_tmx},
              [f"exported {len(units)} units"])
        return 0
    if args.match:
        hits = find_matches(args.match, units, limit=args.limit)
        _emit(args, {"matches": [hit.__dict__ for hit in hits]},
              [f"{hit.score:.3f} {hit.match_type} {hit.source}" for hit in hits])
        return 0
    _emit(args, {"units": len(units), "path": str(path)},
          [f"TM units: {len(units)}"])
    return 0


def _cmd_term(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    path = state.paths.assets / "terms.jsonl"
    terms = read_terms(path)
    if args.import_path:
        imported = import_table(
            args.import_path,
            source_lang=state.project.source_lang,
            target_lang=(state.project.target_langs[0]
                         if state.project.target_langs else "zh-Hans"))
        existing = {(term.source.casefold(), term.target.casefold())
                    for term in terms}
        terms.extend(term for term in imported
                     if (term.source.casefold(), term.target.casefold())
                     not in existing)
        write_terms(path, terms)
        _emit(args, {"terms": len(terms)}, [f"terms: {len(terms)}"])
        return 0
    if args.export_tbx:
        write_tbx(terms, args.export_tbx)
        _emit(args, {"terms": len(terms), "tbx": args.export_tbx},
              [f"exported {len(terms)} terms"])
        return 0
    if args.match:
        hits = match_terms(args.match, terms,
                           confirmed_only=not args.all_statuses,
                           limit=args.limit)
        _emit(args, {"matches": [hit.__dict__ for hit in hits]},
              [f"{hit.status} {hit.source} -> {hit.target}" for hit in hits])
        return 0
    _emit(args, {"terms": len(terms)}, [f"terms: {len(terms)}"])
    return 0


def _cmd_qa(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    terms, _ = _project_terms_tm(state)
    if args.accept or args.ignore:
        items = read_qa(state.paths.qa)
        item_id = args.accept or args.ignore
        status = QAStatus.ACCEPTED if args.accept else QAStatus.IGNORED
        update_qa(items, item_id, status)
        write_qa(state.paths.qa, items)
        _emit(args, {"updated": item_id, "status": str(status)},
              [f"QA {item_id}: {status}"])
        return 0
    items = run_qa(state.project.id, read_segments(state.paths),
                   read_cues(state.paths), terms)
    write_qa(state.paths.qa, items)
    counts: dict[str, int] = {}
    for item in items:
        counts[str(item.severity)] = counts.get(str(item.severity), 0) + 1
    _emit(args, {"items": len(items), "counts": counts,
                 "errors": [item.__dict__ for item in items
                            if str(item.severity) == "error"]},
          [f"QA items: {len(items)}", str(counts)])
    return 0


def _cmd_corpus(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    result = build_and_write(
        out=args.out or str(state.paths.assets / "corpus.jsonl"),
        train=not args.no_train, test_ratio=args.test_ratio,
        source_path=args.source, target_path=args.target,
        ass_path=args.ass,
        source_lang=args.source_lang or state.project.source_lang,
        target_lang=args.target_lang or (
            state.project.target_langs[0]
            if state.project.target_langs else "zh-Hans"),
        origin_project=state.project.id,
    )
    _emit(args, result, [str(result)])
    return 0


def _cmd_translate(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    payload = run_translate(
        state,
        backend=args.backend or "llama_cpp",
        base_url=args.base_url,
        model=args.model or "local",
        target_lang=args.target_lang,
        brief=args.brief or "",
        style=args.style or "",
        limit=args.limit or 0,
        options=parse_options(args.option),
    )
    _emit(args, payload, [str(payload)])
    return 0


def _cmd_segment(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    payload = run_segment(
        state, propose=args.propose, import_path=args.import_path,
        target=args.target, force=args.force)
    _emit(args, payload, [
        f"segments: {payload['segments']}",
        f"cues: {payload['cues']}",
    ])
    return 0


def _cmd_align(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    payload = run_align(state, offset_ms=args.offset_ms)
    average = payload["mean_confidence"]
    _emit(args, payload, [
        f"cues: {payload['cues']}",
        f"needs_timing: {payload['needs_timing']}",
        f"mean_confidence: "
        + ("-" if average is None else f"{average:.3f}"),
    ])
    return 0


def _cmd_style(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    payload = run_style(
        state, template=args.template, output=args.output,
        bilingual=args.bilingual)
    _emit(args, payload, [f"ass: {payload['ass']}"])
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    payload = run_render(
        state, ass=args.ass, preset=args.preset, encoder=args.encoder,
        crf=args.crf, output=args.output)
    _emit(args, payload, [
        f"render: {payload['render']}",
        f"ass: {payload['ass']}",
    ])
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
        from ncat.api.app import create_app
    except ImportError as error:
        raise CLIError(
            "missing_web_dependency",
            "install starlette/uvicorn in the web venv: "
            "uv pip install starlette uvicorn") from error
    app = create_app(workspace=args.workspace)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    state = load_project(args.project)
    run = latest_run(state.paths)
    payload = {
        "project": state.project.id,
        "name": state.project.name,
        "stage": str(state.project.stage),
        "source": state.source.path if state.source else None,
        "proxy": state.source.proxy_path if state.source else None,
        "audio": state.source.audio_path if state.source else None,
        "asr": state.asr,
        "ingest": state.ingest,
        "latest_run": None if run is None else {
            "id": run.id,
            "status": str(run.status),
            "recipe": run.recipe,
            "steps": [
                {"name": step.name, "status": str(step.status),
                 "error": step.error}
                for step in run.steps
            ],
        },
    }
    lines = [
        f"project: {state.project.id}  {state.project.name}",
        f"stage:   {state.project.stage}",
        f"source:  {state.source.path if state.source else '-'}",
    ]
    if state.source:
        lines.append(f"proxy:   {state.source.proxy_path or '-'}")
        lines.append(f"audio:   {state.source.audio_path or '-'}")
    lines.append(f"asr:     {state.asr or '-'}")
    if run is not None:
        lines.append(f"run:     {run.id} ({run.status})")
        for step in run.steps:
            suffix = f" - {step.error}" if step.error else ""
            lines.append(f"  {step.name}: {step.status}{suffix}")
    _emit(args, payload, lines)
    return 0


def _todo(args: argparse.Namespace) -> int:
    message = f"{args.command}: not implemented yet"
    _emit_error(args, "not_implemented", message)
    return 2


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON")


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
    init.add_argument("--pause-after-transcript",
                      action=argparse.BooleanOptionalAction, default=None,
                      help="pause transcript review (default: project setting)")
    _add_json(init)
    init.set_defaults(func=_cmd_init)

    ingest = subparsers.add_parser("ingest", help="transcode proxy/audio")
    ingest.add_argument("project", help="project directory")
    ingest.add_argument("--height", type=int, default=720)
    ingest.add_argument("--encoder", help="h264 encoder; default auto")
    ingest.add_argument("--force", action="store_true",
                        help="re-transcode even if the checksum matches")
    _add_json(ingest)
    ingest.set_defaults(func=_cmd_ingest)

    transcribe = subparsers.add_parser("transcribe", help="run ASR")
    transcribe.add_argument("project", nargs="?")
    transcribe.add_argument("--backend",
                            default=None,
                            help="custom backend name from --list-backends "
                                 "(or NCAT_ASR_BACKEND)")
    transcribe.add_argument("--model")
    transcribe.add_argument("--language")
    transcribe.add_argument("--option", action="append", metavar="KEY=VALUE",
                            help="backend-specific option (repeatable)")
    transcribe.add_argument("--fallback-backend", default=None,
                            help="retry with this backend when primary fails")
    transcribe.add_argument("--fallback-model", default=None)
    transcribe.add_argument("--fallback-option", action="append",
                            metavar="KEY=VALUE",
                            help="fallback backend option (repeatable)")
    transcribe.add_argument("--offset-ms", type=int, default=0,
                            help="global word timestamp offset")
    transcribe.add_argument("--chunk-ms", type=int, default=0,
                            help="group words into chunks of this span")
    transcribe.add_argument("--pause-after-transcript",
                            action=argparse.BooleanOptionalAction, default=None,
                            help="pause transcript review (default: project setting)")
    transcribe.add_argument("--list-backends", action="store_true",
                            help="show available ASR backends and exit")
    _add_json(transcribe)
    transcribe.set_defaults(func=_cmd_transcribe)

    status = subparsers.add_parser("status", help="show project status")
    status.add_argument("project", help="project directory")
    _add_json(status)
    status.set_defaults(func=_cmd_status)

    segment = subparsers.add_parser("segment", help="propose/import segments")
    segment.add_argument("project", help="project directory")
    segment.add_argument("--propose", action="store_true",
                         help="create segments from the ASR transcript")
    segment.add_argument("--import", dest="import_path",
                         help="bilingual text file (TAB-separated)")
    segment.add_argument("--target", help="target text file for --import")
    segment.add_argument("--force", action="store_true")
    _add_json(segment)
    segment.set_defaults(func=_cmd_segment)

    align = subparsers.add_parser("align", help="align cues to ASR words")
    align.add_argument("project", help="project directory")
    align.add_argument("--offset-ms", type=int, default=0)
    _add_json(align)
    align.set_defaults(func=_cmd_align)

    style = subparsers.add_parser("style", help="export ASS")
    style.add_argument("project", help="project directory")
    style.add_argument("--template", help="ASS template path")
    style.add_argument("--output", help="output ASS path")
    style.add_argument("--bilingual", action=argparse.BooleanOptionalAction,
                       default=True)
    _add_json(style)
    style.set_defaults(func=_cmd_style)

    render = subparsers.add_parser("render", help="burn subtitles")
    render.add_argument("project", help="project directory")
    render.add_argument("--ass", help="ASS path (default: export current cues)")
    render.add_argument("--preset", default="original",
                        choices=["original", "youtube_1080p",
                                 "bilibili_2160p"])
    render.add_argument("--encoder", default="libx264")
    render.add_argument("--crf", type=int, default=20)
    render.add_argument("--output", help="output video path")
    _add_json(render)
    render.set_defaults(func=_cmd_render)

    translate = subparsers.add_parser(
        "translate", help="MT/TM/term suggestions")
    translate.add_argument("project", help="project directory")
    translate.add_argument("--backend", default="llama_cpp")
    translate.add_argument("--base-url", help="llama.cpp server base URL")
    translate.add_argument("--model", default="local")
    translate.add_argument("--option", action="append", metavar="KEY=VALUE")
    translate.add_argument("--target-lang")
    translate.add_argument("--brief")
    translate.add_argument("--style")
    translate.add_argument("--limit", type=int, default=0)
    _add_json(translate)
    translate.set_defaults(func=_cmd_translate)

    tm = subparsers.add_parser("tm", help="translation-memory commands")
    tm.add_argument("project", help="project directory")
    tm.add_argument("--import", dest="import_source")
    tm.add_argument("--import-target", dest="import_target")
    tm.add_argument("--export-tmx")
    tm.add_argument("--match")
    tm.add_argument("--limit", type=int, default=5)
    _add_json(tm)
    tm.set_defaults(func=_cmd_tm)

    term = subparsers.add_parser("term", help="term-base commands")
    term.add_argument("project", help="project directory")
    term.add_argument("--import", dest="import_path")
    term.add_argument("--export-tbx")
    term.add_argument("--match")
    term.add_argument("--limit", type=int, default=20)
    term.add_argument("--all-statuses", action="store_true")
    _add_json(term)
    term.set_defaults(func=_cmd_term)

    qa = subparsers.add_parser("qa", help="run QA rules")
    qa.add_argument("project", help="project directory")
    qa.add_argument("--accept")
    qa.add_argument("--ignore")
    _add_json(qa)
    qa.set_defaults(func=_cmd_qa)

    corpus = subparsers.add_parser("corpus", help="mine TM/test data")
    corpus.add_argument("project", help="project directory")
    corpus.add_argument("--source")
    corpus.add_argument("--target")
    corpus.add_argument("--ass")
    corpus.add_argument("--out")
    corpus.add_argument("--test-ratio", type=float, default=0.1)
    corpus.add_argument("--no-train", action="store_true")
    corpus.add_argument("--source-lang")
    corpus.add_argument("--target-lang")
    _add_json(corpus)
    corpus.set_defaults(func=_cmd_corpus)

    edit = subparsers.add_parser("edit", help="export/import or edit cues")
    edit.add_argument("project", help="project directory")
    edit.add_argument("--export", dest="export_path",
                      help="export segments as source<TAB>target")
    edit.add_argument("--import", dest="import_path",
                      help="import segments from source<TAB>target")
    edit.add_argument("--split-cue", help="split this cue")
    edit.add_argument("--merge-cue", help="merge this cue with --other-cue")
    edit.add_argument("--other-cue")
    edit.add_argument("--insert-cue", help="insert after this cue (or at end)")
    edit.add_argument("--delete-cue")
    edit.add_argument("--lock-cue")
    edit.add_argument("--unlock-cue")
    edit.add_argument("--segment-status", help="segment id to update")
    edit.add_argument("--status", help="new segment status")
    edit.add_argument("--at-ms", type=int)
    edit.add_argument("--ratio", type=float, default=0.5)
    edit.add_argument("--start-ms", type=int)
    edit.add_argument("--end-ms", type=int)
    edit.add_argument("--source-text", default="")
    edit.add_argument("--target-text", default="")
    edit.add_argument("--style", default="Default")
    _add_json(edit)
    edit.set_defaults(func=_cmd_edit)

    reindex = subparsers.add_parser("reindex", help="rebuild the local index")
    reindex.add_argument("project", help="project directory")
    reindex.add_argument("--search", help="search the index instead of rebuilding")
    reindex.add_argument("--limit", type=int, default=50)
    _add_json(reindex)
    reindex.set_defaults(func=_cmd_reindex)

    serve = subparsers.add_parser("serve", help="start the local Web editor")
    serve.add_argument("--workspace", help="workspace directory")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=_cmd_serve)

    for name, help_text in COMMANDS:
        if name in {"init", "ingest", "transcribe", "status", "segment",
                    "align", "style", "render", "serve", "edit", "reindex",
                    "translate", "tm", "term", "qa", "corpus"}:
            continue
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("project", nargs="?")
        _add_json(command)
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
    except ActionError as error:
        _emit_error(args, error.code, error.message, error.fields)
        return error.exit_code
    except FileExistsError as error:
        _emit_error(args, "already_exists", str(error))
        return 1
    except FileNotFoundError as error:
        _emit_error(args, "not_found", str(error))
        return 1
    except ASRError as error:
        _emit_error(args, "asr_error", str(error))
        return 1
    except MediaError as error:
        _emit_error(args, "media_error", str(error))
        return 1
    except ValueError as error:
        _emit_error(args, "invalid_argument", str(error))
        return 1
    except OSError as error:
        _emit_error(args, "io_error", str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
