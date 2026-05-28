from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BookBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    subject: str | None = None


class BookCreate(BookBase):
    pass


class BookOut(BookBase):
    id: UUID
    folder_id: UUID | None = None
    pdf_url: str | None = None
    schema_: dict[str, Any] | None = None  # "schema" is reserved by pydantic
    analyser: dict[str, Any] | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_book(cls, book) -> "BookOut":
        return cls(
            id=book.id,
            folder_id=book.folder_id,
            title=book.title,
            subject=book.subject,
            pdf_url=book.pdf_url,
            schema_=book.schema,
            analyser=book.analyser,
            status=book.status,
            created_at=book.created_at,
            updated_at=book.updated_at,
        )


class BookUploadResponse(BaseModel):
    book_id: UUID
    job_id: UUID | None = None
    regen_id: UUID | None = None
    status: str
