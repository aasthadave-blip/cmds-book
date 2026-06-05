from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Book(Base):
    __tablename__ = "books"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    # Optional FK into the new ``folders`` table. Nullable so legacy uploads
    # that predate the V-Studio folder concept keep working; the 0021
    # migration backfills all existing rows into a default folder.
    folder_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("folders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    pdf_url: Mapped[str | None] = mapped_column(Text)  # storage key (S3 or local)
    schema: Mapped[dict | None] = mapped_column(sa.JSON)
    analyser: Mapped[dict | None] = mapped_column(sa.JSON)
    raw_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    # Phase 5 (CONTRACT.md §2 — state contract). Per-stage status fields.
    # Today book.status is unconditionally "ready" regardless of per-stage
    # outcome (extract.py:578). These four fields record the actual outcome
    # of each stage. Eventually book.status becomes derived from these
    # (see derive_book_status()); for now they're populated alongside.
    schema_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, server_default="pending",
    )
    theory_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, server_default="pending",
    )
    questions_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, server_default="pending",
    )
    figures_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, server_default="pending",
    )
    # Last verify_book() report — populated by the quality endpoint.
    # Shape documented in app/services/verify_book.py.
    verification_log: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    # SCHEMA Week 1 — observability for schema generation issues.
    # schema_warnings: list of structured warnings the schema generator
    # hit (sanitizer drops, validator violations, postpass-added sections,
    # corrective-retry triggers). Populated by schema_builder during
    # generation. Shape: [{type, section_id, reason, severity}, ...]
    schema_warnings: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)
    # schema_quality_score: 0-100. Computed by the schema validator
    # (lands in Week 2). 90+ good, 70-89 has warnings, <70 schema
    # is rejected outright and surfaced to the user.
    schema_quality_score: Mapped[int | None] = mapped_column(
        sa.Integer, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    sections = relationship(
        "Section",
        back_populates="book",
        cascade="all, delete-orphan",
    )
    regenerations = relationship(
        "Regeneration",
        back_populates="book",
        cascade="all, delete-orphan",
    )
    figures = relationship(
        "Figure",
        back_populates="book",
        cascade="all, delete-orphan",
    )
    figure_regenerations = relationship(
        "FigureRegeneration",
        back_populates="book",
        cascade="all, delete-orphan",
    )
