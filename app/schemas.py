"""Pydantic schemas — request and response contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

MAX_MESSAGE = 8000


# ---------------------------------------------------------------- auth


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=80)

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if value.isdigit():
            raise ValueError("Password must not be all digits.")
        if len(set(value)) < 4:
            raise ValueError("Password is too repetitive.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    created_at: datetime


# ---------------------------------------------------------------- lore


class LoreEntryIn(BaseModel):
    name: str = Field(default="", max_length=120)
    keys: list[str] = Field(default_factory=list, max_length=20)
    content: str = Field(min_length=1, max_length=4000)
    priority: int = Field(default=10, ge=0, le=100)
    enabled: bool = True
    constant: bool = False
    case_sensitive: bool = False


class LoreEntryOut(LoreEntryIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


# ---------------------------------------------------------------- character


class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=20000)
    personality: str = Field(default="", max_length=20000)
    scenario: str = Field(default="", max_length=20000)
    first_mes: str = Field(default="", max_length=20000)
    mes_example: str = Field(default="", max_length=20000)
    creator_notes: str = Field(default="", max_length=20000)
    system_prompt: str = Field(default="", max_length=20000)
    post_history_instructions: str = Field(default="", max_length=20000)
    alternate_greetings: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)
    creator: str = Field(default="", max_length=120)
    character_version: str = Field(default="", max_length=40)
    is_public: bool = False
    lore: list[LoreEntryIn] = Field(default_factory=list, max_length=100)


class CharacterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    personality: str
    scenario: str
    first_mes: str
    mes_example: str
    creator_notes: str
    system_prompt: str
    post_history_instructions: str
    alternate_greetings: list[str]
    tags: list[str]
    creator: str
    character_version: str
    is_public: bool
    card_version: str
    created_at: datetime
    updated_at: datetime
    lore: list[LoreEntryOut] = Field(default_factory=list)


class CharacterSummary(BaseModel):
    """Lightweight shape for list views — no long text fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    tags: list[str]
    is_public: bool
    created_at: datetime
    updated_at: datetime


class StyleCardOut(BaseModel):
    avg_sentence_len: float
    question_rate: float
    exclaim_rate: float
    ellipsis_rate: float
    emoji_rate: float
    contraction_rate: float
    first_person_rate: float
    second_person_rate: float
    avg_word_len: float
    caps_ratio: float
    comma_rate: float
    signature_words: list[str]
    sample_sentences: int
    confidence: float


class CardImportRequest(BaseModel):
    card: dict


class CardImportResponse(BaseModel):
    character: CharacterOut
    warnings: list[str]


# ---------------------------------------------------------------- chat


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    backend: str
    model: str
    simulated: bool
    fidelity_score: int | None
    fidelity_label: str | None
    prompt_fingerprint: str
    prompt_tokens: int
    latency_ms: int
    created_at: datetime


class ConversationIn(BaseModel):
    character_id: int
    title: str = Field(default="", max_length=120)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    character_id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class FitReportOut(BaseModel):
    budget: int
    used: int
    reserved_for_reply: int
    remaining: int
    utilisation: float
    history_messages_kept: int
    history_messages_dropped: int
    truncated_blocks: list[str]
    dropped_lore: list[str]


class FidelityOut(BaseModel):
    score: int
    label: str
    findings: list[dict]
    features_checked: int


class SendMessageResponse(BaseModel):
    message: MessageOut
    # None when no model produced the reply — scoring a notice would be noise.
    fidelity: FidelityOut | None = None
    fit: FitReportOut
    fingerprint: str
    backend: str
    simulated: bool


class PreviewRequest(BaseModel):
    content: str = Field(default="", max_length=MAX_MESSAGE)
    context_budget: int | None = Field(default=None, ge=256, le=32768)


class PreviewResponse(BaseModel):
    blocks: list[dict]
    fit: FitReportOut
    fingerprint: str
    rendered: str


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
    backend: str
    database: str
    warnings: list[str]


TokenResponse.model_rebuild()
