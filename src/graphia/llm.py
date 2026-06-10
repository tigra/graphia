"""Bedrock LLM singletons (large + small tiers) and structured-output schemas.

Two capability tiers, **named by size, not by model family**, so swapping the
underlying Bedrock model never requires renaming call sites:

- ``get_large()`` — the heavier gameplay model (AI dialogue, votes, pointing).
- ``get_small()`` — the lighter mechanical model (roster name generation).

Both currently resolve to Amazon Nova (Pro / Lite) per ADR-003; that choice is
an operational detail captured below and in the ADR, not in the function names.
"""

from __future__ import annotations

from typing import Literal

from langchain_aws import ChatBedrockConverse
from pydantic import BaseModel, Field, field_validator, model_validator

from graphia.config import load_config

# Model ids are operational choices (ADR-003: Nova over Claude). The *tier*
# names above are the stable interface; these ids can change without touching
# any caller.
_LARGE_MODEL_ID = "amazon.nova-pro-v1:0"
_SMALL_MODEL_ID = "amazon.nova-lite-v1:0"

_large: ChatBedrockConverse | None = None
_small: ChatBedrockConverse | None = None


def get_large() -> ChatBedrockConverse:
    global _large
    if _large is None:
        _large = ChatBedrockConverse(
            model=_LARGE_MODEL_ID,
            region_name=load_config().aws_region,
            temperature=0.7,
        )
    return _large


def get_small() -> ChatBedrockConverse:
    global _small
    if _small is None:
        _small = ChatBedrockConverse(
            model=_SMALL_MODEL_ID,
            region_name=load_config().aws_region,
            temperature=0.8,
        )
    return _small


class Roster(BaseModel):
    names: list[str] = Field(min_length=6, max_length=6)

    @field_validator("names")
    @classmethod
    def _distinct_nonempty(cls, v: list[str]) -> list[str]:
        stripped = [n.strip() for n in v]
        if any(not n for n in stripped):
            raise ValueError("every name must be a non-empty string after strip")
        lowered = [n.lower() for n in stripped]
        if len(set(lowered)) != len(lowered):
            raise ValueError("names must be distinct (case-insensitive)")
        return stripped


class Pointing(BaseModel):
    target_id: str = Field(min_length=1)


class Ballot(BaseModel):
    """A single Yes/No ballot cast during a vote-to-execute.

    Kept deliberately flat: Bedrock Converse tool-use schemas behave best with
    a single top-level primitive field. The boolean ``yes`` is the only signal.
    """

    yes: bool


class DayAction(BaseModel):
    """Flat schema for a Day-phase action.

    Bedrock Converse is finicky about discriminated unions, so we keep
    ``kind`` + ``text`` + ``target_id`` all at the top level and enforce
    the mutual-exclusion invariant via a model validator.
    """

    kind: Literal["speak", "vote"]
    text: str | None = None
    target_id: str | None = None

    @model_validator(mode="after")
    def _check_kind(self) -> "DayAction":
        if self.kind == "speak":
            if self.text is None or not self.text.strip():
                raise ValueError("speak requires non-empty text")
        else:  # kind == "vote"
            if self.target_id is None or not self.target_id.strip():
                raise ValueError("vote requires non-empty target_id")
        return self
