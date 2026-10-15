"""
TenderIQ v2 web API.

Phase 1 is read-only and has NO login yet, so it listens on 127.0.0.1 only
(config.API_HOST) — never bind it to 0.0.0.0 until Phase 2 adds auth.

Run from V2.0/backend:  python -m tenderiq
"""
from datetime import date, datetime, time
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import config
from .db import get_session
from .models import Source, Tender

app = FastAPI(title="TenderIQ", version="2.0.0-phase1")

SessionDep = Annotated[Session, Depends(get_session)]


class TenderSummary(BaseModel):
    id: int
    source: str
    reference: str
    title: str
    category: str | None
    score: int | None
    closes_on: date | None
    closes_time: time | None
    days_left: int | None
    purchase_required: bool | None
    active: bool
    first_seen: date


class DocumentOut(BaseModel):
    filename: str
    size_bytes: int | None
    present: bool


class ScoreOut(BaseModel):
    scored_by: str
    category: str | None
    score: int | None
    factors: dict | None
    reasons: list | None
    summary: str | None
    scored_at: datetime | None


class TenderDetail(TenderSummary):
    rfx_type: str | None
    portal_status: str | None
    portal_response_status: str | None
    floated_on: date | None
    estimated_value: str | None
    link: str | None
    scope_of_work: str | None
    last_seen: date | None
    documents: list[DocumentOut]
    latest_score: ScoreOut | None


def _today_oman() -> date:
    return datetime.now(config.OMAN_TZ).date()


def _summary_fields(t: Tender) -> dict:
    latest = t.scores[-1] if t.scores else None
    return dict(
        id=t.id,
        source=t.source.name,
        reference=t.reference,
        title=t.title,
        category=latest.category if latest else None,
        score=latest.score if latest else None,
        closes_on=t.closes_on,
        closes_time=t.closes_time,
        days_left=(t.closes_on - _today_oman()).days if t.closes_on else None,
        purchase_required=t.purchase_required,
        active=t.active,
        first_seen=t.first_seen,
    )


@app.get("/api/health")
def health(session: SessionDep) -> dict:
    count = session.scalar(select(func.count()).select_from(Tender))
    return {"status": "ok", "tenders": count}


@app.get("/api/tenders")
def list_tenders(
    session: SessionDep,
    source: Annotated[str | None, Query(description="Portal key, e.g. pdo_srm")] = None,
    status: Literal["active", "inactive", "all"] = "active",
) -> list[TenderSummary]:
    q = (
        select(Tender)
        .join(Tender.source)
        .options(selectinload(Tender.scores), selectinload(Tender.source))
        .order_by(Tender.closes_on.is_(None), Tender.closes_on, Tender.closes_time)
    )
    if source:
        q = q.where(Source.key == source)
    if status != "all":
        q = q.where(Tender.active == (status == "active"))
    return [TenderSummary(**_summary_fields(t)) for t in session.scalars(q)]


@app.get("/api/tenders/{tender_id}")
def get_tender(tender_id: int, session: SessionDep) -> TenderDetail:
    t = session.get(Tender, tender_id)
    if t is None:
        raise HTTPException(404, "Tender not found")
    latest = t.scores[-1] if t.scores else None
    return TenderDetail(
        **_summary_fields(t),
        rfx_type=t.rfx_type,
        portal_status=t.portal_status,
        portal_response_status=t.portal_response_status,
        floated_on=t.floated_on,
        estimated_value=t.estimated_value,
        link=t.link,
        scope_of_work=t.scope_of_work,
        last_seen=t.last_seen,
        documents=[DocumentOut(filename=d.filename, size_bytes=d.size_bytes, present=d.present)
                   for d in t.documents],
        latest_score=ScoreOut.model_validate(latest, from_attributes=True) if latest else None,
    )
