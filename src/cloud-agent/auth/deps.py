"""FastAPI dependencies for SSO auth.

Access JWT is read from the HttpOnly `access_token` cookie (primary).
`Authorization: Bearer` remains as a fallback for API docs / scripts.
"""

from __future__ import annotations

from typing import Annotated

from config import Settings, get_settings
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.security import ACCESS_TOKEN_COOKIE
from auth.store import User, auth_store
from auth.tokens import TokenError, decode_access_token

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_access_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    if credentials is not None and credentials.scheme.lower() == "bearer":
        return credentials.credentials
    cookie = request.cookies.get(ACCESS_TOKEN_COOKIE)
    return cookie or None


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    token = _extract_access_token(request, credentials)
    if not token:
        raise _unauthorized("AUTH_004", "Thiếu access token (cookie hoặc Bearer).")
    try:
        payload = decode_access_token(token, settings)
    except TokenError as exc:
        raise _unauthorized(exc.code, exc.message) from exc
    user = auth_store.get_user(str(payload["sub"]))
    if user is None:
        raise _unauthorized("AUTH_004", "User không tồn tại.")
    return user


async def get_optional_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User | None:
    """Trả User nếu có token hợp lệ; None nếu không gửi token."""
    token = _extract_access_token(request, credentials)
    if not token:
        return None
    try:
        return await get_current_user(request, credentials, settings)
    except HTTPException:
        return None


async def require_user_if_configured(
    user: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User | None:
    """Khi AUTH_REQUIRED=true bắt buộc đăng nhập; ngược lại optional."""
    if settings.auth_required and user is None:
        raise _unauthorized("AUTH_004", "Yêu cầu đăng nhập SSO.")
    return user
