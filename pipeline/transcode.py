"""Transcode stage: build the preview proxy and the ASR audio track.

Rewritten from the old top-level ``transcode.py``. Behaviour is the same
(720p 1 Mbps H.264 proxy + 16-bit 16 kHz mono PCM WAV), but the encoder
probe now happens once and reports what it chose, instead of silently
trying four codecs in order on every invocation.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .encoders import (EncoderError, ffmpeg_available, pick_video_encoder,
                        select_ffmpeg)
from .paths import Report, noop_report

# Preference order and the encoder probe live in pipeline/encoders.py so the
# render stage cannot pick a different encoder than this one.


class TranscodeError(EncoderError):
    pass





def transcode(source: str | Path,
              proxy: str | Path,
              wav: str | Path,
              video_bitrate: str = '1M',
              audio_bitrate: str = '128K',
              report: Report = noop_report,
              audio_only: bool = False) -> tuple[Path | None, Path]:
    """Produce ``proxy`` (720p) and ``wav`` (16 kHz mono) from ``source``.

    ``audio_only`` skips the proxy and returns ``(None, wav)``. The proxy
    exists for editor scrubbing and nothing downstream of ASR reads it, so a
    measurement run has no reason to pay for it. On 4K AV1 that is not a
    small saving: decoding 87 minutes of 4K60 AV1 to build an artefact that is
    then ignored costs over an hour of wall clock.
    """
    source = Path(source)
    if not source.is_file():
        raise TranscodeError(f'source not found: {source}')

    proxy = Path(proxy)
    wav = Path(wav)

    video_cmd = None
    if not audio_only:
        encoder = pick_video_encoder(report)
        report(f'video encoder: {encoder}')
        video_cmd = [
            select_ffmpeg(report), '-y', '-i', str(source),
            '-c:v', encoder, '-b:v', video_bitrate,
            '-c:a', 'aac', '-b:a', audio_bitrate,
            '-vf', 'scale=-1:720',
            str(proxy),
        ]
    else:
        report('audio only: skipping the 720p proxy')

    audio_cmd = [
        select_ffmpeg(report), '-y', '-i', str(source),
        '-vn', '-c:a', 'pcm_s16le', '-ac', '1', '-ar', '16000',
        str(wav),
    ]

    for cmd, target, label in (
        (video_cmd, proxy, 'proxy'),
        (audio_cmd, wav, 'audio'),
    ):
        if cmd is None:
            continue
        report(f'transcoding {label}')
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = '\n'.join((proc.stderr or '').strip().splitlines()[-8:])
            raise TranscodeError(
                f'{label} transcode failed (rc={proc.returncode}):\n{tail}'
            )
        if not target.is_file():
            raise TranscodeError(f'{label} transcode produced no output: {target}')
        size_mb = target.stat().st_size / 1048576
        report(f'{label} done: {target.name} ({size_mb:.1f} MB)')

    return (None if video_cmd is None else proxy), wav


def transcode_stage(job_id: int,
                    report: Report = noop_report,
                    paths=None,
                    source: str | Path | None = None,
                    **_ignored) -> tuple[Path, Path]:
    """Entry point used by the web runner."""
    if paths is None:
        raise ValueError('transcode stage requires paths')
    if source is None:
        source = paths.source
    if source is None:
        raise ValueError('no source file to transcode')
    paths.require('proxy', 'wav')
    return transcode(source, paths.proxy, paths.wav, report=report)
