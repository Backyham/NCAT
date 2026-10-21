#!/usr/bin/env python3
"""NCAT entry point: CLI pipeline runner and web server.

    python main.py --serve [--host 0.0.0.0] [--port 80]   start the web service
    python main.py URL                                     download + transcode + transcribe
    python main.py --stages transcode,transcribe VIDEO     run selected stages
    python main.py --list-stages                           show stage names

Each stage is also independently runnable as ``python -m pipeline.<stage>``;
this script exists to sequence them without repeating the path bookkeeping.
"""
from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from pipeline.paths import STAGES, STAGE_LABELS, JobPaths  # noqa: E402


def load_config() -> dict:
    """Read [tool.ncat] from pyproject.toml, if present."""
    pyproject = BASE_DIR / 'pyproject.toml'
    if not pyproject.is_file():
        return {}
    with pyproject.open('rb') as fh:
        return tomllib.load(fh).get('tool', {}).get('ncat', {}) or {}


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from backend.app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from pipeline import runner

    config = load_config()
    source = args.source
    if source and not Path(source).exists():
        # Not a local path: treat it as something the download stage fetches.
        pass

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir \
        else BASE_DIR / 'data' / 'cli'
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(source).stem if source and Path(source).exists() else 'job'
    paths = JobPaths.build(out_dir, stem=stem, source=source)

    stages = list(STAGES) if not args.stages else [
        s.strip() for s in args.stages.split(',') if s.strip()
    ]
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        print(f'unknown stage(s): {", ".join(unknown)}', file=sys.stderr)
        print(f'available: {", ".join(STAGES)}', file=sys.stderr)
        return 2

    terms = None
    if args.glossary:
        from backend.db import get_session  # noqa: F401  (ensures deps present)
        from backend.models import Term
        from sqlmodel import Session, select
        from backend.db import engine

        with Session(engine) as session:
            glossary = session.get(__import__('backend.models', fromlist=['Glossary']).Glossary,
                                   args.glossary)
            if glossary is None:
                print(f'glossary {args.glossary} not found', file=sys.stderr)
                return 2
            terms = [t.model_dump() for t in session.exec(
                select(Term).where(Term.glossary_id == args.glossary))]

    def report(message: str) -> None:
        print(f'  {message}', flush=True)

    context = {
        'paths': paths,
        'job_id': 0,
        'source_url': args.url or (source if source and not Path(source).exists() else ''),
        'asr_model': config.get('asr_model') or None,
        'aligner_model': config.get('aligner_model') or None,
        'mt_model': args.mt_model,
        'terms': terms,
    }

    for stage in stages:
        print(f'\n=== {stage}: {STAGE_LABELS.get(stage, stage)} ===', flush=True)
        try:
            runner.run_stage(stage, 0, context, report)
        except Exception as exc:
            print(f'\nstage {stage} failed: {type(exc).__name__}: {exc}',
                  file=sys.stderr)
            return 1

    print(f'\nartifacts in {paths.root}')
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog='main.py',
        description='NCAT: video translation pipeline and web service.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Stages, in order:\n  ' + '\n  '.join(
            f'{s:<12} {STAGE_LABELS[s]}' for s in STAGES),
    )
    p.add_argument('--serve', action='store_true',
                   help='run the web service instead of the CLI pipeline')
    p.add_argument('--host', default='0.0.0.0',
                   help='bind address for --serve (default: 0.0.0.0, LAN)')
    p.add_argument('--port', type=int, default=80,
                   help='bind port for --serve (default: 80)')
    p.add_argument('--log-level', default='info')

    p.add_argument('source', nargs='?',
                   help='local media file, or a URL to download')
    p.add_argument('--url', default='',
                   help='explicit source URL for the download stage')
    p.add_argument('--stages', default='',
                   help='comma-separated subset of stages to run')
    p.add_argument('--out-dir', default=None,
                   help='output directory (default: data/cli)')
    p.add_argument('--glossary', type=int, default=None,
                   help='glossary id whose terms to apply during translate')
    p.add_argument('--mt-model', default=None,
                   help='MT model name registered with the MT service. '
                        'Required for the translate stage: two candidates '
                        'are still under evaluation and neither is a '
                        'default.')
    p.add_argument('--list-stages', action='store_true',
                   help='print the stage list and exit')

    args = p.parse_args(argv)

    if args.list_stages:
        for stage in STAGES:
            print(f'{stage:<12} {STAGE_LABELS[stage]}')
        return 0

    if args.serve:
        if os.geteuid() == 0:
            print('refusing to run as root; use setcap on the interpreter '
                  'instead:\n'
                  '  sudo setcap cap_net_bind_service=+ep '
                  '.venv-app/bin/python', file=sys.stderr)
            return 1
        return cmd_serve(args)

    if not args.source and not args.url:
        p.print_help()
        return 2
    return cmd_run(args)


if __name__ == '__main__':
    raise SystemExit(main())
