"""Media probing and transcode helpers.

The legacy transcode step produced a 720p proxy and a 16 kHz mono WAV. This
module keeps that contract, but makes the encoder choice explicit and testable
and never writes a dangling ffmpeg option.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Iterable

ENCODER_ABSENT = 8
VIDEO_ENCODERS: tuple[str, ...] = (
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
    "h264_videotoolbox",
    "libx264",
)


class MediaError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: str
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    has_video: bool
    has_audio: bool
    size_bytes: int


@dataclass
class TranscodeResult:
    info: MediaInfo
    proxy: Path
    wav: Path
    encoder: str


def ffmpeg_bin() -> str:
    return os.environ.get("NCAT_FFMPEG", "").strip() or "ffmpeg"


def ffprobe_bin() -> str:
    return os.environ.get("NCAT_FFPROBE", "").strip() or "ffprobe"


def ffmpeg_available() -> bool:
    binary = ffmpeg_bin()
    return bool(shutil.which(binary) or Path(binary).is_file())


def ffprobe_available() -> bool:
    binary = ffprobe_bin()
    return bool(shutil.which(binary) or Path(binary).is_file())


def file_checksum(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """SHA-256 of a file, streamed so large videos are not loaded into RAM."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fps(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None


def probe_media(path: str | Path, *, ffprobe: str | None = None) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"media file not found: {path}")
    binary = ffprobe or ffprobe_bin()
    proc = subprocess.run(
        [
            binary, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as error:
        raise MediaError(f"ffprobe returned invalid JSON for {path}") from error

    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = payload.get("format", {}).get("duration")
    try:
        duration_ms = int(round(float(duration) * 1000)) if duration else None
    except (TypeError, ValueError):
        duration_ms = None

    return MediaInfo(
        path=str(path),
        duration_ms=duration_ms,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
        if video else None,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        has_video=video is not None,
        has_audio=audio is not None,
        size_bytes=path.stat().st_size,
    )


def _short_reason(stderr: str | None, limit: int = 160) -> str:
    if not stderr:
        return "no diagnostic output"
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return (lines[-1] if lines else "unknown error")[:limit]


def classify_encoder_probe(returncode: int, output: str | Path | None,
                           stderr: str | None = None) -> tuple[bool, str]:
    """Classify an encoder probe without conflating absence with breakage.

    ffmpeg uses exit code 8 when an encoder/codec is not available in this
    build. That is an expected skip. Any other non-zero exit means the
    encoder exists but could not be opened/used (driver, API version, GPU
    permissions), which is worth reporting separately. A zero exit still
    requires a non-empty output file: some wrapper scripts return 0 after a
    no-op, and selecting such an encoder would make every later transcode
    fail.
    """
    if returncode == ENCODER_ABSENT:
        return False, "not built into this ffmpeg"
    if returncode != 0:
        return False, f"present but unusable ({_short_reason(stderr)})"
    if output is None:
        return False, "encoder returned success but produced no output"
    path = Path(output)
    if not path.is_file() or path.stat().st_size == 0:
        return False, "encoder returned success but produced no output"
    return True, ""


def pick_video_encoder(ffmpeg: str | None = None,
                       candidates: Iterable[str] = VIDEO_ENCODERS) -> str:
    """Return the first encoder that can actually encode a short probe."""
    binary = ffmpeg or ffmpeg_bin()
    return _pick_video_encoder(binary, tuple(candidates))


@lru_cache(maxsize=8)
def _pick_video_encoder(binary: str, candidates: tuple[str, ...]) -> str:
    if not (shutil.which(binary) or Path(binary).is_file()):
        raise MediaError(
            f"ffmpeg not found: {binary}. Set NCAT_FFMPEG or install ffmpeg."
        )
    reasons: list[str] = []
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "probe.mp4"
        for encoder in candidates:
            if output.exists():
                output.unlink()
            proc = subprocess.run(
                [
                    binary, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=320x180:rate=24",
                    "-frames:v", "8", "-c:v", encoder, str(output),
                ],
                capture_output=True, text=True,
            )
            usable, reason = classify_encoder_probe(
                proc.returncode, output, proc.stderr)
            if usable:
                return encoder
            reasons.append(f"{encoder}: {reason} (exit {proc.returncode})")
    raise MediaError(
        "no usable H.264 encoder among "
        + ", ".join(candidates)
        + "; "
        + "; ".join(reasons)
    )


def clear_encoder_cache() -> None:
    _pick_video_encoder.cache_clear()


def build_proxy_command(source: str | Path, output: str | Path, *,
                        encoder: str, height: int = 720,
                        video_bitrate: str = "1M",
                        audio_bitrate: str = "128k",
                        ffmpeg: str | None = None) -> list[str]:
    return [
        ffmpeg or ffmpeg_bin(),
        "-y", "-v", "error",
        "-i", str(source),
        "-c:v", encoder,
        "-b:v", video_bitrate,
        "-vf", f"scale=-2:{height}",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(output),
    ]


def build_audio_command(source: str | Path, output: str | Path, *,
                        sample_rate: int = 16000,
                        ffmpeg: str | None = None) -> list[str]:
    return [
        ffmpeg or ffmpeg_bin(),
        "-y", "-v", "error",
        "-i", str(source),
        "-vn",
        "-c:a", "pcm_s16le",
        "-ac", "1",
        "-ar", str(sample_rate),
        str(output),
    ]


def _run(command: list[str], label: str) -> None:
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"{label} failed (exit {proc.returncode}): "
                         f"{proc.stderr.strip()}")


def transcode_source(source: str | Path, output_dir: str | Path, *,
                     stem: str | None = None,
                     height: int = 720,
                     encoder: str | None = None,
                     ffmpeg: str | None = None) -> TranscodeResult:
    source = Path(source)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or source.stem
    proxy = output_dir / f"{stem}-{height}p.mp4"
    wav = output_dir / f"{stem}.wav"
    info = probe_media(source)
    chosen = encoder or pick_video_encoder(ffmpeg)

    temporary = Path(tempfile.mkdtemp(prefix=".ncat-", dir=output_dir))
    try:
        temporary_proxy = temporary / proxy.name
        temporary_wav = temporary / wav.name
        _run(build_proxy_command(source, temporary_proxy, encoder=chosen,
                                 height=height, ffmpeg=ffmpeg),
             "video transcode")
        _run(build_audio_command(source, temporary_wav, ffmpeg=ffmpeg),
             "audio transcode")
        os.replace(temporary_proxy, proxy)
        os.replace(temporary_wav, wav)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return TranscodeResult(info=info, proxy=proxy, wav=wav, encoder=chosen)


def _escape_filter_path(path: str | Path) -> str:
    value = str(Path(path).resolve())
    value = value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"'{value}'"


def build_burn_command(source: str | Path, ass: str | Path,
                       output: str | Path, *,
                       preset: str = "original",
                       encoder: str = "libx264",
                       crf: int = 20,
                       ffmpeg: str | None = None) -> list[str]:
    filters = [f"subtitles={_escape_filter_path(ass)}"]
    if preset == "youtube_1080p":
        filters.append("scale=-2:1080")
    elif preset == "bilibili_2160p":
        filters.append("scale=-2:2160")
    quality = (["-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
               if "nvenc" in encoder else
               ["-crf", str(crf), "-preset", "medium"])
    return [
        ffmpeg or ffmpeg_bin(),
        "-y", "-v", "error",
        "-i", str(source),
        "-vf", ",".join(filters),
        "-c:v", encoder,
        *quality,
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ]


def render_subtitles(source: str | Path, ass: str | Path,
                     output: str | Path, **kwargs) -> Path:
    source, ass, output = Path(source), Path(ass), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + ".partial" + output.suffix)
    command = build_burn_command(source, ass, temporary, **kwargs)
    try:
        _run(command, "subtitle render")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output
