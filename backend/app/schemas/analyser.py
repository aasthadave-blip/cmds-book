"""Schemas for P1 Analyser + P2 Schema generator outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

_VALID_SECTION_TYPES = {"chapter", "section", "subsection", "excluded"}
_SECTION_TYPE_MAP = {
    "subsubsection": "subsection",
    "unit": "chapter",
    "topic": "subsection",
    "sub-section": "subsection",
    "sub_section": "subsection",
    "part": "chapter",
}


class AnalyserResult(BaseModel):
    pdf_type: Literal["digital", "scanned", "mixed"]
    estimated_pages: int = 0
    estimated_words: int = 0
    document_title: str = ""
    subject: str = ""
    has_equations: bool = False
    has_tables: bool = False
    has_diagrams: bool = False


class SchemaSection(BaseModel):
    id: str
    level: int
    title: str
    type: str = "section"
    content_types: list[str] = Field(default_factory=lambda: ["theory"])
    subsections: list["SchemaSection"] = Field(default_factory=list)
    # New fields from improved prompt (optional for backwards compat)
    page_start: int | None = None
    page_end: int | None = None
    is_numbered: bool = True

    model_config = {"extra": "ignore"}

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, v: str) -> str:
        if v in _VALID_SECTION_TYPES:
            return v
        return _SECTION_TYPE_MAP.get(str(v).lower(), "subsection")


class ExcludedSection(BaseModel):
    title: str
    page_start: int | None = None
    page_end: int | None = None
    reason: str = ""
    is_numbered: bool = False

    model_config = {"extra": "ignore"}


class BookSchema(BaseModel):
    document_title: str = ""
    subject: str = ""
    grade_level: str | None = None
    board: str | None = None
    total_pages: int | None = None
    sections: list[SchemaSection] = Field(default_factory=list)
    # New prompt returns excluded_sections; old returned exclusion_summary
    excluded_sections: list[ExcludedSection] = Field(default_factory=list)
    exclusion_summary: list[str] = Field(default_factory=list)
    extraction_notes: str = ""

    model_config = {"extra": "ignore"}


SchemaSection.model_rebuild()
