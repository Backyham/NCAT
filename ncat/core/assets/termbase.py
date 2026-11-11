"""Termbase JSONL/CSV/TBX storage and matching."""
from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from ..mt.base import TermHit
from ..project import new_id, now_iso
from ..text import normalise_key
from ..types import Term, TermStatus, TermType


def _term_from_row(row: dict, *, source_lang: str = "en",
                   target_lang: str = "zh-Hans") -> Term:
    try:
        status = TermStatus(str(row.get("status", "suggested")))
    except ValueError:
        status = TermStatus.SUGGESTED
    try:
        term_type = TermType(str(row.get("term_type", "term")))
    except ValueError:
        term_type = TermType.TERM
    return Term(
        id=row.get("id") or new_id("term"),
        source=str(row.get("source", "")),
        target=str(row.get("target", "")),
        source_lang=str(row.get("source_lang") or source_lang),
        target_lang=str(row.get("target_lang") or target_lang),
        term_type=term_type, status=status,
        forbidden=bool(row.get("forbidden", False)),
        context=str(row.get("context", "")),
        origin=str(row.get("origin", "")),
        concept_id=row.get("concept_id"),
        usage_count=int(row.get("usage_count", 0)),
        created_at=row.get("created_at") or now_iso(),
        updated_at=row.get("updated_at") or now_iso(),
    )


def read_terms(path: str | Path) -> list[Term]:
    path = Path(path)
    if not path.is_file():
        return []
    terms = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            terms.append(_term_from_row(json.loads(line)))
    return terms


def write_terms(path: str | Path, terms: Iterable[Term]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for term in terms:
            handle.write(json.dumps({
                "id": term.id, "source": term.source, "target": term.target,
                "source_lang": term.source_lang,
                "target_lang": term.target_lang,
                "term_type": str(term.term_type), "status": str(term.status),
                "forbidden": term.forbidden, "context": term.context,
                "origin": term.origin, "concept_id": term.concept_id,
                "usage_count": term.usage_count,
                "created_at": term.created_at, "updated_at": term.updated_at,
            }, ensure_ascii=False) + "\n")
    temporary.replace(path)
    return path


def import_table(path: str | Path, *, source_lang: str = "en",
                 target_lang: str = "zh-Hans") -> list[Term]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows = [json.loads(line) for line in
                path.read_text(encoding="utf-8-sig").splitlines()
                if line.strip()]
    else:
        delimiter = "\t" if suffix in {".tsv", ".txt"} else ","
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if reader.fieldnames and "source" in reader.fieldnames:
                rows = list(reader)
            else:
                handle.seek(0)
                rows = [{"source": row[0],
                         "target": row[1] if len(row) > 1 else "",
                         "status": row[2] if len(row) > 2 else "suggested"}
                        for row in csv.reader(handle, delimiter=delimiter)
                        if row and row[0].strip()]
    terms = []
    for row in rows:
        if not str(row.get("source", "")).strip():
            continue
        terms.append(_term_from_row(row, source_lang=source_lang,
                                    target_lang=target_lang))
    return terms


def match_terms(source: str, terms: Iterable[Term], *,
                confirmed_only: bool = True, limit: int = 20) -> list[TermHit]:
    key = normalise_key(source)
    hits: list[TermHit] = []
    for term in terms:
        if confirmed_only and term.status != TermStatus.PREFERRED:
            continue
        source_key = normalise_key(term.source)
        if not source_key:
            continue
        if source_key in key:
            hits.append(TermHit(
                term_id=term.id, source=term.source, target=term.target,
                status=str(term.status), forbidden=term.forbidden))
            if len(hits) >= limit:
                break
    return hits


def read_tbx(path: str | Path) -> list[Term]:
    root = ET.parse(path).getroot()
    terms: list[Term] = []
    for entry in root.iter("termEntry"):
        concept = entry.attrib.get("id")
        for lang_set in entry.iter("langSet"):
            language = lang_set.attrib.get(
                "{http://www.w3.org/XML/1998/namespace}lang", "en")
            for tig in lang_set.iter("tig"):
                term_node = tig.find("term")
                if term_node is not None and term_node.text:
                    terms.append(Term(
                        id=f"{concept or new_id('term')}:{len(terms)}",
                        source=term_node.text,
                        target="",
                        source_lang=language,
                        target_lang=language,
                        concept_id=concept,
                        status=TermStatus.SUGGESTED,
                        created_at=now_iso(), updated_at=now_iso(),
                    ))
    return terms


def write_tbx(terms: Iterable[Term], path: str | Path) -> Path:
    root = ET.Element("tbx", {"type": "TBX-Basic", "style": "dca",
                              "version": "3.0"})
    body = ET.SubElement(root, "body")
    for term in terms:
        entry = ET.SubElement(body, "termEntry", {"id": term.concept_id
                                                  or term.id})
        lang_set = ET.SubElement(entry, "langSet", {
            "{http://www.w3.org/XML/1998/namespace}lang": term.source_lang})
        tig = ET.SubElement(lang_set, "tig")
        node = ET.SubElement(tig, "term")
        node.text = term.source
        if term.target:
            target_set = ET.SubElement(entry, "langSet", {
                "{http://www.w3.org/XML/1998/namespace}lang":
                    term.target_lang})
            target_tig = ET.SubElement(target_set, "tig")
            target_node = ET.SubElement(target_tig, "term")
            target_node.text = term.target
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path
