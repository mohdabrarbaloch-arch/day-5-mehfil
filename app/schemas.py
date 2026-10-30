"""Pydantic request/response models.

Validation lives here, not in the routers, so every endpoint gets the same
rules and the OpenAPI schema is accurate.

Every text field is capped. A character description is not a place to send a
megabyte, and an uncapped field is a free denial-of-service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# --- Auth -------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=80)
    # 72 bytes is bcrypt's limit; 128 chars is a generous UI cap.
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password must be 72 bytes or fewer (bcrypt's limit)")
        return value

    @field_validator("display_name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("display_name cannot be blank")
        return cleaned


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    created_at: datetime


# --- Characters -------------------------------------------------------------


class CharacterBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=20_000)
    personality: str = Field(default="", max_length=20_000)
    scenario: str = Field(default="", max_length=20_000)
    first_mes: str = Field(default="", max_length=20_000)
    mes_example: str = Field(default="", max_length=20_000)
    system_prompt: str = Field(default="", max_length=20_000)
    post_history_instructions: str = Field(default="", max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    is_public: bool = False

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        return [tag.strip()[:50] for tag in value if tag.strip()][:20]


class CharacterCreate(CharacterBase):
    character_book: dict[str, Any] | None = None


class CharacterUpdate(BaseModel):
    """Every field optional: this is a PATCH."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=20_000)
    personality: str | None = Field(default=None, max_length=20_000)
    scenario: str | None = Field(default=None, max_length=20_000)
    first_mes: str | None = Field(default=None, max_length=20_000)
    mes_example: str | None = Field(default=None, max_length=20_000)
    system_prompt: str | None = Field(default=None, max_length=20_000)
    post_history_instructions: str | None = Field(default=None, max_length=20_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    is_public: bool | None = None
    character_book: dict[str, Any] | None = None


class CharacterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    name: str
    description: str
    personality: str
    scenario: str
    first_mes: str
    mes_example: str
    system_prompt: str
    post_history_instructions: str
    tags: list[str] = []
    is_public: bool
    created_at: datetime
    updated_at: datetime


class CharacterDetail(CharacterOut):
    character_book: dict[str, Any] | None = None
    warnings: list[str] = []


class CardImportRequest(BaseModel):
    """A Character Card V2 (or v1) payload, as JSON text or an object."""

    card: dict[str, Any] | str
    is_public: bool = False


# --- Chats ------------------------------------------------------------------


class ChatCreate(BaseModel):
    character_id: int
    title: str = Field(default="New chat", max_length=160)


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    character_id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    backend: str
    simulated: bool
    prompt_tokens: int
    latency_ms: int
    prompt_fingerprint: str
    created_at: datetime


class ChatDetail(ChatOut):
    messages: list[MessageOut] = []
    character_name: str = ""


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8_000)

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message cannot be blank")
        return cleaned


# --- Inspection -------------------------------------------------------------


class PromptPreviewRequest(BaseModel):
    """Preview the prompt for a hypothetical next message."""

    content: str = Field(default="", max_length=8_000)


class PromptPreviewOut(BaseModel):
    blocks: list[dict[str, Any]]
    total_tokens: int
    fingerprint: str
    lore_matches: list[dict[str, Any]]
    fit: dict[str, Any]
    messages: list[dict[str, str]]


# --- Health -----------------------------------------------------------------


class HealthOut(BaseModel):
    status: str
    version: str
    backend: dict[str, Any]
    context_budget: int
