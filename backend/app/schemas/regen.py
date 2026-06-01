"""Regeneration parameter schema + human-readable maps used in P5 prompt."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RegenParams(BaseModel):
    intensity: Literal["light", "moderate", "heavy"] = "moderate"
    tone: Literal["academic", "conversational", "simplified"] = "academic"
    equations_handling: Literal["preserve", "explain"] = "preserve"
    diagrams_handling: Literal["preserve", "describe"] = "preserve"
    analogies: Literal["none", "add_one", "add_multiple"] = "none"
    structure: Literal["identical", "reorganize"] = "identical"
    language: str = "en"
    target_audience: str | None = None
    custom_instructions: str | None = None
    # v3 recap rules (opt-in). Empty = no recap behavior.
    # Only consumed when THEORY_REGEN_PROMPT_VERSION=v3.
    recap_rule_ids: list[str] = Field(default_factory=list)


INTENSITY_MAP: dict[str, str] = {
    "light": (
        "LIGHT — 20-30% change. Swap synonyms and tweak sentence openings only. "
        "Most sentences must look nearly identical to the original. "
        "Do NOT restructure paragraphs or change the overall flow."
    ),
    "moderate": (
        "MODERATE — 40-60% change. Restructure sentences, vary vocabulary significantly, "
        "reorder clauses, and change paragraph transitions. "
        "The output should feel noticeably different from the original while covering the same ideas."
    ),
    "heavy": (
        "HEAVY — 70-90% change. FULLY REWRITE every sentence from scratch. "
        "No sentence should match the original wording. "
        "Change structure, phrasing, sentence length, and paragraph organisation completely. "
        "The content must be unrecognisable as a paraphrase while remaining factually identical."
    ),
}

TONE_MAP: dict[str, str] = {
    "academic": (
        "ACADEMIC — formal third-person prose, passive voice where appropriate, "
        "precise technical vocabulary, no colloquialisms."
    ),
    "conversational": (
        "CONVERSATIONAL — friendly second-person tone ('you'), short accessible sentences, "
        "rhetorical questions, relatable phrasing. Avoid jargon where a plain word works."
    ),
    "simplified": (
        "SIMPLIFIED — very short sentences (max 15 words each), everyday vocabulary only, "
        "define every technical term immediately after use, add a brief real-world example "
        "after every abstract concept."
    ),
}

EQ_HANDLING_MAP: dict[str, str] = {
    "preserve": "keep equations character-for-character identical",
    "explain": "keep equations identical; add one brief explanation line above",
}

DIAG_HANDLING_MAP: dict[str, str] = {
    "preserve": "keep figure captions exactly as given",
    "describe": "keep figure captions exactly; may add one-sentence description",
}

ANALOGY_MAP: dict[str, str] = {
    "none": "do not add analogies",
    "add_one": "add at most one analogy per section if it improves clarity",
    "add_multiple": "add analogies liberally where they help understanding",
}

STRUCTURE_MAP: dict[str, str] = {
    "identical": "IDENTICAL — headers and order must exactly match the original",
    "reorganize": "minor reorganization allowed for better pedagogical flow",
}


LANGUAGE_MAP: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "bn": "Bengali",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
    "ur": "Urdu",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "zh": "Chinese (Simplified)",
    "ja": "Japanese",
    "ar": "Arabic",
}


def param_descriptors(
    params: RegenParams,
    assigned_keypoints: list[str] | None = None,
) -> dict[str, str]:
    lang_name = LANGUAGE_MAP.get(params.language, params.language)
    audience_line = (
        f"TARGET AUDIENCE: Write specifically for {params.target_audience}. "
        f"Calibrate vocabulary, examples, and depth accordingly."
        if params.target_audience
        else ""
    )
    custom_line = (
        f"{params.custom_instructions}"
        if params.custom_instructions
        else ""
    )
    extra = "\n\n".join(filter(None, [audience_line, custom_line]))

    # Recap directives (v3 prompt only — v1 ignores these via _SafeDict).
    from app.services.recap_config import (
        render_active_ids,
        render_keypoints_directive,
        render_renames_directive,
    )

    recap_ids = list(params.recap_rule_ids or [])
    return {
        "intensity_description": INTENSITY_MAP[params.intensity],
        "tone_description": TONE_MAP[params.tone],
        "equations_handling": EQ_HANDLING_MAP[params.equations_handling],
        "diagrams_handling": DIAG_HANDLING_MAP[params.diagrams_handling],
        "analogies": ANALOGY_MAP[params.analogies],
        "structure": STRUCTURE_MAP[params.structure],
        "language": f"{lang_name} — write ALL output text in {lang_name} only",
        "extra_instructions": extra,
        "recap_active_ids": render_active_ids(recap_ids),
        "recap_renames_directive": render_renames_directive(recap_ids),
        "recap_keypoints_directive": render_keypoints_directive(
            assigned_keypoints or [],
            label="Key Points",
        ),
    }


class PostRegenQCResult(BaseModel):
    pass_: bool = Field(alias="pass", default=True)
    drifted_values: list[str] = Field(default_factory=list)
    original_number_count: int = 0

    # New defensive structural checks (R1.4 — quality safety net without
    # an extra LLM call). Block-count drift signals the LLM under- or
    # over-produced free blocks; word-ratio drift signals truncation or
    # runaway expansion beyond the prompt's ±15% length integrity rule.
    block_count_original: int = 0
    block_count_regenerated: int = 0
    word_count_original: int = 0
    word_count_regenerated: int = 0
    word_ratio: float = 1.0  # regen / original

    # Free-form warning strings the UI can surface to reviewers.
    warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class RegenerationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    book_id: UUID
    params: dict[str, Any]
    blocks_by_section: dict[str, Any]
    qc_drift: dict[str, Any] | None
    created_at: datetime
