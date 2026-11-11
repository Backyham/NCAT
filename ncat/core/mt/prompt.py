"""Prompt construction and response parsing for MT suggestions."""
from __future__ import annotations

import json
import re
from typing import Iterable

from .base import MTRequest, TargetSegment

SYSTEM_PROMPT = """You are a professional subtitle translator.
Translate every source segment into the requested target language.
Preserve segment order and output exactly one line per input segment in the
form `SEGMENT_ID<TAB>TRANSLATION`. Do not add explanations or numbering.
Follow the supplied terminology and use translation-memory matches as context.
"""


def build_messages(request: MTRequest) -> list[dict[str, str]]:
    lines = []
    if request.document_brief:
        lines.append(f"Document brief: {request.document_brief}")
    if request.style:
        lines.append(f"Style: {request.style}")
    lines.append(f"Target language: {request.target_lang}")
    terms = []
    for segment in request.segments:
        for hit in segment.term_hits:
            suffix = " [FORBIDDEN]" if hit.forbidden else ""
            terms.append(f"- {hit.source} -> {hit.target}{suffix}")
    if terms:
        lines.append("Terminology (must follow):")
        lines.extend(dict.fromkeys(terms))
    tms = []
    for segment in request.segments:
        for match in segment.tm_matches:
            if match.match_type in {"exact", "fuzzy"}:
                tms.append(f"- {match.source} => {match.target} ({match.score:.2f})")
    if tms:
        lines.append("Translation memory:")
        lines.extend(dict.fromkeys(tms))
    lines.append("Segments:")
    for segment in request.segments:
        lines.append(f"{segment.id}\t{segment.source}")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_response(text: str, request: MTRequest) -> list[TargetSegment]:
    ids = [segment.id for segment in request.segments]
    parsed: dict[str, str] = {}
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            items = payload.get("translations") or payload.get("segments") or []
            for item in items:
                if isinstance(item, dict) and "id" in item:
                    parsed[str(item["id"])] = str(item.get("target", ""))
        elif isinstance(payload, list):
            for index, item in enumerate(payload):
                if isinstance(item, dict):
                    key = str(item.get("id", ids[index] if index < len(ids) else index))
                    parsed[key] = str(item.get("target", ""))
    except json.JSONDecodeError:
        for line in cleaned.splitlines():
            if "\t" in line:
                key, value = line.split("\t", 1)
            elif ":" in line:
                key, value = line.split(":", 1)
            else:
                continue
            key = key.strip().strip("`")
            if key in ids:
                parsed[key] = value.strip()
    results: list[TargetSegment] = []
    for index, segment in enumerate(request.segments):
        target = parsed.get(segment.id, "")
        warnings = []
        if not target:
            warnings.append("missing_translation")
        if target and target.strip() == segment.source.strip():
            warnings.append("source_echo")
        results.append(TargetSegment(
            id=segment.id, target=target,
            confidence=1.0 if target and "missing_translation" not in warnings
            else 0.0,
            warnings=warnings,
        ))
    return results
