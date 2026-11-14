from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.core.config import settings
from app.core.db import get_db
from app.core.models import TokenRevocation, User
from app.core.security import (
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    security,
    verify_password,
)
from app.schemas import TokenOut, UserCreate, UserOut
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _delete_expired_token_revocations(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT public.minicloud_cleanup_expired_token_revocations()"))
        return

    db.execute(
        delete(TokenRevocation).where(
            TokenRevocation.expires_at <= datetime.now(UTC)
        )
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserCreate, db: Session = Depends(get_db)) -> TokenOut:
    email = payload.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.email)
    return TokenOut(
        user=UserOut(id=user.id, email=user.email),
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/login", response_model=TokenOut)
def login_user(payload: UserCreate, db: Session = Depends(get_db)) -> TokenOut:
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(user.email)
    return TokenOut(
        user=UserOut(id=user.id, email=user.email),
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserOut)
def current_user(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email)


@router.post("/logout")
def logout_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    payload = decode_access_token(credentials.credentials)
    expiry = datetime.fromtimestamp(payload["exp"], tz=UTC)
    fingerprint = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    existing = db.query(TokenRevocation).filter(TokenRevocation.token_hash == fingerprint).first()
    if existing:
        return {"logged_out": True}

    _delete_expired_token_revocations(db)
    db.add(TokenRevocation(token_hash=fingerprint, expires_at=expiry))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if not db.query(TokenRevocation).filter(TokenRevocation.token_hash == fingerprint).first():
            raise
    return {"logged_out": True}
