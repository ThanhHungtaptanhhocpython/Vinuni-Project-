"""SSO Auth routes — Google OIDC (DOC 1 §4.2).

Endpoints:
  GET  /auth/google/login
  GET  /auth/google/callback
  POST /auth/refresh
  POST /auth/logout
  GET  /auth/me
  PATCH /auth/me
  POST /auth/dev/login   (development/test only)
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from auth.deps import get_current_user
from auth.google_oidc import (
    build_google_authorize_url,
    email_domain_allowed,
    exchange_code_for_tokens,
    pkce_pair,
    verify_google_id_token,
)
from auth.security import ACCESS_TOKEN_COOKIE, hash_password, verify_password
from auth.store import User, auth_store
from auth.tokens import (
    TokenError,
    create_access_token,
    decode_access_token,
    decode_oauth_state,
    sign_oauth_state,
)
from config import Settings, get_settings
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

router = APIRouter(prefix="/auth", tags=["auth"])

OAUTH_STATE_COOKIE = "oauth_state"
# Host-wide path so a reverse proxy / callback path rewrite still sends it.
# This cookie is first-party on the API host (Google redirects here), not the UI.
OAUTH_STATE_COOKIE_PATH = "/"
_LEGACY_OAUTH_STATE_COOKIE_PATH = "/api/v1/auth"
REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"
# Broad enough for all /api/v1/* calls via the Next.js rewrite (first-party).
ACCESS_COOKIE_PATH = "/api/v1"
_bearer = HTTPBearer(auto_error=False)


def _meta() -> dict[str, str]:
    return {
        "trace_id": f"trc_{secrets.token_hex(8)}",
        "server_time": datetime.now(UTC).isoformat(timespec="milliseconds"),
    }


def _envelope(data: Any) -> dict:
    return {"data": data, "meta": _meta()}


def _auth_error(code: str, message: str, http_status: int = 401) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"code": code, "message": message},
    )


def _safe_redirect_to(path: str | None) -> str:
    """Only relative paths — chống open redirect (VAL_004)."""
    if not path:
        # Mặc định về trang xác nhận trên chính API (không cần frontend :3000)
        return "/api/v1/auth/done"
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise _auth_error("VAL_004", "redirect_to phải là path nội bộ tương đối.", 400)
    return path


def _cookie_samesite(settings: Settings) -> str:
    # Top-level Google 302 back to this API is first-party; Lax is sent on that GET.
    return "lax"


def _cookie_secure(settings: Settings, request: Request | None = None) -> bool:
    if settings.auth_cookie_secure:
        return True
    if request is not None:
        return request.url.scheme == "https"
    return False


def _set_refresh_cookie(
    response: Response, token: str, settings: Settings, request: Request | None = None
) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=_cookie_secure(settings, request),
        samesite=_cookie_samesite(settings),
        path=REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_ttl_s,
    )


def _set_access_cookie(
    response: Response, token: str, settings: Settings, request: Request | None = None
) -> None:
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        httponly=True,
        secure=_cookie_secure(settings, request),
        samesite=_cookie_samesite(settings),
        path=ACCESS_COOKIE_PATH,
        max_age=settings.access_token_ttl_s,
    )


def _clear_refresh_cookie(
    response: Response, settings: Settings, request: Request | None = None
) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        secure=_cookie_secure(settings, request),
        httponly=True,
        samesite=_cookie_samesite(settings),
    )


def _clear_access_cookie(
    response: Response, settings: Settings, request: Request | None = None
) -> None:
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE,
        path=ACCESS_COOKIE_PATH,
        secure=_cookie_secure(settings, request),
        httponly=True,
        samesite=_cookie_samesite(settings),
    )


def _set_session_cookies(
    response: Response,
    *,
    access: str,
    refresh: str,
    settings: Settings,
    request: Request | None = None,
) -> None:
    _set_access_cookie(response, access, settings, request)
    _set_refresh_cookie(response, refresh, settings, request)


def _clear_session_cookies(
    response: Response, settings: Settings, request: Request | None = None
) -> None:
    _clear_access_cookie(response, settings, request)
    _clear_refresh_cookie(response, settings, request)


def _clear_oauth_state_cookie(
    response: Response, settings: Settings, request: Request | None = None
) -> None:
    opts = {
        "secure": _cookie_secure(settings, request),
        "httponly": True,
        "samesite": _cookie_samesite(settings),
    }
    response.delete_cookie(OAUTH_STATE_COOKIE, path=OAUTH_STATE_COOKIE_PATH, **opts)
    response.delete_cookie(OAUTH_STATE_COOKIE, path=_LEGACY_OAUTH_STATE_COOKIE_PATH, **opts)


def _issue_access(user: User, settings: Settings) -> str:
    return create_access_token(user_id=user.id, email=user.email, settings=settings)


def _session_body(user: User, settings: Settings) -> dict:
    """JSON body for login/refresh — never includes the access JWT (cookie only)."""
    return {
        "token_type": "cookie",
        "expires_in": settings.access_token_ttl_s,
        "user": user.public_dict(),
    }


@router.get("/google/setup", response_class=HTMLResponse)
async def google_setup(settings: Annotated[Settings, Depends(get_settings)]) -> str:
    """Local helper: show the exact redirect URI Google Console must list."""
    uri = settings.google_redirect_uri
    cid = settings.google_client_id or "(not set)"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Google OAuth setup</title>
<style>
 body {{ font-family: system-ui,sans-serif; max-width: 42rem; margin: 2.5rem auto; line-height: 1.5; padding: 0 1rem; }}
 code,pre {{ background:#f4f4f4; padding:.15rem .4rem; border-radius:4px; }}
 pre {{ padding:1rem; overflow-x:auto; word-break:break-all; white-space:pre-wrap; }}
 .ok {{ color:#0a7; }} .warn {{ color:#b60; }}
</style></head><body>
<h1>Fix Error 400: redirect_uri_mismatch</h1>
<p>Your Cloud app sends this <code>redirect_uri</code> to Google. It must appear
<strong>exactly</strong> under the OAuth client’s <em>Authorized redirect URIs</em>:</p>
<pre id="uri">{uri}</pre>
<button type="button" onclick="navigator.clipboard.writeText(document.getElementById('uri').textContent)">Copy URI</button>
<ol>
<li>Open <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">Google Cloud → Credentials</a></li>
<li>Edit OAuth 2.0 Client ID <code>{cid}</code> (type must be <strong>Web application</strong>)</li>
<li>Add the URI above to <strong>Authorized redirect URIs</strong> → Save</li>
<li>Wait ~1 minute, then retry login</li>
</ol>
<p class="warn">localhost ≠ 127.0.0.1. Trailing slash matters. Scheme must be http for local.</p>
<p>Also set <code>UI_BASE={settings.ui_base}</code> so post-login returns to the UI.</p>
</body></html>
"""


