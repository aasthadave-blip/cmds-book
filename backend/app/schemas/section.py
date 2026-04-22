from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    book_id: UUID
    section_id: str
    title: str
    level: int | None = None
    blocks: list[dict[str, Any]] = []
    qc_local: dict[str, Any] | None = None
    qc_llm: dict[str, Any] | None = None
    status: str
    attempts: int
