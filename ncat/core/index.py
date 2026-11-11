"""SQLite/FTS index over the file-authoritative JSONL stores."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project import ProjectPaths, read_cues, read_segments


@dataclass
class IndexStatus:
    path: str
    exists: bool
    segments: int = 0
    cues: int = 0
    built_at: str | None = None


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def build_index(paths: ProjectPaths) -> IndexStatus:
    paths.index.parent.mkdir(parents=True, exist_ok=True)
    temporary = paths.index.with_suffix(".db.tmp")
    if temporary.exists():
        temporary.unlink()
    segments = read_segments(paths)
    cues = read_cues(paths)
    connection = _connect(temporary)
    try:
        connection.executescript("""
            CREATE TABLE segments(
                id TEXT PRIMARY KEY, ord INTEGER, source TEXT, target TEXT,
                status TEXT, locked INTEGER);
            CREATE TABLE cues(
                id TEXT PRIMARY KEY, ord INTEGER, start_ms INTEGER,
                end_ms INTEGER, source_text TEXT, target_text TEXT,
                style TEXT, locked INTEGER);
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        """)
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE fts USING fts5(kind, id, text)")
            fts = True
        except sqlite3.OperationalError:
            connection.execute(
                "CREATE TABLE fts(kind TEXT, id TEXT, text TEXT)")
            fts = False
        for segment in segments:
            connection.execute(
                "INSERT OR REPLACE INTO segments VALUES (?,?,?,?,?,?)",
                (segment.id, segment.order, segment.source, segment.target,
                 str(segment.status), int(segment.locked)))
            connection.execute(
                "INSERT INTO fts(kind,id,text) VALUES (?,?,?)",
                ("segment", segment.id,
                 f"{segment.source}\n{segment.target}"))
        for cue in cues:
            connection.execute(
                "INSERT OR REPLACE INTO cues VALUES (?,?,?,?,?,?,?,?)",
                (cue.id, cue.order, cue.start_ms, cue.end_ms,
                 cue.source_text, cue.target_text, cue.style,
                 int(cue.locked)))
            connection.execute(
                "INSERT INTO fts(kind,id,text) VALUES (?,?,?)",
                ("cue", cue.id, f"{cue.source_text}\n{cue.target_text}"))
        from .project import now_iso
        built_at = now_iso()
        connection.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
            ("built_at", built_at))
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, paths.index)
    return IndexStatus(str(paths.index), True, len(segments), len(cues),
                       built_at)


def index_status(paths: ProjectPaths) -> IndexStatus:
    if not paths.index.is_file():
        return IndexStatus(str(paths.index), False)
    connection = _connect(paths.index)
    try:
        segments = connection.execute(
            "SELECT COUNT(*) AS n FROM segments").fetchone()["n"]
        cues = connection.execute(
            "SELECT COUNT(*) AS n FROM cues").fetchone()["n"]
        row = connection.execute(
            "SELECT value FROM meta WHERE key='built_at'").fetchone()
        return IndexStatus(str(paths.index), True, segments, cues,
                           row["value"] if row else None)
    finally:
        connection.close()


def search_index(paths: ProjectPaths, query: str, *,
                 limit: int = 50) -> list[dict[str, Any]]:
    if not paths.index.is_file():
        raise FileNotFoundError(f"index not found; run `ncat reindex`: "
                                f"{paths.index}")
    query = (query or "").strip()
    if not query:
        return []
    connection = _connect(paths.index)
    try:
        # Keep FTS syntax safe for human queries.
        terms = [token for token in query.replace('"', " ").split()
                 if token]
        expression = " AND ".join(f'"{token}"*' for token in terms)
        rows = connection.execute(
            "SELECT kind, id, text FROM fts WHERE fts MATCH ? LIMIT ?",
            (expression, limit),
        ).fetchall()
        return [{"kind": row["kind"], "id": row["id"],
                 "text": row["text"]} for row in rows]
    finally:
        connection.close()


def delete_index(paths: ProjectPaths) -> bool:
    if paths.index.is_file():
        paths.index.unlink()
        return True
    return False
