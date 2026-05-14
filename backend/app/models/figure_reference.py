"""FigureReference — link table: one Figure can be referenced by many
theory blocks / questions across the book.

Created in migration 0015. NOT to be confused with FigureRegeneration
(0004, per-figure regen history). FigureReference is about WHERE a figure
shows up (placeholders); FigureRegeneration is about WHICH variants of the
figure exist.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class FigureReference(Base):
    __tablename__ = "figure_references"

    id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid4,
    )
    figure_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("figures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    book_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_ref: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    # "theory" | "question"
    context: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    question_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    placeholder_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # "auto" (linker) | "manual" (user override)
    link_method: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, server_default="auto",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
