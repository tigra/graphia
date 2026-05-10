"""Bedrock LLM singletons (Sonnet + Haiku) and structured-output schemas."""

from __future__ import annotations

from typing import Literal

from langchain_aws import ChatBedrockConverse
from pydantic import BaseModel, Field, field_validator, model_validator

from graphia.config import load_config

_SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
_HAIKU_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

_sonnet: ChatBedrockConverse | None = None
_haiku: ChatBedrockConverse | None = None


def get_sonnet() -> ChatBedrockConverse:
    global _sonnet
    if _sonnet is None:
        _sonnet = ChatBedrockConverse(
            model=_SONNET_MODEL_ID,
            region_name=load_config().aws_region,
            temperature=0.7,
        )
    return _sonnet


def get_haiku() -> ChatBedrockConverse:
    global _haiku
    if _haiku is None:
        _haiku = ChatBedrockConverse(
            model=_HAIKU_MODEL_ID,
            region_name=load_config().aws_region,
            temperature=0.8,
        )
    return _haiku


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
