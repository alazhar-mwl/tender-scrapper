"""
Database tables.

Ownership rule: each part of the system writes only its own tables, so a
scrape can never overwrite a person's decision.
  - scraper   → sources, tenders, documents
  - AI scoring → tender_scores
  - people    → workflow / notes / users / audit (added in later phases)

Dates are Oman local time (config.OMAN_TZ) stored without an offset.
NULL means "unknown" — it is deliberately kept distinct from False / "".
"""
from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Source(Base):
    """A tender portal (PDO SRM, OQ Tawreed, ...)."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)  # e.g. "pdo_srm"
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    tenders: Mapped[list["Tender"]] = relationship(back_populates="source")


class Tender(Base):
    """Facts scraped from a portal. Overwritten freely by each scrape."""

    __tablename__ = "tenders"
    __table_args__ = (UniqueConstraint("source_id", "reference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    reference: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(Text)
    rfx_type: Mapped[str | None] = mapped_column(String(80))
    portal_status: Mapped[str | None] = mapped_column(String(80))
    # The portal's own bid-response status (e.g. "No Bid Created") — a portal
    # fact, not SSP's internal pipeline stage.
    portal_response_status: Mapped[str | None] = mapped_column(String(80))

    floated_on: Mapped[date | None] = mapped_column(Date)
    closes_on: Mapped[date | None] = mapped_column(Date)
    # NULL when the portal gives only a date (PDO does).
    closes_time: Mapped[time | None] = mapped_column(Time)

    estimated_value: Mapped[str | None] = mapped_column(String(80))
    link: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    purchase_required: Mapped[bool | None] = mapped_column(Boolean)

    scope_of_work: Mapped[str | None] = mapped_column(Text)
    sow_source: Mapped[str | None] = mapped_column(String(255))
    sow_extraction: Mapped[str | None] = mapped_column(String(40))
    doc_fetch_done: Mapped[bool | None] = mapped_column(Boolean)
    doc_fetch_note: Mapped[str | None] = mapped_column(Text)

    first_seen: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)
    # False once the tender stops appearing on the portal. Never deleted, so
    # the team's notes and decisions on it survive.
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # The original v1 tenders.json record, so nothing unmodelled is lost.
    v1_raw: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    source: Mapped[Source] = relationship(back_populates="tenders")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="Document.position"
    )
    scores: Mapped[list["TenderScore"]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="TenderScore.id"
    )


class Document(Base):
    """A file downloaded for a tender, stored under the data dir's documents/."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    # Relative to config.documents_dir(); NULL if the file never existed.
    rel_path: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    present: Mapped[bool] = mapped_column(Boolean)
    v1_path: Mapped[str | None] = mapped_column(Text)

    tender: Mapped[Tender] = relationship(back_populates="documents")


class TenderScore(Base):
    """AI output for a tender. The latest row is the current one."""

    __tablename__ = "tender_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    # Who produced it, e.g. "v1_classifier" (category only) or "v2_scorer".
    scored_by: Mapped[str] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(120))
    score: Mapped[int | None] = mapped_column(Integer)
    factors: Mapped[dict | None] = mapped_column(JSON)
    reasons: Mapped[list | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(80))
    profile_version: Mapped[int | None] = mapped_column(Integer)
    # Fingerprint of the inputs; re-score only when it changes.
    input_hash: Mapped[str | None] = mapped_column(String(64))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime)

    tender: Mapped[Tender] = relationship(back_populates="scores")
