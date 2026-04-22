from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Figure(Base):
    __tablename__ = "figures"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    figure_number: Mapped[str | None] = mapped_column(sa.Text)
    caption: Mapped[str | None] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text)
    image_url: Mapped[str | None] = mapped_column(sa.Text)
    page_number: Mapped[int | None] = mapped_column(sa.Integer)
    bounding_box: Mapped[dict | None] = mapped_column(sa.JSON)
    semantic_type: Mapped[str] = mapped_column(sa.String(32), default="other")
    tags: Mapped[list] = mapped_column(sa.JSON, default=list)
    status: Mapped[str] = mapped_column(sa.String(32), default="extracted")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    book = relationship("Book", back_populates="figures")
    regenerations = relationship("FigureRegeneration", back_populates="figure", cascade="all, delete-orphan")
