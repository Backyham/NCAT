"""Render stage: burn the ASS into the video with FFmpeg.

Ported from ``sub_renderer.py``. The original ran everything at module
level and prompted with ``input()`` twice before doing any work, which is
fine at a terminal but deadlocks a request handler. That pause behaviour is
kept in the CLI entry point only; the pipeline-callable function takes its
arguments and returns immediately.

Encoder selection follows the same preference order as the transcode stage,
so a box with an NVIDIA card uses NVENC and everything else falls back to
libx264.
"""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .paths import Report, noop_report

VIDEO_ENCODERS = ('h264_nvenc', 'h264_qsv', 'h264_amf', 'libx264')


class RenderError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _pick_encoder() -> str:
    if shutil.which('ffmpeg') is None:
        raise RenderError('ffmpeg not found on PATH. On Arch: sudo pacman -S ffmpeg')
    for encoder in VIDEO_ENCODERS:
        probe = subprocess.run(
            ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'nullsrc=s=hd720', '-t', '1',
             '-c:v', encoder, '-f', 'mp4', '/dev/null'],
            capture_output=True,
        )
        if probe.returncode != 8:
            return encoder
    raise RenderError(f'no usable H.264 encoder among {VIDEO_ENCODERS}')


def render(video: str | Path,
           ass: str | Path,
           output: str | Path,
           width: int = 3840,
           crf: str = '20',
           preset: str = 'slow',
           report: Report = noop_report) -> Path:
    """Burn ``ass`` into ``video`` at ``width``; returns the output path."""
    video, ass, output = Path(video), Path(ass), Path(output)
    for path, label in ((video, 'video'), (ass, 'ass file')):
        if not path.is_file():
            raise RenderError(f'{label} not found: {path}')

    encoder = _pick_encoder()
    report(f'rendering with {encoder} at {width}px')

    # The ass filter needs a path with no shell-hostile characters and no
    # colon, since ffmpeg parses the filter argument itself.
    filter_expr = (
        f"scale={width}:-1:flags=lanczos[sc];"
        f"[sc]ass=filename='{ass.as_posix()}'"
    )
    cmd = [
        'ffmpeg', '-y', '-i', str(video),
        '-c:a', 'aac', '-b:a', '320K',
        '-c:v', encoder,
        '-filter_complex', filter_expr,
        str(output),
    ]
    if encoder == 'libx264':
        cmd[cmd.index('-c:v') + 1:cmd.index('-c:v') + 1] = [
            '-preset', preset, '-crf', crf,
        ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = '\n'.join((proc.stderr or '').strip().splitlines()[-10:])
        raise RenderError(f'render failed (rc={proc.returncode}):\n{tail}')
    if not output.is_file():
        raise RenderError(f'render produced no output: {output}')

    report(f'render done: {output.name} '
           f'({output.stat().st_size / 1048576:.1f} MB)')
    return output


def render_stage(job_id: int,
                 report: Report = noop_report,
                 paths=None,
                 video: str | Path | None = None,
                 ass: str | Path | None = None,
                 output: str | Path | None = None,
                 **_ignored) -> Path:
    """Entry point used by the web runner."""
    if paths is None:
        raise ValueError('render stage requires paths')
    video = video or paths.proxy or paths.source
    ass = ass or paths.ass
    output = output or paths.burned
    if video is None or ass is None or output is None:
        raise ValueError('render stage needs video, ass and output paths')
    return render(video, ass, output, report=report)


def default_output(video: str | Path) -> str:
    """Default output name: a release tag followed by the source stem."""
    return f'[BE4K6M]{Path(video).stem}.burned.mp4'


def _cli() -> int:
    """Interactive entry point.

    The original script paused on ``input()`` so the user could read the
    result before the window closed. That pause belongs to the CLI, not to
    the pipeline function above, so a web request never blocks on stdin.
    """
    video = input('Original video, please.\n')
    ass = input('ASS file, please.\n')
    try:
        render(video, ass, default_output(video),
               report=lambda m: print(m, flush=True))
    except (RenderError, OSError) as exc:
        print(f'render failed: {exc}')
        return 1
    input('Press Enter to Exit')
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog='python -m pipeline.render',
        description='NCAT stage 7/7: burn an ASS into a video.',
    )
    p.add_argument('video', help='Source video')
    p.add_argument('ass', help='ASS subtitle file')
    p.add_argument('-o', '--output', default=None,
                   help='Output path (default: [BE4K6M]<stem>.burned.mp4)')
    p.add_argument('--width', type=int, default=3840,
                   help='Output width in pixels (default: 3840, i.e. 4K)')
    p.add_argument('--crf', default='20')
    p.add_argument('--preset', default='slow')
    args = p.parse_args(argv)

    output = args.output or default_output(args.video)
    try:
        render(args.video, args.ass, output, width=args.width,
               crf=args.crf, preset=args.preset,
               report=lambda m: print(m, flush=True))
    except (RenderError, OSError) as exc:
        print(f'render failed: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    import sys
    # With no arguments, keep the original interactive behaviour.
    raise SystemExit(_cli() if len(sys.argv) == 1 else main())
