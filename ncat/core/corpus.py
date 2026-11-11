"""Corpus/TM mining helpers used by the CLI and tests."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Iterable

from .assets.tm import write_units
from .project import new_id, now_iso
from .segment import load_bilingual_pair
from .subs.ass import parse_ass
from .subs.model import cues_from_ass
from .text import source_hash
from .types import TMUnit


def units_from_pairs(pairs: Iterable[tuple[str, str]], *,
                     source_lang: str = "en",
                     target_lang: str = "zh-Hans",
                     origin_project: str | None = None) -> list[TMUnit]:
    units = []
    for source, target in pairs:
        source, target = source.strip(), target.strip()
        if not source or not target:
            continue
        units.append(TMUnit(
            id=new_id("tm"), source=source, target=target,
            source_lang=source_lang, target_lang=target_lang,
            source_hash=source_hash(source), origin_project=origin_project,
            created_at=now_iso(), updated_at=now_iso(),
        ))
    return units


def units_from_ass(path: str | Path, *, origin_project: str | None = None,
                   source_lang: str = "en",
                   target_lang: str = "zh-Hans") -> list[TMUnit]:
    document = parse_ass(Path(path))
    cues = cues_from_ass(document, origin_project or "corpus",
                         bilingual_only=True)
    return units_from_pairs(
        ((cue.source_text, cue.target_text) for cue in cues),
        source_lang=source_lang, target_lang=target_lang,
        origin_project=origin_project,
    )


def build_corpus(*, source_path: str | Path | None = None,
                 target_path: str | Path | None = None,
                 ass_path: str | Path | None = None,
                 source_lang: str = "en",
                 target_lang: str = "zh-Hans",
                 origin_project: str | None = None) -> list[TMUnit]:
    if source_path and target_path:
        return units_from_pairs(
            load_bilingual_pair(source_path, target_path),
            source_lang=source_lang, target_lang=target_lang,
            origin_project=origin_project,
        )
    if ass_path:
        return units_from_ass(
            ass_path, origin_project=origin_project,
            source_lang=source_lang, target_lang=target_lang,
        )
    raise ValueError("corpus requires --source/--target or --ass")


def split_units(units: list[TMUnit], *, test_ratio: float = 0.1,
                seed: int = 13) -> tuple[list[TMUnit], list[TMUnit]]:
    shuffled = list(units)
    random.Random(seed).shuffle(shuffled)
    test_size = max(1, int(len(shuffled) * test_ratio)) if shuffled else 0
    return shuffled[test_size:], shuffled[:test_size]


def write_manifest(path: str | Path, units: Iterable[TMUnit]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for unit in units:
            handle.write(json.dumps({
                "source": unit.source, "target": unit.target,
                "source_lang": unit.source_lang,
                "target_lang": unit.target_lang,
            }, ensure_ascii=False) + "\n")
    return path


def build_and_write(*, out: str | Path, train: bool = True,
                    test_ratio: float = 0.1, **kwargs) -> dict:
    units = build_corpus(**kwargs)
    out_path = Path(out)
    train_units, test_units = split_units(units, test_ratio=test_ratio)
    write_units(out_path, units)
    result = {"units": len(units), "out": str(out_path)}
    if train:
        train_path = out_path.with_name(out_path.stem + ".train.jsonl")
        test_path = out_path.with_name(out_path.stem + ".test.jsonl")
        write_manifest(train_path, train_units)
        write_manifest(test_path, test_units)
        result.update(train=str(train_path), test=str(test_path),
                      train_units=len(train_units),
                      test_units=len(test_units))
    return result
