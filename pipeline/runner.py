"""Stage dispatch for the web runner.

``backend.jobs`` resolves a stage name to a callable and calls it as
``fn(job_id=..., report=..., **context)``. The stage modules in this
package are written to be callable directly with the same signature, so
this module only has to adapt the few places where the CLI shape differs
(``download`` takes a URL first, ``transcode`` takes two output paths).

Keeping the adaptation here rather than inside each stage means every stage
module stays a plain function with named keyword arguments, which is what
makes ``python -m pipeline.<stage>`` and the web path share code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .paths import STAGES, JobPaths, Report


class StageError(RuntimeError):
    pass


def _resolve(stage: str) -> Callable[..., Any]:
    import importlib
    table = {
        'download': ('pipeline.download', 'download_stage'),
        'transcode': ('pipeline.transcode', 'transcode_stage'),
        'transcribe': ('pipeline.transcribe', 'transcribe_stage'),
        'segment': ('pipeline.segment', 'segment'),
        'translate': ('pipeline.translate', 'translate_cues'),
        'style': ('pipeline.style', 'style_cues'),
        'render': ('pipeline.render', 'render_stage'),
    }
    if stage not in table:
        raise StageError(f'unknown stage: {stage}')
    module_name, attr = table[stage]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise StageError(f'cannot import stage {stage}: {exc}') from exc
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise StageError(f'stage {stage} has no entry point {attr}') from exc
    raise StageError(f'unknown stage: {stage}')


def _paths(context: dict[str, Any]) -> JobPaths:
    paths = context.get('paths')
    if not isinstance(paths, JobPaths):
        raise StageError('runner requires a JobPaths in the context')
    return paths


def run_stage(stage: str,
              job_id: int,
              context: dict[str, Any],
              report: Report) -> Any:
    """Run one stage against the job's paths."""
    paths = _paths(context)
    entry = _resolve(stage)

    kwargs: dict[str, Any] = {
        'job_id': job_id,
        'report': report,
        'paths': paths,
    }
    if stage == 'download':
        url = context.get('source_url') or ''
        if not url:
            # An uploaded file needs no download step.
            report('no source URL; download skipped')
            return {'skipped': True}
        kwargs['url'] = url
        kwargs['output_dir'] = paths.root
    elif stage == 'transcribe':
        kwargs['wav'] = paths.wav
        kwargs['asr_model'] = context.get('asr_model')
        kwargs['aligner_model'] = context.get('aligner_model')
    elif stage == 'translate':
        # No default: the MT model is an open decision, so the caller must
        # name one. translate_cues raises a clear error if it is missing.
        kwargs['model'] = context.get('mt_model')
        kwargs['terms'] = context.get('terms')
    elif stage == 'render':
        video = paths.proxy or paths.source
        if video is None or not Path(video).is_file():
            report('no video to render; skipped')
            return {'skipped': True}
        kwargs['video'] = video
        kwargs['ass'] = paths.ass
        kwargs['output'] = paths.burned

    # Drop keys the stage does not accept, so callers can pass a uniform
    # context without every stage declaring the same optional arguments.
    import inspect
    accepted = inspect.signature(entry).parameters
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in accepted.values()):
        kwargs = {k: v for k, v in kwargs.items() if k in accepted}

    return entry(**kwargs)


__all__ = ['STAGES', 'JobPaths', 'run_stage', 'StageError']
