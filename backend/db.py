"""Engine and session wiring.

One SQLite file under ``data/``. WAL is enabled because the web layer writes
job status from a background thread while HTTP handlers read; without it
readers block the writer and the progress stream stalls.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('NCAT_DATA_DIR', BASE_DIR / 'data'))
DB_PATH = DATA_DIR / 'ncat.db'

DATA_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR = DATA_DIR / 'jobs'
JOBS_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f'sqlite:///{DB_PATH}',
    echo=False,
    connect_args={'check_same_thread': False, 'timeout': 30},
)


@event.listens_for(engine, 'connect')
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cur = dbapi_connection.cursor()
    cur.execute('PRAGMA journal_mode=WAL')
    cur.execute('PRAGMA synchronous=NORMAL')
    cur.execute('PRAGMA foreign_keys=ON')
    cur.close()


def init_db() -> None:
    """Create tables if absent. Safe to call on every startup."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(engine) as session:
        yield session
