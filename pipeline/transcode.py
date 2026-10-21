"""Transcode stage: build the preview proxy and the ASR audio track.

Rewritten from the old top-level ``transcode.py``. Behaviour is the same
(720p 1 Mbps H.264 proxy + 16-bit 16 kHz mono PCM WAV), but the encoder
probe now happens once and reports what it chose, instead of silently
trying four codecs in order on every invocation.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .paths import Report, noop_report

# Tried in order. h264_nvenc first because the box has a Tesla P40 and
# hardware encode frees the CPU for ASR running alongside.
VIDEO_ENCODERS = ('h264_nvenc', 'h264_qsv', 'h264_amf', 'libx264')


class TranscodeError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which('ffmpeg') is not None


@lru_cache(maxsize=1)
def pick_video_encoder() -> str:
    """Return the first usable H.264 encoder, or raise if none work."""
    if not ffmpeg_available():
        raise TranscodeError(
            'ffmpeg not found on PATH. On Arch:  sudo pacman -S ffmpeg'
        )
    null = 'NUL' if __import__('os').name == 'nt' else '/dev/null'
    for encoder in VIDEO_ENCODERS:
        probe = subprocess.run(
            ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'nullsrc=s=hd720', '-t', '1',
             '-c:v', encoder, '-f', 'mp4', null],
            capture_output=True,
        )
        # rc 8 means "no such encoder"; anything else means it initialised.
        if probe.returncode != 8:
            return encoder
    raise TranscodeError(
        f'no usable H.264 encoder among {VIDEO_ENCODERS}'
    )


def transcode(source: str | Path,
              proxy: str | Path,
              wav: str | Path,
              video_bitrate: str = '1M',
              audio_bitrate: str = '128K',
              report: Report = noop_report) -> tuple[Path, Path]:
    """Produce ``proxy`` (720p) and ``wav`` (16 kHz mono) from ``source``."""
    source = Path(source)
    if not source.is_file():
        raise TranscodeError(f'source not found: {source}')

    proxy = Path(proxy)
    wav = Path(wav)

    encoder = pick_video_encoder()
    report(f'video encoder: {encoder}')

    video_cmd = [
        'ffmpeg', '-y', '-i', str(source),
        '-c:v', encoder, '-b:v', video_bitrate,
        '-c:a', 'aac', '-b:a', audio_bitrate,
        '-vf', 'scale=-1:720',
        str(proxy),
    ]
    audio_cmd = [
        'ffmpeg', '-y', '-i', str(source),
        '-vn', '-c:a', 'pcm_s16le', '-ac', '1', '-ar', '16000',
        str(wav),
    ]

    for cmd, target, label in (
        (video_cmd, proxy, 'proxy'),
        (audio_cmd, wav, 'audio'),
    ):
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

    return proxy, wav


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog='python -m pipeline.transcode',
        description='NCAT stage 2/7: 720p proxy + 16 kHz mono WAV.',
    )
    p.add_argument('source', help='Source video or audio file')
    p.add_argument('--out-dir', default=None,
                   help='Output directory (default: alongside the source)')
    p.add_argument('--stem', default=None,
                   help='Output filename stem (default: source filename)')
    args = p.parse_args(argv)

    src = Path(args.source).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else src.parent
    stem = args.stem or src.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        transcode(src, out_dir / f'{stem}-720p.mp4', out_dir / f'{stem}.wav',
                  report=lambda m: print(m, flush=True))
    except (TranscodeError, subprocess.SubprocessError) as exc:
        print(f'transcode failed: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
