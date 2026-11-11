"""Translation memory JSONL/TMX storage and matching."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from ..mt.base import TMMatch
from ..text import normalise_key, source_hash
from ..types import TMUnit


def read_units(path: str | Path) -> list[TMUnit]:
    path = Path(path)
    if not path.is_file():
        return []
    units: list[TMUnit] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        units.append(TMUnit(
            id=row.get("id") or source_hash(str(row.get("source", "")))[:16],
            source=str(row.get("source", "")),
            target=str(row.get("target", "")),
            source_lang=str(row.get("source_lang", "en")),
            target_lang=str(row.get("target_lang", "zh-Hans")),
            source_hash=str(row.get("source_hash")
                            or source_hash(str(row.get("source", "")))),
            origin_project=row.get("origin_project"),
            origin_segment=row.get("origin_segment"),
            approved_at=row.get("approved_at"),
            usage_count=int(row.get("usage_count", 0)),
            alternatives=row.get("alternatives", []),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        ))
    return units


def write_units(path: str | Path, units: Iterable[TMUnit]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for unit in units:
            handle.write(json.dumps({
                "id": unit.id, "source": unit.source, "target": unit.target,
                "source_lang": unit.source_lang,
                "target_lang": unit.target_lang,
                "source_hash": unit.source_hash,
                "origin_project": unit.origin_project,
                "origin_segment": unit.origin_segment,
                "approved_at": unit.approved_at,
                "usage_count": unit.usage_count,
                "alternatives": unit.alternatives,
                "created_at": unit.created_at,
                "updated_at": unit.updated_at,
            }, ensure_ascii=False) + "\n")
    temporary.replace(path)
    return path


def find_matches(source: str, units: Iterable[TMUnit], *,
                 threshold: float = 0.75, limit: int = 5) -> list[TMMatch]:
    key = normalise_key(source)
    if not key:
        return []
    key_hash = source_hash(source)
    scored: list[tuple[float, str, TMUnit]] = []
    for unit in units:
        if unit.source_hash == key_hash or normalise_key(unit.source) == key:
            scored.append((1.0, "exact", unit))
            continue
        ratio = SequenceMatcher(None, key, normalise_key(unit.source)).ratio()
        if ratio >= threshold:
            scored.append((ratio, "fuzzy", unit))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        TMMatch(unit_id=unit.id, source=unit.source, target=unit.target,
                score=round(score, 4), match_type=kind)
        for score, kind, unit in scored[:limit]
    ]


def read_tmx(path: str | Path) -> list[TMUnit]:
    root = ET.parse(path).getroot()
    units: list[TMUnit] = []
    for tu in root.iter("tu"):
        texts: dict[str, str] = {}
        for tuv in tu.iter("tuv"):
            language = tuv.attrib.get("{http://www.w3.org/XML/1998/namespace}lang") \
                or tuv.attrib.get("lang") or ""
            seg = tuv.find("seg")
            if seg is not None and seg.text:
                texts[language] = seg.text
        if len(texts) < 2:
            continue
        (source_lang, source), (target_lang, target) = list(texts.items())[:2]
        units.append(TMUnit(
            id=tu.attrib.get("tuid") or source_hash(source)[:16],
            source=source, target=target,
            source_lang=source_lang, target_lang=target_lang,
            source_hash=source_hash(source),
        ))
    return units


def write_tmx(units: Iterable[TMUnit], path: str | Path) -> Path:
    root = ET.Element("tmx", version="1.4")
    header = ET.SubElement(root, "header", {
        "creationtool": "NCAT", "segtype": "sentence",
        "adminlang": "en", "srclang": "en", "datatype": "plaintext",
    })
    body = ET.SubElement(root, "body")
    for unit in units:
        tu = ET.SubElement(body, "tu", {"tuid": unit.id})
        for lang, text in ((unit.source_lang, unit.source),
                           (unit.target_lang, unit.target)):
            tuv = ET.SubElement(tu, "tuv", {
                "{http://www.w3.org/XML/1998/namespace}lang": lang})
            seg = ET.SubElement(tuv, "seg")
            seg.text = text
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path
