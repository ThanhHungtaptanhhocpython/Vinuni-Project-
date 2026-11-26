from datetime import UTC, datetime, timedelta

import jwt
from config import get_settings
from jwt import InvalidTokenError

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"


def hash_password(password: str) -> str:
    from passlib.context import CryptContext  # pyrefly: ignore[missing-import]

    return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(password)


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    from passlib.context import CryptContext  # pyrefly: ignore[missing-import]

    return CryptContext(schemes=["bcrypt"], deprecated="auto").verify(plain, hashed)


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_expire_minutes)
    return jwt.encode(
        {"sub": user_id, "exp": expire, "type": "access"},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def create_refresh_token(user_id: str) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expire_days)
    return jwt.encode(
        {"sub": user_id, "exp": expire, "type": "refresh"},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str, expected_type: str) -> str | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        if payload.get("type") != expected_type:
            return None
        sub = payload.get("sub")
        return sub if isinstance(sub, str) else None
    except InvalidTokenError:
        return None
