"""Stage contracts and the path layout for one job.

Two things live here because everything else depends on them:

* the ordered stage list, which the web UI renders as its pipeline rail and
  the runner iterates over;
* ``JobPaths``, the single place that decides where a job's artifacts go.

Every stage is a plain function taking ``(job_id, report, paths, ...)``.
No stage imports another stage and no stage knows about HTTP, so the same
code runs three ways: as ``python -m pipeline.<stage>``, from ``main.py``
on the CLI, and from a FastAPI background thread.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Stages run in this order. 'segment' covers cue building from the ASR
# word timings plus the bilingual merge; 'style' writes the final ASS.
STAGES: tuple[str, ...] = (
    'download',
    'transcode',
    'transcribe',
    'segment',
    'translate',
    'style',
    'render',
)

STAGE_LABELS: dict[str, str] = {
    'download': 'Get media',
    'transcode': 'Prepare media',
    'transcribe': 'Transcribe',
    'segment': 'Segment',
    'translate': 'Translate',
    'style': 'Style',
    'render': 'Render',
}

# A stage reports human-readable progress while it runs.
Report = Callable[[str], None]


def noop_report(_: str) -> None:
    """Default report sink, for CLI use where stdout is already the log."""


@dataclass
class JobPaths:
    """Every path a job touches, derived from one job directory.

    The old flat scripts wrote ``<name>.txt`` / ``<name>.json`` /
    ``<name>-720p.mp4`` into whatever directory the user happened to be
    standing in. Concentrating that here is what lets a job be a
    self-contained directory the web UI can serve as artifacts.
    """

    root: Path
    source: Path | None = None        # downloaded or uploaded original
    proxy: Path | None = None         # 720p H.264 preview
    wav: Path | None = None           # 16 kHz mono PCM for ASR
    transcript_txt: Path | None = None
    transcript_json: Path | None = None   # VoiceBase-style word timings
    cues_json: Path | None = None     # segmented cues, one object per line
    ass: Path | None = None           # final bilingual ASS
    burned: Path | None = None        # rendered video with subs burned in
    segments_txt: Path | None = None  # human-editable bilingual source

    _extra: dict[str, Path] = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, root: str | os.PathLike[str], stem: str = 'job',
              source: str | os.PathLike[str] | None = None) -> 'JobPaths':
        r = Path(root).expanduser().resolve()
        r.mkdir(parents=True, exist_ok=True)
        paths = cls(
            root=r,
            proxy=r / f'{stem}-720p.mp4',
            wav=r / f'{stem}.wav',
            transcript_txt=r / f'{stem}.txt',
            transcript_json=r / f'{stem}.json',
            cues_json=r / f'{stem}.cues.json',
            ass=r / f'{stem}.ass',
            burned=r / f'{stem}.burned.mp4',
            segments_txt=r / f'{stem}.txt.seg',
        )
        if source:
            paths.set_source(source)
        return paths

    def set_source(self, path: str | os.PathLike[str]) -> Path:
        self.source = Path(path).expanduser().resolve()
        return self.source

    def extra(self, key: str, suffix: str) -> Path:
        """Named sidecar artifact, e.g. ``extra('mt_raw', '.json')``."""
        if key not in self._extra:
            self._extra[key] = self.root / f'{key}{suffix}'
        return self._extra[key]

    def require(self, *attrs: str) -> None:
        missing = [a for a in attrs if getattr(self, a, None) is None]
        if missing:
            raise ValueError(f'JobPaths missing: {", ".join(missing)}')

    def __fspath__(self) -> str:
        return str(self.root)


def build_paths(root: str | os.PathLike[str],
                source: str | os.PathLike[str] | None = None) -> JobPaths:
    """Convenience wrapper used by the web layer."""
    return JobPaths.build(root, stem='job', source=source)
