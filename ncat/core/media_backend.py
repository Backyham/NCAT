"""Pluggable media execution backend.

The HTTP API and core actions talk to this contract instead of calling
``ffmpeg`` directly. ``FfmpegBackend`` runs local subprocesses; a future
remote service or browser-backed implementation can satisfy the same
protocol without changing callers.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .media import (
    MediaError, MediaInfo, TranscodeResult, build_audio_command,
    ffmpeg_available, ffprobe_available, probe_media, render_subtitles,
    transcode_source,
)


@dataclass(frozen=True)
class MediaCapabilities:
    """Capabilities advertised by a media backend."""

    probe: bool = True
    transcode: bool = True
    extract_audio: bool = True
    render: bool = True
    hardware_encoders: tuple[str, ...] = ()
    remote: bool = False


@runtime_checkable
class MediaBackend(Protocol):
    """Interface for proxy/audio/subtitle media operations."""

    name: str

    def capabilities(self) -> MediaCapabilities: ...

    def probe(self, path: str | Path) -> MediaInfo: ...

    def transcode(self, source: str | Path, output_dir: str | Path, *,
                  stem: str | None = None, height: int = 720,
                  encoder: str | None = None) -> TranscodeResult: ...

    def extract_audio(self, source: str | Path, output: str | Path, *,
                      sample_rate: int = 16000) -> Path: ...

    def render(self, source: str | Path, ass: str | Path,
               output: str | Path, *, preset: str = "original",
               encoder: str = "libx264", crf: int = 20) -> Path: ...


class FfmpegBackend:
    """Local ffmpeg/ffprobe subprocess backend."""

    name = "ffmpeg"

    def capabilities(self) -> MediaCapabilities:
        ffmpeg = ffmpeg_available()
        return MediaCapabilities(
            probe=ffprobe_available(),
            transcode=ffmpeg,
            extract_audio=ffmpeg,
            render=ffmpeg,
        )

    def probe(self, path: str | Path) -> MediaInfo:
        return probe_media(path)

    def transcode(self, source: str | Path, output_dir: str | Path, *,
                  stem: str | None = None, height: int = 720,
                  encoder: str | None = None) -> TranscodeResult:
        return transcode_source(
            source, output_dir, stem=stem, height=height, encoder=encoder,
        )

    def extract_audio(self, source: str | Path, output: str | Path, *,
                      sample_rate: int = 16000) -> Path:
        source = Path(source)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.stem + ".partial" + output.suffix)
        command = build_audio_command(source, temporary,
                                      sample_rate=sample_rate)
        process = subprocess.run(command, capture_output=True, text=True)
        if process.returncode != 0:
            temporary.unlink(missing_ok=True)
            raise MediaError(
                f"audio extract failed (exit {process.returncode}): "
                f"{process.stderr.strip()}")
        os.replace(temporary, output)
        return output

    def render(self, source: str | Path, ass: str | Path,
               output: str | Path, *, preset: str = "original",
               encoder: str = "libx264", crf: int = 20) -> Path:
        return render_subtitles(
            source, ass, output, preset=preset, encoder=encoder, crf=crf,
        )


def get_media_backend(name: str | None = None) -> MediaBackend:
    """Return the configured media backend (local ffmpeg by default)."""
    selected = (name or os.environ.get("NCAT_MEDIA_BACKEND")
                or "ffmpeg").strip().lower()
    if selected in {"ffmpeg", "local"}:
        return FfmpegBackend()
    raise MediaError(f"unknown media backend: {selected}")