@router.get("/google/login")
async def google_login(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    redirect_to: str | None = Query(default=None),
) -> RedirectResponse:
    """Khởi tạo OIDC flow — 302 tới Google + cookie oauth_state."""
    if not settings.google_client_id or not settings.google_client_secret:
        raise _auth_error(
            "AUTH_001",
            "Google SSO chưa được cấu hình (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).",
            503,
        )

    safe_redirect = _safe_redirect_to(redirect_to)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    code_verifier, code_challenge = pkce_pair()
    # Persist redirect_uri used at authorize time so token exchange matches exactly
    oauth_redirect = settings.google_redirect_uri
    state_jwt = sign_oauth_state(
        {
            "state": state,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "redirect_to": safe_redirect,
            "redirect_uri": oauth_redirect,
        },
        settings,
        ttl_s=600,
    )
    auth_store.put_oauth(
        state,
        nonce=nonce,
        code_verifier=code_verifier,
        redirect_to=safe_redirect,
        redirect_uri=oauth_redirect,
    )
    url = build_google_authorize_url(
        settings=settings,
        state=state,
        nonce=nonce,
        code_challenge=code_challenge,
        redirect_uri=oauth_redirect,
    )
    response = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state_jwt,
        httponly=True,
        secure=_cookie_secure(settings, request),
        samesite=_cookie_samesite(settings),
        max_age=600,
        path=OAUTH_STATE_COOKIE_PATH,
    )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Google redirect — exchange code, upsert user, set refresh cookie."""
    ui = settings.ui_base.rstrip("/")

    def fail(code_err: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"{ui}/login?error={code_err}",
            status_code=status.HTTP_302_FOUND,
        )

    if error or not code or not state:
        return fail("AUTH_005")

    # CSRF: require the first-party oauth_state cookie set on /google/login.
    # Do not fall back to the in-memory pending store — that lets an attacker
    # complete *their* Google login and trick a victim into hitting this
    # callback (login CSRF). Cookie is host-only on the API (Google redirects
    # here); UI origin mismatch does not apply. AUTH_011 = cookie missing.
    raw_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not raw_state:
        return fail("AUTH_011")
    try:
        saved = decode_oauth_state(raw_state, settings)
    except TokenError:
        return fail("AUTH_005")
    if saved.get("state") != state:
        return fail("AUTH_005")
    auth_store.pop_oauth(state)

    try:
        token_payload = await exchange_code_for_tokens(
            settings=settings,
            code=code,
            code_verifier=str(saved["code_verifier"]),
            redirect_uri=str(saved.get("redirect_uri") or settings.google_redirect_uri),
        )
        id_token = token_payload.get("id_token")
        if not id_token:
            return fail("AUTH_005")
        claims = verify_google_id_token(
            id_token, settings=settings, nonce=str(saved["nonce"])
        )
    except Exception:
        return fail("AUTH_005")

    email = str(claims["email"])
    if not claims.get("email_verified", False):
        return fail("AUTH_006")
    if not email_domain_allowed(email, settings.allowed_email_domains):
        return fail("AUTH_007")

    user = auth_store.upsert_google_user(
        google_sub=str(claims["sub"]),
        email=email,
        name=str(claims.get("name") or ""),
        picture_url=str(claims.get("picture") or ""),
    )
    refresh = auth_store.issue_refresh(user.id, settings.refresh_token_ttl_s)
    access = create_access_token(user_id=user.id, email=user.email, settings=settings)
    redirect_to = str(saved.get("redirect_to") or "/api/v1/auth/done")
    response = RedirectResponse(
        url=f"{ui}{redirect_to}",
        status_code=status.HTTP_302_FOUND,
    )
    _set_session_cookies(response, access=access, refresh=refresh, settings=settings, request=request)
    _clear_oauth_state_cookie(response, settings, request)
    return response


@router.post("/refresh")
async def refresh_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    """Đổi refresh cookie lấy access token mới (rotation)."""
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise _auth_error("AUTH_004", "Thiếu refresh token cookie.")

    rotated = auth_store.rotate_refresh(raw, settings.refresh_token_ttl_s)
    if rotated is None:
        # Distinguish replay (already used) is hard after revoke-family;
        # report AUTH_004; AUTH_020 only if we detect used before revoke
        raise _auth_error("AUTH_004", "Refresh token không hợp lệ hoặc đã hết hạn.")

    new_refresh, user = rotated
    access = _issue_access(user, settings)
    response = JSONResponse(content=_envelope(_session_body(user, settings)))
    _set_session_cookies(
        response,
        access=access,
        refresh=new_refresh,
        settings=settings,
        request=request,
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
    all_devices: bool = Query(default=False),
) -> Response:
    """Thu hồi refresh token hiện tại (và toàn bộ device nếu all_devices)."""
    raw = request.cookies.get(REFRESH_COOKIE)
    user: User | None = None
    token = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    else:
        token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if token:
        try:
            payload = decode_access_token(token, settings)
            user = auth_store.get_user(str(payload["sub"]))
        except TokenError:
            user = None

    if all_devices and user is not None:
        auth_store.revoke_all_for_user(user.id)
    elif raw:
        auth_store.revoke_refresh(raw)

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookies(response, settings, request)
    return response


class MePatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    timezone: str | None = None
    locale: str | None = Field(default=None, max_length=16)


@router.get("/done", response_class=HTMLResponse)
async def auth_done() -> str:
    """Trang xác nhận sau SSO — không cần frontend trên :3000."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>SSO login OK</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; line-height: 1.5; }
    code { background: #f4f4f4; padding: 0.15rem 0.35rem; border-radius: 4px; }
    pre { background: #111; color: #eee; padding: 1rem; border-radius: 8px; overflow-x: auto; }
  </style>
</head>
<body>
  <h1>SSO login OK</h1>
  <p>Google sign-in finished. HttpOnly <code>access_token</code> (path
  <code>/api/v1</code>) and <code>refresh_token</code> (path
  <code>/api/v1/auth</code>) cookies were set.</p>
  <p>Refresh when access expires:</p>
  <pre>POST /api/v1/auth/refresh  (credentials included)</pre>
  <p>Or open API docs: <a href="/docs">/docs</a></p>
  <p id="out">Checking session…</p>
  <script>
    fetch('/api/v1/auth/me', { credentials: 'include' })
      .then(r => r.json().then(j => ({ status: r.status, j })))
      .then(({ status, j }) => {
        const el = document.getElementById('out');
        if (status === 200 && j.data) {
          el.innerHTML = '<p><b>Session OK (HttpOnly cookies)</b></p>' +
            '<p>User: ' + j.data.email + '</p>';
        } else {
          el.textContent = 'Session check failed (' + status + ').';
        }
      })
      .catch(e => { document.getElementById('out').textContent = String(e); });
  </script>
</body>
</html>
"""


@router.get("/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> dict:
    from stores.robots import robot_store
    from stores.schedules import schedule_store
    from stores.sessions import session_store

    data = user.public_dict(with_stats=True)
    data["stats"] = {
        "robot_count": len(robot_store.list()),
        "session_count": session_store.count(user_id=user.id),
        "active_schedule_count": schedule_store.enabled_count(user_id=user.id),
    }
    return _envelope(data)


@router.patch("/me")
async def patch_me(
    body: MePatch,
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except ZoneInfoNotFoundError as exc:
            raise _auth_error("VAL_006", "timezone phải là IANA hợp lệ.", 400) from exc

    updated = auth_store.update_user(
        user.id,
        name=body.name,
        timezone=body.timezone,
        locale=body.locale,
    )
    assert updated is not None
    return _envelope(updated.public_dict(with_stats=True))


class DevLoginRequest(BaseModel):
    email: str = Field(default="dev@example.com")
    name: str = Field(default="Dev User")


@router.post("/dev/login")
async def dev_login(
    request: Request,
    body: DevLoginRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    """Tạo phiên test nhanh (chỉ APP_ENV=development|test). Không gọi Google."""
    if settings.app_env not in {"development", "test"}:
        raise _auth_error("AUTH_001", "Dev login chỉ bật trong development/test.", 403)

    user = auth_store.upsert_google_user(
        google_sub=f"dev_{body.email.lower()}",
        email=body.email,
        name=body.name,
        picture_url="",
    )
    refresh = auth_store.issue_refresh(user.id, settings.refresh_token_ttl_s)
    access = _issue_access(user, settings)
    response = JSONResponse(content=_envelope(_session_body(user, settings)))
    _set_session_cookies(
        response,
        access=access,
        refresh=refresh,
        settings=settings,
        request=request,
    )
    return response


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/register")
async def register(
    request: Request,
    body: RegisterRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    """Đăng ký bằng email/mật khẩu — song song với Google OIDC, không thay thế."""
    email = body.email.strip().lower()
    if not email_domain_allowed(email, settings.allowed_email_domains):
        raise _auth_error("AUTH_005", "Tài khoản không được phép truy cập hệ thống này.", 403)

    user = auth_store.create_password_user(
        email=email,
        password_hash=hash_password(body.password),
        name=body.name.strip(),
    )
    if user is None:
        raise _auth_error("AUTH_008", "Email này đã được sử dụng.", 409)

    refresh = auth_store.issue_refresh(user.id, settings.refresh_token_ttl_s)
    access = _issue_access(user, settings)
    response = JSONResponse(
        content=_envelope(_session_body(user, settings)), status_code=status.HTTP_201_CREATED
    )
    _set_session_cookies(response, access=access, refresh=refresh, settings=settings, request=request)
    return response


@router.post("/login")
async def login(
    request: Request,
    body: LoginRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    """Đăng nhập bằng email/mật khẩu."""
    user = auth_store.get_user_by_email(body.email.strip().lower())
    # Cùng một thông báo cho "không có user" và "sai mật khẩu" — không để lộ
    # email nào đã đăng ký (AUTH_009).
    if user is None or not verify_password(body.password, user.password_hash):
        raise _auth_error("AUTH_009", "Email hoặc mật khẩu không đúng.", 401)

    refresh = auth_store.issue_refresh(user.id, settings.refresh_token_ttl_s)
    access = _issue_access(user, settings)
    response = JSONResponse(content=_envelope(_session_body(user, settings)))
    _set_session_cookies(response, access=access, refresh=refresh, settings=settings, request=request)
    return response
