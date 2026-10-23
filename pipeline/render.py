"""Render stage: burn the ASS into the video with FFmpeg.

Ported from ``sub_renderer.py``. The original ran everything at module
level and prompted with ``input()`` twice before doing any work. That pause
was dropped along with the command-line entry point: it has no meaning in a
web request, where it would simply deadlock the handler.

Encoder selection follows the same preference order as the transcode stage,
so a box with an NVIDIA card uses NVENC and everything else falls back to
libx264.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .encoders import EncoderError, ffmpeg_bin, pick_video_encoder, select_ffmpeg
from .paths import Report, noop_report


class RenderError(EncoderError):
    pass




def render(video: str | Path,
           ass: str | Path,
           output: str | Path,
           width: int = 3840,
           video_bitrate: str = '25M',
           audio_bitrate: str = '320K',
           report: Report = noop_report) -> Path:
    """Burn ``ass`` into ``video`` at ``width``; returns the output path.

    The default is a 4K60 release-grade encode. On a Pascal card that means
    NVENC at ~16 Mbps rather than x264: measured on a 10 s 4K60 clip, x264
    ``-preset slow`` CRF 20 took 26.9 s and still only reached 4.3 Mbps,
    while NVENC p4 at 16 Mbps took 8.8 s and hit 17 Mbps. For a 40-minute
    video that is 107 minutes versus 35, and the x264 path was under the
    target bitrate besides.

    ``video_bitrate`` defaults to 16M, appropriate for 4K60; scale it down
    for smaller outputs.
    """
    video, ass, output = Path(video), Path(ass), Path(output)
    for path, label in ((video, 'video'), (ass, 'ASS file')):
        if not path.is_file():
            raise RenderError(f'{label} not found: {path}')

    encoder = pick_video_encoder(report)
    report(f'rendering with {encoder} at {width}px')

    # The ass filter needs a path with no shell-hostile characters and no
    # colon, since ffmpeg parses the filter argument itself.
    filter_expr = (
        f"scale={width}:-1:flags=lanczos[sc];"
        f"[sc]ass=filename='{ass.as_posix()}'"
    )
    # Built in one go rather than spliced into a list: inserting after '-c:v'
    # displaced the encoder name itself and handed ffmpeg a garbled argument
    # order, which it reported as an output file it could not identify.
    # Bitrate-targeted in one pass, the way the legacy transcode did with
    # -b:v 1M. CRF and -preset are x264-specific and do not apply to NVENC,
    # which the P40 always uses; constant-CQ at 4K would also land near
    # 115 Mbps instead of a predictable release size.
    video_args = ['-c:v', encoder, '-b:v', video_bitrate]
    if encoder != 'libx264':
        video_args += ['-preset']

    cmd = [
        select_ffmpeg(report), '-y', '-i', str(video),
        '-c:a', 'aac', '-b:a', audio_bitrate,
        *video_args,
        '-filter_complex', filter_expr,
        str(output),
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
    """Entry point used by the web runner.

    Renders from the *original* media, never the 720p proxy. The proxy
    exists so the editor can scrub cheaply; burning from it would upscale
    720p/1 Mbps to 4K and spend hardware-encoder time on detail that cannot
    be recovered. Falls back to the proxy only when no original is present,
    and says so, because that output is not release quality.
    """
    if paths is None:
        raise ValueError('render stage requires paths')
    video = video or paths.source or paths.proxy
    if video is None:
        raise ValueError('no video to render')
    if video == paths.proxy and paths.source is None:
        report('note: no original media, rendering from the 720p proxy -- '
               'output will be upscaled and is not release quality')
    ass = ass or paths.ass
    output = output or paths.burned
    if ass is None or output is None:
        raise ValueError('render stage needs ass and output paths')
    return render(video, ass, output, report=report)


def default_output(video: str | Path) -> str:
    """Default output name: a release tag followed by the source stem."""
    return f'[BE4K6M]{Path(video).stem}.burned.mp4'
