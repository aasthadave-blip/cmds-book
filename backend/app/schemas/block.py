"""Canonical Block union — the content model from the master brief.

INVARIANT block types (eq, def, fig, example) are never sent to Claude during
regeneration; they are split out, copied verbatim, and merged back in order.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ParagraphBlock(BaseModel):
    t: Literal["p"] = "p"
    c: str


class HeadingBlock(BaseModel):
    t: Literal["h3"] = "h3"
    c: str


class EquationBlock(BaseModel):
    t: Literal["eq"] = "eq"
    c: str


class DefinitionBlock(BaseModel):
    t: Literal["def"] = "def"
    term: str
    c: str


class KeyPointBlock(BaseModel):
    t: Literal["kp"] = "kp"
    c: str


class FigureBlock(BaseModel):
    t: Literal["fig"] = "fig"
    c: str


class ListBlock(BaseModel):
    t: Literal["list"] = "list"
    items: list[str]


class ExampleBlock(BaseModel):
    t: Literal["example"] = "example"
    label: str
    prob: str
    eqs: list[str] = Field(default_factory=list)


Block = Annotated[
    Union[
        ParagraphBlock,
        HeadingBlock,
        EquationBlock,
        DefinitionBlock,
        KeyPointBlock,
        FigureBlock,
        ListBlock,
        ExampleBlock,
    ],
    Field(discriminator="t"),
]

INVARIANT_TYPES: set[str] = {"eq", "def", "fig", "example", "table"}
FREE_TYPES: set[str] = {"p", "h3", "kp", "list"}
