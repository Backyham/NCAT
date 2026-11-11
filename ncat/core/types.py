"""Domain types shared by the CLI, API and tests.

The types are deliberately plain dataclasses: they are cheap to construct in
tests, easy to serialize to JSONL and do not tie the core to a web framework
or an ORM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProjectStage(StrEnum):
    INTAKE = "intake"
    DRAFTING = "drafting"
    REVIEW = "review"
    APPROVED = "approved"
    DELIVERED = "delivered"
    ARCHIVED = "archived"


class SegmentStatus(StrEnum):
    EMPTY = "empty"
    MT_SUGGESTED = "mt_suggested"
    TM_SUGGESTED = "tm_suggested"
    TRANSLATED = "translated"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class TermType(StrEnum):
    PROPER = "proper"
    TERM = "term"
    ABBREVIATION = "abbreviation"


class TermStatus(StrEnum):
    PREFERRED = "preferred"
    SUGGESTED = "suggested"
    DEPRECATED = "deprecated"


class QASeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


class QAStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    IGNORED = "ignored"
    FIXED = "fixed"


class RenderStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class Source:
    id: str
    project_id: str
    path: str
    proxy_path: str | None = None
    audio_path: str | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    language: str | None = None
    status: str = "pending"
    checksum: str | None = None


@dataclass
class Project:
    id: str
    name: str
    source_id: str | None = None
    source_lang: str = "en"
    target_langs: list[str] = field(default_factory=lambda: ["zh-Hans"])
    stage: ProjectStage = ProjectStage.INTAKE
    pause_for_transcript_review: bool = True
    quality: str = "standard"
    style_template: str = "default"
    owner: str | None = None
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class RunStep:
    name: str
    status: StepStatus = StepStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    progress: float = 0.0
    message: str = ""
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class Run:
    id: str
    project_id: str
    status: RunStatus = RunStatus.QUEUED
    recipe: str = "standard"
    steps: list[RunStep] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str | None = None
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class Segment:
    id: str
    project_id: str
    order: int
    source: str
    target: str = ""
    source_hash: str = ""
    status: SegmentStatus = SegmentStatus.EMPTY
    locked: bool = False
    tm_match: dict[str, Any] | None = None
    term_hits: list[str] = field(default_factory=list)
    notes: str = ""
    revision: int = 0
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class Cue:
    id: str
    project_id: str
    segment_id: str | None
    order: int
    start_ms: int | None = None
    end_ms: int | None = None
    source_text: str = ""
    target_text: str = ""
    needs_timing: bool = False
    timing_confidence: float | None = None
    alignment_method: str | None = None
    cps_source: float | None = None
    cps_target: float | None = None
    line_length: int | None = None
    lines: int | None = None
    style: str = "default"
    locked: bool = False
    revision: int = 0


@dataclass
class Word:
    index: int
    text: str
    start_ms: int
    end_ms: int
    normalized: str = ""
    confidence: float | None = None
    speaker: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Transcript:
    source_id: str
    language: str
    backend: str
    model: str
    text: str = ""
    words: list[Word] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int | None = None
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Alignment:
    cue_id: str
    first_word: int | None = None
    last_word: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    method: str = "lcs_lis_regularize"
    confidence: float | None = None
    needs_timing: bool = False


@dataclass
class TMUnit:
    id: str
    source: str
    target: str
    source_lang: str
    target_lang: str
    source_hash: str
    origin_project: str | None = None
    origin_segment: str | None = None
    approved_at: str | None = None
    usage_count: int = 0
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class Term:
    id: str
    source: str
    target: str
    source_lang: str
    target_lang: str
    term_type: TermType = TermType.TERM
    status: TermStatus = TermStatus.SUGGESTED
    forbidden: bool = False
    context: str = ""
    origin: str = ""
    concept_id: str | None = None
    usage_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class QAItem:
    id: str
    project_id: str
    kind: str
    severity: QASeverity
    message: str
    segment_id: str | None = None
    cue_id: str | None = None
    evidence: str = ""
    suggestion: str = ""
    status: QAStatus = QAStatus.OPEN
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class Render:
    id: str
    project_id: str
    preset: str
    lang: str
    target_lang: str | None = None
    path: str | None = None
    status: RenderStatus = RenderStatus.QUEUED
    changed_since: str | None = None
    checksum: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
