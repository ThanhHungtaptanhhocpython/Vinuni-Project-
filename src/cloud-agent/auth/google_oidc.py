"""Google OIDC helpers — authorization URL, PKCE token exchange, id_token verify.

DOC 1 §4.2 + DOC 0 §6.1 (iss/aud/exp/nonce/email_verified).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from config import Settings
from jwt import PyJWKClient

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(GOOGLE_JWKS_URL)
    return _jwks_client


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_google_authorize_url(
    *,
    settings: Settings,
    state: str,
    nonce: str,
    code_challenge: str,
    redirect_uri: str | None = None,
) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri or settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(
    *,
    settings: Settings,
    code: str,
    code_verifier: str,
    redirect_uri: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    data = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": redirect_uri or settings.google_redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    try:
        resp = await client.post(GOOGLE_TOKEN_URL, data=data)
        resp.raise_for_status()
        return resp.json()
    finally:
        if owns_client:
            await client.aclose()


def verify_google_id_token(
    id_token: str,
    *,
    settings: Settings,
    nonce: str,
) -> dict[str, Any]:
    """Verify Google ID token signature + claims."""
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=list(GOOGLE_ISSUERS),
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise ValueError(f"id_token invalid: {exc}") from exc

    if claims.get("nonce") != nonce:
        raise ValueError("id_token nonce mismatch")
    if not claims.get("email_verified"):
        raise ValueError("email not verified")
    if not claims.get("email"):
        raise ValueError("email missing")
    return claims


def email_domain_allowed(email: str, allowed_csv: str) -> bool:
    """Empty allowlist → all domains accepted."""
    domains = [d.strip().lower() for d in allowed_csv.split(",") if d.strip()]
    if not domains:
        return True
    parts = email.lower().rsplit("@", 1)
    if len(parts) != 2:
        return False
    return parts[1] in domains
