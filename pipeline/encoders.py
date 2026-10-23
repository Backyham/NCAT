"""Hardware/software video encoder selection.

Shared by the transcode and render stages so the probe cannot drift between
them -- an earlier divergence let transcode pick an encoder that render
could not use.

Two failure modes have to be told apart, as the legacy ``transcode.py`` did
with exit code 8:

* **exit 8** -- the encoder is not built into this ffmpeg. Skip silently;
  that is exactly what the code means.
* **any other non-zero** -- the encoder exists but could not be used. On a
  Tesla P40 with the R580 driver this is ``h264_nvenc``: it is listed, but
  R580 exposes NVENC API 13.0 while ffmpeg 9 demands 13.1. That is an
  actionable condition, so it is reported rather than quietly folded into
  "unavailable".

Requiring a real encode with non-empty output is what separates a working
encoder from one that merely exists. A presence check alone selects
h264_nvenc here and every subsequent transcode fails.
"""
from __future__ import annotations

import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .config import setting

# Preference order: hardware first, software as the guaranteed fallback.
VIDEO_ENCODERS: tuple[str, ...] = (
    'h264_nvenc', 'h264_qsv', 'h264_amf', 'libx264',
)
HARDWARE_ENCODERS = frozenset({'h264_nvenc', 'h264_qsv', 'h264_amf',
                               'h264_vaapi'})

# ffmpeg's exit status when the requested encoder/decoder does not exist.
ENCODER_ABSENT = 8


class EncoderError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    """Path to the ffmpeg binary to use.

    Overridable via ``NCAT_FFMPEG``. That exists because the system ffmpeg
    cannot use NVENC on a Pascal card under R580 (it demands NVENC API 13.1,
    the driver offers 13.0), and a private ffmpeg 8 build can -- see
    ``nvenc_workaround()``.
    """
    return setting('ffmpeg')


def ffmpeg_available() -> bool:
    from shutil import which
    binary = ffmpeg_bin()
    return which(binary) is not None or Path(binary).is_file()


# Most specific first: ffmpeg emits the root cause early and a generic
# "Terminating thread with return code -22" at the end, so taking the last
# match would report the symptom and hide the cause.
_REASON_PATTERNS: tuple[str, ...] = (
    'Driver does not support',
    'minimum required',
    'Unknown encoder',
    'Could not open encoder',
    'Error while opening encoder',
    'No such encoder',
    'not found',
    'Failed to open',
)


def _short_reason(stderr: str | None, limit: int = 160) -> str:
    """Pull the root-cause line out of ffmpeg's stderr.

    ffmpeg is verbose and buries the useful detail under progress output.
    For NVENC on R580 the line that matters is "Driver does not support the
    required nvenc API version. Required: 13.1 Found: 13.0", which appears
    several lines before the final thread-termination noise.
    """
    if not stderr:
        return 'no diagnostic output'
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for pattern in _REASON_PATTERNS:
        for line in lines:
            if pattern in line:
                # Drop ffmpeg's "[h264_nvenc @ 0x...]" prefix.
                cleaned = line.split('] ', 1)[-1] if '] ' in line else line
                return cleaned[:limit]
    return (lines[-1] if lines else 'unknown error')[:limit]


@lru_cache(maxsize=1)
def pick_video_encoder(report: Callable[[str], None] | None = None) -> str:
    """Return the first encoder that can really encode, reporting skips."""
    say = report or (lambda _msg: None)
    if not ffmpeg_available():
        raise EncoderError(
            'ffmpeg not found on PATH. On Arch:  sudo pacman -S ffmpeg'
        )

    binary = select_ffmpeg(report)
    rejections: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'probe.mp4'
        for encoder in VIDEO_ENCODERS:
            proc = subprocess.run(
                [binary, '-y', '-v', 'error', '-f', 'lavfi',
                 '-i', 'testsrc2=size=640x480',
                 '-frames:v', '20', '-c:v', encoder, str(out)],
                capture_output=True, text=True,
            )
            if proc.returncode == ENCODER_ABSENT:
                # Not built in -- expected, not worth reporting.
                rejections.append(f'{encoder}: not built into this ffmpeg')
                continue
            if proc.returncode != 0 or not out.is_file() \
                    or out.stat().st_size == 0:
                reason = _short_reason(proc.stderr)
                rejections.append(f'{encoder}: present but unusable ({reason})')
                if encoder in HARDWARE_ENCODERS:
                    say(f'note: {encoder} skipped -- {reason}')
                continue
            for line in rejections:
                say(f'note: {line}')
            return encoder

    raise EncoderError(
        'no usable H.264 encoder among '
        f'{", ".join(VIDEO_ENCODERS)}. '
        + '; '.join(rejections)
    )

_resolved_binary: str | None = None


def select_ffmpeg(report: Callable[[str], None] | None = None) -> str:
    """Resolve once and remember, so the report callback is not needed twice."""
    global _resolved_binary
    if _resolved_binary is None:
        _resolved_binary = _select_ffmpeg(report)
    return _resolved_binary


def _select_ffmpeg(report: Callable[[str], None] | None = None) -> str:
    """Pick the ffmpeg binary that can actually use the GPU encoder.

    The system ffmpeg may be fine for software encoding but unable to use
    NVENC: on a Pascal card with the R580 driver, ffmpeg 9 requires NVENC
    API 13.1 while the driver exposes 13.0, so the encoder is listed but
    rejected at open time. A 4K60 burn-in is then ~3x slower than it needs
    to be, and for a 40-minute video that is the difference between 35 and
    107 minutes.

    Strategy: honour ``NCAT_FFMPEG`` if set. Otherwise try the system
    ffmpeg; if no hardware encoder works there, try a private build in
    ``NCAT_FFMPEG_FALLBACK`` and use that if NVENC becomes available.
    """
    say = report or (lambda _msg: None)
    import os

    explicit = os.environ.get('NCAT_FFMPEG', '').strip()
    if explicit:
        return explicit

    system = ffmpeg_bin()
    if _hardware_encoder_works(system):
        return system

    say(f'note: no usable hardware encoder with {system} '
        f'(--nvenc rejected); trying a private build')
    fallback = os.environ.get('NCAT_FFMPEG_FALLBACK', '').strip()
    if fallback and Path(fallback).exists() and _hardware_encoder_works(fallback):
        say(f'note: using {fallback} for hardware encoding')
        return fallback
    return system


def _hardware_encoder_works(binary: str) -> bool:
    """True if ``binary`` can open a GPU encoder and emit real output."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'hw.mp4'
        for encoder in ('h264_nvenc', 'h264_vaapi', 'h264_qsv', 'h264_amf'):
            proc = subprocess.run(
                [binary, '-y', '-v', 'error', '-f', 'lavfi',
                 '-i', 'testsrc2=size=640x480', '-frames:v', '20',
                 '-c:v', encoder, str(out)],
                capture_output=True, text=True,
            )
            if proc.returncode == 0 and out.is_file() and out.stat().st_size > 0:
                return True
    return False
