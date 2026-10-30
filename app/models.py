"""SQLAlchemy models.

Four tables: users, characters, chats, messages. A character belongs to one
owner but can be public; a chat belongs to one user and one character.

Messages store `backend` and `simulated` per row. That is deliberate: it
means the history can never retroactively claim a model answered when none
did. Provenance is a column, not a convention.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    characters: Mapped[list[Character]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    chats: Mapped[list[Chat]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    personality: Mapped[str] = mapped_column(Text, default="")
    scenario: Mapped[str] = mapped_column(Text, default="")
    first_mes: Mapped[str] = mapped_column(Text, default="")
    mes_example: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    post_history_instructions: Mapped[str] = mapped_column(Text, default="")

    # JSON-encoded lists/dicts. Stored as text so the same schema works on
    # SQLite and Postgres without a JSONB branch.
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    character_book_json: Mapped[str] = mapped_column(Text, default="")

    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    owner: Mapped[User] = relationship(back_populates="characters")
    chats: Mapped[list[Chat]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="chats")
    character: Mapped[Character] = relationship(back_populates="chats")
    messages: Mapped[list[Message]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Provenance. Never inferred, always recorded.
    backend: Mapped[str] = mapped_column(String(32), default="none")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_fingerprint: Mapped[str] = mapped_column(String(32), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    chat: Mapped[Chat] = relationship(back_populates="messages")
