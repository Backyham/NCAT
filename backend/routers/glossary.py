"""Glossary and term endpoints.

Glossaries are the durable asset in this system: they are what the MT stage
consults and what makes the second video faster than the first. The API is
therefore CRUD plus CSV round-trip, and CSV comes first in importance --
bulk-editing a term list in a spreadsheet and importing it is how a real
glossary gets maintained.

CSV columns: source,target,target_lang,note,confirmed
Header is optional; unknown extra columns are ignored.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..db import get_session
from ..models import Glossary, Term, TranslationMemory, now_ms

router = APIRouter(prefix='/api/glossaries', tags=['glossary'])

CSV_FIELDS = ('source', 'target', 'target_lang', 'note', 'confirmed')


# ------------------------------------------------------------- payloads --

class GlossaryIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ''
    source_lang: str = 'en'
    target_lang: str = 'zh'


class GlossaryPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    source_lang: str | None = None
    target_lang: str | None = None


class TermIn(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=500)
    target_lang: str = 'zh'
    note: str = ''
    confirmed: bool = True


class TermPatch(BaseModel):
    source: str | None = None
    target: str | None = None
    target_lang: str | None = None
    note: str | None = None
    confirmed: bool | None = None


# --------------------------------------------------------------- helpers --

def _normalise(text: str) -> str:
    """Casefold and collapse whitespace for matching, not for storage."""
    return re.sub(r'\s+', ' ', text).strip().casefold()


def _require_glossary(session: Session, glossary_id: int) -> Glossary:
    glossary = session.get(Glossary, glossary_id)
    if glossary is None:
        raise HTTPException(404, f'glossary {glossary_id} not found')
    return glossary


# ------------------------------------------------------------- glossaries --

@router.get('')
def list_glossaries(session: Session = Depends(get_session)) -> list[Glossary]:
    return list(session.exec(select(Glossary).order_by(Glossary.name)))


@router.post('', status_code=201)
def create_glossary(payload: GlossaryIn,
                    session: Session = Depends(get_session)) -> Glossary:
    glossary = Glossary(**payload.model_dump())
    session.add(glossary)
    session.commit()
    session.refresh(glossary)
    return glossary


@router.get('/{glossary_id}')
def get_glossary(glossary_id: int,
                 session: Session = Depends(get_session)) -> Glossary:
    return _require_glossary(session, glossary_id)


@router.patch('/{glossary_id}')
def patch_glossary(glossary_id: int, payload: GlossaryPatch,
                   session: Session = Depends(get_session)) -> Glossary:
    glossary = _require_glossary(session, glossary_id)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(glossary, field, value)
    glossary.updated_at = now_ms()
    session.add(glossary)
    session.commit()
    session.refresh(glossary)
    return glossary


@router.delete('/{glossary_id}', status_code=204)
def delete_glossary(glossary_id: int,
                    session: Session = Depends(get_session)) -> Response:
    glossary = _require_glossary(session, glossary_id)
    for term in session.exec(select(Term).where(Term.glossary_id == glossary_id)):
        session.delete(term)
    for row in session.exec(
            select(TranslationMemory).where(
                TranslationMemory.glossary_id == glossary_id)):
        session.delete(row)
    session.delete(glossary)
    session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ terms --

@router.get('/{glossary_id}/terms')
def list_terms(glossary_id: int,
               q: str | None = None,
               confirmed: bool | None = None,
               session: Session = Depends(get_session)) -> list[Term]:
    _require_glossary(session, glossary_id)
    statement = select(Term).where(Term.glossary_id == glossary_id)
    if confirmed is not None:
        statement = statement.where(Term.confirmed == confirmed)
    terms = list(session.exec(statement.order_by(Term.source)))
    if q:
        needle = _normalise(q)
        terms = [t for t in terms
                 if needle in _normalise(t.source) or needle in _normalise(t.target)]
    return terms


@router.post('/{glossary_id}/terms', status_code=201)
def create_term(glossary_id: int, payload: TermIn,
                session: Session = Depends(get_session)) -> Term:
    _require_glossary(session, glossary_id)
    term = Term(glossary_id=glossary_id, **payload.model_dump())
    session.add(term)
    session.commit()
    session.refresh(term)
    return term


@router.patch('/{glossary_id}/terms/{term_id}')
def patch_term(glossary_id: int, term_id: int, payload: TermPatch,
               session: Session = Depends(get_session)) -> Term:
    _require_glossary(session, glossary_id)
    term = session.get(Term, term_id)
    if term is None or term.glossary_id != glossary_id:
        raise HTTPException(404, 'term not found in this glossary')
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(term, field, value)
    term.updated_at = now_ms()
    session.add(term)
    session.commit()
    session.refresh(term)
    return term


@router.delete('/{glossary_id}/terms/{term_id}', status_code=204)
def delete_term(glossary_id: int, term_id: int,
                session: Session = Depends(get_session)) -> Response:
    _require_glossary(session, glossary_id)
    term = session.get(Term, term_id)
    if term is None or term.glossary_id != glossary_id:
        raise HTTPException(404, 'term not found in this glossary')
    session.delete(term)
    session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------ csv round-trip --

@router.get('/{glossary_id}/terms.csv')
def export_terms(glossary_id: int,
                 session: Session = Depends(get_session)) -> Response:
    glossary = _require_glossary(session, glossary_id)
    terms = list(session.exec(
        select(Term).where(Term.glossary_id == glossary_id).order_by(Term.source)))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for term in terms:
        writer.writerow({
            'source': term.source,
            'target': term.target,
            'target_lang': term.target_lang,
            'note': term.note,
            'confirmed': 'true' if term.confirmed else 'false',
        })
    # BOM so Excel opens the CJK target column in UTF-8 rather than mojibake.
    payload = '\ufeff' + buffer.getvalue()
    filename = re.sub(r'[^A-Za-z0-9._-]+', '_', glossary.name) or 'glossary'
    return Response(
        content=payload.encode('utf-8'),
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':
                 f'attachment; filename="{filename}.csv"'},
    )


@router.post('/{glossary_id}/terms.csv')
async def import_terms(glossary_id: int,
                       file: UploadFile,
                       replace: bool = False,
                       session: Session = Depends(get_session)) -> dict[str, Any]:
    """Merge a CSV into the glossary.

    With ``replace=true`` the existing terms are cleared first. Otherwise
    rows are matched on (source, target_lang): an existing pair is updated,
    a new pair is inserted. Returns counts so the UI can show what happened.
    """
    _require_glossary(session, glossary_id)
    raw = (await file.read()).decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames is None:
        raise HTTPException(400, 'CSV has no header row')

    if replace:
        for term in session.exec(
                select(Term).where(Term.glossary_id == glossary_id)):
            session.delete(term)
        session.commit()

    existing = {
        (_normalise(t.source), t.target_lang): t
        for t in session.exec(select(Term).where(Term.glossary_id == glossary_id))
    }

    added = updated = skipped = 0
    for row in reader:
        source = (row.get('source') or '').strip()
        target = (row.get('target') or '').strip()
        if not source or not target:
            skipped += 1
            continue
        target_lang = (row.get('target_lang') or 'zh').strip() or 'zh'
        confirmed = (row.get('confirmed') or 'true').strip().lower() not in (
            'false', '0', 'no', '')
        key = (_normalise(source), target_lang)
        existing_term = existing.get(key)
        if existing_term is None:
            session.add(Term(
                glossary_id=glossary_id, source=source, target=target,
                target_lang=target_lang,
                note=(row.get('note') or '').strip(),
                confirmed=confirmed,
            ))
            added += 1
        else:
            existing_term.target = target
            existing_term.note = (row.get('note') or '').strip()
            existing_term.confirmed = confirmed
            existing_term.updated_at = now_ms()
            session.add(existing_term)
            updated += 1

    session.commit()
    return {'added': added, 'updated': updated, 'skipped': skipped}
