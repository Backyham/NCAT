"""Database schema.

SQLModel definitions mapped onto a single SQLite file. SQLModel is used
rather than raw SQL because it keeps the schema declarative while still
letting SQLModel/SQLAlchemy generate the DDL; nothing here requires
migrations for the current scope, and ``create_all`` is idempotent.

Design notes:

* Glossaries are the durable, reusable asset. Terms hang off them and are
  what the MT stage consults. They outlive any individual job.
* Jobs are disposable records of a run. Cues hang off a job and are
  regenerated whenever the pipeline re-runs that job.
* Timestamps are stored as integer epoch milliseconds (UTC) so that
  sorting and range queries are unambiguous; ASS formatting happens at
  export time only.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# --------------------------------------------------------------- glossary --

class Glossary(SQLModel, table=True):
    """A named term collection, e.g. one per channel or per video series."""

    __tablename__ = 'glossaries'

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ''
    source_lang: str = 'en'
    target_lang: str = 'zh'
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)


class Term(SQLModel, table=True):
    """One glossary entry: a source term and its agreed translation.

    ``note`` holds why the decision was made (product name to keep in
    English, tone preference, and so on) so a later run can cite the
    reason rather than just the mapping.
    """

    __tablename__ = 'terms'
    __table_args__ = (UniqueConstraint('glossary_id', 'source', 'target_lang',
                                       name='uq_term_per_glossary'),)

    id: int | None = Field(default=None, primary_key=True)
    glossary_id: int = Field(foreign_key='glossaries.id', index=True)
    source: str = Field(index=True)
    target: str
    target_lang: str = 'zh'
    note: str = ''
    # How many times this term has actually been used by a translation run.
    usage_count: int = 0
    # Set when a human accepted the suggestion; unconfirmed rows are shown
    # separately in the UI rather than silently trusted.
    confirmed: bool = True
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)


class TranslationMemory(SQLModel, table=True):
    """Whole-segment reuse, keyed on normalised source text.

    Distinct from Term: a term is a word-level glossary rule, this is a
    sentence the translator already approved once.
    """

    __tablename__ = 'translation_memory'
    __table_args__ = (UniqueConstraint('glossary_id', 'source_hash',
                                       name='uq_tm_per_glossary'),)

    id: int | None = Field(default=None, primary_key=True)
    glossary_id: int = Field(foreign_key='glossaries.id', index=True)
    source: str
    source_hash: str = Field(index=True)
    target: str
    target_lang: str = 'zh'
    usage_count: int = 0
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)


# -------------------------------------------------------------------- job --

class Job(SQLModel, table=True):
    """One pipeline run over one piece of media."""

    __tablename__ = 'jobs'

    id: int | None = Field(default=None, primary_key=True)
    title: str = ''
    source_url: str = ''
    # 'pending' | 'running' | 'paused' | 'done' | 'error' | 'cancelled'
    status: str = Field(default='pending', index=True)
    # Name of the stage currently executing.
    current_stage: str = ''
    # Ordered stage ids with per-stage status/timing, for the UI rail.
    stages: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str = ''
    job_dir: str = ''
    glossary_id: int | None = Field(default=None, foreign_key='glossaries.id')
    progress: int = 0          # 0-100, coarse
    created_at: int = Field(default_factory=now_ms, index=True)
    updated_at: int = Field(default_factory=now_ms)


class Cue(SQLModel, table=True):
    """A single bilingual subtitle line.

    ``start_ms``/``end_ms`` are integer milliseconds from media start. The
    pair ``(en, zh)`` is the unit the editor and the MT stage both work
    on. ``setTime(0, 0)`` in the old aligner means "unaligned, needs a
    human"; that maps to ``needs_timing`` here rather than a sentinel time.
    """

    __tablename__ = 'cues'
    __table_args__ = (UniqueConstraint('job_id', 'index', name='uq_cue_index'),)

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key='jobs.id', index=True)
    index: int
    start_ms: int = 0
    end_ms: int = 0
    en: str = ''
    zh: str = ''
    # 'draft' | 'edited' | 'approved'
    status: str = 'draft'
    needs_timing: bool = False
    # Characters per second and per-line length, measured at write time so
    # the editor can flag rows without recomputing.
    cps: float = 0.0
    line_length: int = 0
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)


class PunchItem(SQLModel, table=True):
    """A decision the pipeline wants a human to make.

    Mirrors the punch-list concept: the pipeline never silently changes
    text, it files an item explaining what is wrong and what it suggests.
    """

    __tablename__ = 'punch_items'

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key='jobs.id', index=True)
    cue_id: int | None = Field(default=None, foreign_key='cues.id', index=True)
    # 'timing' | 'terminology' | 'cps' | 'line_length' | 'audio' | 'question'
    kind: str = Field(default='question', index=True)
    reason: str = ''
    suggestion: str = ''
    alternatives: list = Field(default_factory=list, sa_column=Column(JSON))
    # 'open' | 'accepted' | 'edited' | 'dismissed'
    status: str = Field(default='open', index=True)
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)
