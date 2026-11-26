"""SSO auth tests — Google OIDC flow surface + session lifecycle (no real Google)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from auth.store import auth_store
from config import Settings, get_settings
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def _clean_auth_store():
    auth_store.clear()
    yield
    auth_store.clear()


@pytest.mark.asyncio
async def test_google_login_not_configured(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "")
    monkeypatch.setattr(settings, "google_client_secret", "")
    resp = await client.get("/api/v1/auth/google/login", follow_redirects=False)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "AUTH_001"


@pytest.mark.asyncio
async def test_google_login_redirects_when_configured(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(
        settings, "google_redirect_uri", "http://test/api/v1/auth/google/callback"
    )

    resp = await client.get(
        "/api/v1/auth/google/login?redirect_to=/robots",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "accounts.google.com" in location
    assert "code_challenge" in location
    assert "state=" in location
    assert "oauth_state" in resp.cookies


@pytest.mark.asyncio
async def test_google_login_rejects_absolute_redirect(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "secret")

    resp = await client.get(
        "/api/v1/auth/google/login?redirect_to=https://evil.example/phish",
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "VAL_004"


@pytest.mark.asyncio
async def test_dev_login_refresh_me_logout(client):
    # Dev login issues HttpOnly access + refresh cookies (JWT not in JSON body)
    login = await client.post(
        "/api/v1/auth/dev/login",
        json={"email": "alice@example.com", "name": "Alice"},
    )
    assert login.status_code == 200
    body = login.json()["data"]
    assert "access_token" not in body
    assert body["user"]["email"] == "alice@example.com"
    assert "refresh_token" in login.cookies
    assert "access_token" in login.cookies

    # Cookie session — no Authorization header required
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["email"] == "alice@example.com"
    assert "stats" in me.json()["data"]

    patch = await client.patch(
        "/api/v1/auth/me",
        json={"name": "Alice Updated", "timezone": "Asia/Bangkok"},
    )
    assert patch.status_code == 200
    assert patch.json()["data"]["name"] == "Alice Updated"
    assert patch.json()["data"]["timezone"] == "Asia/Bangkok"

    # Refresh rotates cookies; body still omits JWT
    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert "access_token" not in refreshed.json()["data"]
    assert "access_token" in refreshed.cookies

    me2 = await client.get("/api/v1/auth/me")
    assert me2.status_code == 200

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204

    again = await client.post("/api/v1/auth/refresh")
    assert again.status_code == 401


@pytest.mark.asyncio
async def test_me_without_token_is_401(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_login_me(client):
    register = await client.post(
        "/api/v1/auth/register",
        json={"email": "bob@example.com", "password": "matkhau123", "name": "Bob"},
    )
    assert register.status_code == 201
    body = register.json()["data"]
    assert "access_token" not in body
    assert body["user"]["email"] == "bob@example.com"
    assert "refresh_token" in register.cookies
    assert "access_token" in register.cookies

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["email"] == "bob@example.com"

    await client.post("/api/v1/auth/logout")

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "matkhau123"},
    )
    assert login.status_code == 200
    assert login.json()["data"]["user"]["email"] == "bob@example.com"


@pytest.mark.asyncio
async def test_register_duplicate_email_is_409(client):
    first = await client.post(
        "/api/v1/auth/register",
        json={"email": "dup@example.com", "password": "matkhau123", "name": "Dup"},
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/auth/register",
        json={"email": "dup@example.com", "password": "khac123456", "name": "Dup2"},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "AUTH_008"


@pytest.mark.asyncio
async def test_login_wrong_password_is_401(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "carol@example.com", "password": "matkhau123", "name": "Carol"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com", "password": "sai_mat_khau"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "AUTH_009"


@pytest.mark.asyncio
async def test_login_unknown_email_is_401(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "khong-ton-tai@example.com", "password": "matkhau123"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "AUTH_009"


@pytest.mark.asyncio
async def test_register_password_too_short_is_422(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "short@example.com", "password": "abc123", "name": "Short"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_requires_auth_when_enabled(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_required", True)

    denied = await client.post("/api/v1/chat", json={"message": "hello"})
    assert denied.status_code == 401

    login = await client.post(
        "/api/v1/auth/dev/login",
        json={"email": "bob@example.com", "name": "Bob"},
    )
    assert login.status_code == 200
    assert "access_token" in login.cookies

    # Empty invalid message validates as 422, which means we passed auth via cookie
    bad = await client.post(
        "/api/v1/chat",
        json={"message": ""},
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_status_exposes_auth_flags(client):
    resp = await client.get("/api/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "auth_required" in data
    assert "google_sso_configured" in data
    assert "google_redirect_uri" in data


def test_auth_survives_new_store_instance(test_database_url):
    from auth.store import AuthStore

    first = AuthStore(database_url=test_database_url)
    user = first.upsert_google_user(
        google_sub="dev_alice@example.com",
        email="alice@example.com",
        name="Alice",
    )
    raw = first.issue_refresh(user.id, ttl_s=3600)

    second = AuthStore(database_url=test_database_url)
    rotated = second.rotate_refresh(raw, ttl_s=3600)
    assert rotated is not None
    _new_raw, loaded = rotated
    assert loaded.email == "alice@example.com"
    assert second.get_user(user.id) is not None


@pytest.mark.asyncio
async def test_google_setup_page(client):
    resp = await client.get("/api/v1/auth/google/setup")
    assert resp.status_code == 200
    assert "redirect_uri_mismatch" in resp.text
    assert "google/callback" in resp.text


def _google_settings(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(
        settings, "google_redirect_uri", "http://test/api/v1/auth/google/callback"
    )
    monkeypatch.setattr(settings, "ui_base", "http://ui.test")


@pytest.mark.asyncio
async def test_google_callback_rejects_missing_oauth_cookie(client, monkeypatch):
    """Login CSRF: completing someone else's Google code without the state cookie."""
    _google_settings(monkeypatch)
    start = await client.get("/api/v1/auth/google/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.cookies.clear()

    resp = await client.get(
        f"/api/v1/auth/google/callback?code=attacker-code&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "login?error=AUTH_011" in resp.headers["location"]


@pytest.mark.asyncio
async def test_google_callback_rejects_state_cookie_mismatch(client, monkeypatch):
    _google_settings(monkeypatch)
    await client.get("/api/v1/auth/google/login", follow_redirects=False)

    resp = await client.get(
        "/api/v1/auth/google/callback?code=x&state=not-the-cookie-state",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "login?error=AUTH_005" in resp.headers["location"]


def test_jwt_secret_required_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "dev-only-change-me")
    with pytest.raises(ValidationError):
        Settings()


def test_jwt_secret_min_length_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(ValidationError):
        Settings()


def test_dev_jwt_secret_persists_to_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "jwt.secret"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("JWT_SECRET", "dev-only-change-me")
    monkeypatch.setenv("JWT_SECRET_FILE", str(secret_file))
    first = Settings()
    second = Settings()
    assert len(first.jwt_secret) >= 32
    assert first.jwt_secret == second.jwt_secret
    assert secret_file.read_text(encoding="utf-8").strip() == first.jwt_secret


def test_security_jwt_helpers_use_settings_fields():
    settings = get_settings()
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_access_expire_minutes == max(1, settings.access_token_ttl_s // 60)
    assert settings.jwt_refresh_expire_days == max(1, settings.refresh_token_ttl_s // 86400)

    from auth.security import create_access_token, create_refresh_token, decode_token

    access = create_access_token("user-1")
    refresh = create_refresh_token("user-1")
    assert decode_token(access, "access") == "user-1"
    assert decode_token(refresh, "refresh") == "user-1"
