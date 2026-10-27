from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["affected_by_notice", "excluded_by_notice", "needs_information"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalized(value: str) -> str:
    # Preserve punctuation, suffixes, internal spaces, and leading zeros.
    return value.strip().upper()


def display_guard(value: str) -> str:
    if chr(0x2014) in value:
        raise ValueError(
            "Display text contains forbidden punctuation; source is retained unchanged."
        )
    return value


class Identifier(StrictModel):
    value: str | None
    state: Literal["present", "missing", "empty", "malformed"]

    @classmethod
    def parse(cls, value: str | None, field: str) -> Identifier:
        if value is None:
            return cls(value=None, state="missing")
        if not value.strip():
            return cls(value=value, state="empty")
        pattern = r"[0-9]{4}" if field == "date_code" else r"[A-Za-z0-9][A-Za-z0-9 ()._-]{0,79}"
        valid = bool(re.fullmatch(pattern, value.strip()))
        return cls(value=value, state="present" if valid else "malformed")


class Stock(StrictModel):
    stock_id: str
    brand: Identifier
    model: Identifier
    date_code: Identifier
    serial_number: Identifier
    location: str
    issues: list[str] = Field(default_factory=list)


class Evidence(StrictModel):
    start: int
    end: int
    quote: str


class Alias(StrictModel):
    canonical: str
    alias: str
    evidence: Evidence


class Rule(StrictModel):
    op: Literal["all", "any", "model_set", "date_code_set", "serial_range", "unsupported"]
    values: list[str] = Field(default_factory=list)
    children: list[Rule] = Field(default_factory=list)
    evidence: Evidence
    lower: str | None = None
    upper: str | None = None
    reason: str | None = None


class Criteria(StrictModel):
    brand: str
    root: Rule
    aliases: list[Alias] = Field(default_factory=list)
    complete: bool
    unsupported: list[str] = Field(default_factory=list)


class Snapshot(StrictModel):
    notice_id: str
    url: str
    retrieved_at: str
    source_hash: str
    raw_hash: str
    text: str
    title: str
    complete: bool
    attribution: str = "U.S. Consumer Product Safety Commission"
    source_notice: str = (
        "Official recall text excerpt; related recalls and site navigation omitted."
    )


class Finding(StrictModel):
    stock_id: str
    notice_id: str
    source_hash: str
    status: Status
    reason: str
    required_fields: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    valid: bool = True
