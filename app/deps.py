"""Shared dependencies: current user, ownership guards."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import Character, Conversation, User
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise CREDENTIALS_ERROR

    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise CREDENTIALS_ERROR

    subject = payload.get("sub")
    if not subject:
        raise CREDENTIALS_ERROR

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise CREDENTIALS_ERROR from None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise CREDENTIALS_ERROR
    return user


def get_owned_character(
    character_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Character:
    """Fetch a character the user owns.

    Returns 404 rather than 403 for someone else's character — a 403 confirms
    the id exists, which is an enumeration leak.
    """
    character = db.get(Character, character_id)
    if character is None or character.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Character not found.")
    return character


def get_owned_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation
