"""MT backend protocol; no model-specific code lives here."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class TMMatch:
    unit_id: str
    source: str
    target: str
    score: float
    match_type: str = "exact"


@dataclass
class TermHit:
    term_id: str
    source: str
    target: str
    status: str = "preferred"
    forbidden: bool = False


@dataclass
class SegmentInput:
    id: str
    source: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)
    tm_matches: list[TMMatch] = field(default_factory=list)
    term_hits: list[TermHit] = field(default_factory=list)


@dataclass
class MTRequest:
    source_lang: str
    target_lang: str
    segments: list[SegmentInput]
    document_brief: str = ""
    style: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class TargetSegment:
    id: str
    target: str
    confidence: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None


@dataclass
class MTResult:
    segments: list[TargetSegment]
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] | None = None


@dataclass
class MTCapabilities:
    supports_batch: bool = True
    supports_json: bool = True
    supports_stream: bool = False
    supports_system_prompt: bool = True
    context_window: int | None = None
    max_output_tokens: int | None = None
    target_languages: list[str] = field(default_factory=list)
    prompt_style: str = "chat"
    local: bool = True
    notes: str = ""


class MTBackend(Protocol):
    name: str
    capabilities: MTCapabilities

    def translate(self, request: MTRequest) -> MTResult:
        ...
