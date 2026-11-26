"""User + refresh-token store persisted in PostgreSQL.

OAuth pending state stays in memory (TTL ~10 minutes).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from stores.db import get_connection


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class User:
    id: str
    email: str
    google_sub: str
    name: str = ""
    picture_url: str = ""
    timezone: str = "Asia/Ho_Chi_Minh"
    locale: str = "vi"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # None cho user đăng nhập Google thuần (không có mật khẩu). `google_sub`
    # vẫn giữ NOT NULL UNIQUE ở DB — user đăng ký bằng email/mật khẩu được cấp
    # một `google_sub` giả (`local_<id>`, xem `create_password_user`) để không
    # phải nới lỏng ràng buộc đó; nếu sau này họ đăng nhập Google bằng đúng
    # email này, `upsert_google_user` tìm ra theo email và thay bằng sub thật.
    password_hash: str | None = None

    def public_dict(self, *, with_stats: bool = False) -> dict:
        data = {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "picture_url": self.picture_url,
            "timezone": self.timezone,
            "locale": self.locale,
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
        }
        if with_stats:
            data["stats"] = {
                "robot_count": 0,
                "session_count": 0,
                "active_schedule_count": 0,
            }
        return data


@dataclass
class RefreshRecord:
    token_hash: str
    user_id: str
    family_id: str
    expires_at: datetime
    revoked: bool = False
    used: bool = False


@dataclass
class OAuthPending:
    nonce: str
    code_verifier: str
    redirect_to: str
    redirect_uri: str
    expires_at: datetime


class AuthStore:
    """SSO users and refresh tokens in SQL; oauth pending in RAM."""

    def __init__(self, database_url: str | None = None) -> None:
        self._lock = threading.Lock()
        self._database_url = database_url
        self._hydrated = False
        self.users_by_id: dict[str, User] = {}
        self.users_by_email: dict[str, User] = {}
        self.users_by_google_sub: dict[str, User] = {}
        self.refresh: dict[str, RefreshRecord] = {}
        self.oauth_pending: dict[str, OAuthPending] = {}

    def _db(self):
        conn, _path = get_connection(self._database_url)
        return conn

    def _hydrate(self) -> None:
        if self._hydrated:
            return
        conn = self._db()
        for row in conn.execute("SELECT * FROM users"):
            user = User(
                id=row["id"],
                email=row["email"],
                google_sub=row["google_sub"],
                name=row["name"] or "",
                picture_url=row["picture_url"] or "",
                timezone=row["timezone"] or "Asia/Ho_Chi_Minh",
                locale=row["locale"] or "vi",
                created_at=_parse(row["created_at"]),
                password_hash=row["password_hash"],
            )
            self._index_user(user)
        for row in conn.execute("SELECT * FROM refresh_tokens"):
            rec = RefreshRecord(
                token_hash=row["token_hash"],
                user_id=row["user_id"],
                family_id=row["family_id"],
                expires_at=_parse(row["expires_at"]),
                revoked=bool(row["revoked"]),
                used=bool(row["used"]),
            )
            self.refresh[rec.token_hash] = rec
        self._hydrated = True

    def _index_user(self, user: User) -> None:
        self.users_by_id[user.id] = user
        self.users_by_email[user.email] = user
        self.users_by_google_sub[user.google_sub] = user

    def _save_user(self, user: User) -> None:
        self._db().execute(
            """
            INSERT INTO users (
                id, email, google_sub, name, picture_url, timezone, locale, created_at, password_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                email = excluded.email,
                google_sub = excluded.google_sub,
                name = excluded.name,
                picture_url = excluded.picture_url,
                timezone = excluded.timezone,
                locale = excluded.locale,
                password_hash = excluded.password_hash
            """,
            (
                user.id,
                user.email,
                user.google_sub,
                user.name,
                user.picture_url,
                user.timezone,
                user.locale,
                _iso(user.created_at),
                user.password_hash,
            ),
        )
        self._db().commit()

    def _save_refresh(self, rec: RefreshRecord) -> None:
        self._db().execute(
            """
            INSERT INTO refresh_tokens (
                token_hash, user_id, family_id, expires_at, revoked, used
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_hash) DO UPDATE SET
                revoked = excluded.revoked,
                used = excluded.used,
                expires_at = excluded.expires_at
            """,
            (
                rec.token_hash,
                rec.user_id,
                rec.family_id,
                _iso(rec.expires_at),
                1 if rec.revoked else 0,
                1 if rec.used else 0,
            ),
        )
        self._db().commit()

    def clear(self) -> None:
        with self._lock:
            self._hydrate()
            conn = self._db()
            conn.execute("DELETE FROM refresh_tokens")
            conn.execute("DELETE FROM users")
            conn.commit()
            self.users_by_id.clear()
            self.users_by_email.clear()
            self.users_by_google_sub.clear()
            self.refresh.clear()
            self.oauth_pending.clear()

    def put_oauth(
        self,
        state: str,
        *,
        nonce: str,
        code_verifier: str,
        redirect_to: str,
        redirect_uri: str,
        ttl_s: int = 600,
    ) -> None:
        with self._lock:
            self._hydrate()
            self.oauth_pending[state] = OAuthPending(
                nonce=nonce,
                code_verifier=code_verifier,
                redirect_to=redirect_to,
                redirect_uri=redirect_uri,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
            )

    def pop_oauth(self, state: str) -> OAuthPending | None:
        with self._lock:
            self._hydrate()
            rec = self.oauth_pending.pop(state, None)
        if rec is None or rec.expires_at < datetime.now(UTC):
            return None
        return rec

    def upsert_google_user(
        self,
        *,
        google_sub: str,
        email: str,
        name: str = "",
        picture_url: str = "",
    ) -> User:
        with self._lock:
            self._hydrate()
            user = self.users_by_google_sub.get(google_sub) or self.users_by_email.get(
                email.lower()
            )
            if user is None:
                user = User(
                    id=_new_id("usr"),
                    email=email.lower(),
                    google_sub=google_sub,
                    name=name,
                    picture_url=picture_url,
                )
            else:
                user.google_sub = google_sub
                user.email = email.lower()
                if name:
                    user.name = name
                if picture_url:
                    user.picture_url = picture_url
            self._index_user(user)
            self._save_user(user)
            return user

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            self._hydrate()
            return self.users_by_id.get(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        with self._lock:
            self._hydrate()
            return self.users_by_email.get(email.lower())

    def create_password_user(
        self, *, email: str, password_hash: str, name: str = ""
    ) -> User | None:
        """None nếu email đã có người dùng (Google hoặc mật khẩu) — AUTH_008."""
        with self._lock:
            self._hydrate()
            email = email.lower()
            if email in self.users_by_email:
                return None
            user = User(
                id=_new_id("usr"),
                email=email,
                # `google_sub` là NOT NULL UNIQUE ở DB — user chưa từng đăng
                # nhập Google được cấp một giá trị giả duy nhất thay vì NULL,
                # để không phải nới ràng buộc đó (xem comment trên `User`).
                google_sub=f"local_{_new_id('usr')}",
                name=name,
                password_hash=password_hash,
            )
            self._index_user(user)
            self._save_user(user)
            return user

    def update_user(
        self,
        user_id: str,
        *,
        name: str | None = None,
        timezone: str | None = None,
        locale: str | None = None,
    ) -> User | None:
        with self._lock:
            self._hydrate()
            user = self.users_by_id.get(user_id)
            if user is None:
                return None
            if name is not None:
                user.name = name
            if timezone is not None:
                user.timezone = timezone
            if locale is not None:
                user.locale = locale
            self._save_user(user)
            return user

    def issue_refresh(
        self, user_id: str, ttl_s: int, family_id: str | None = None
    ) -> str:
        raw = secrets.token_urlsafe(48)
        token_hash = hash_token(raw)
        family = family_id or _new_id("fam")
        rec = RefreshRecord(
            token_hash=token_hash,
            user_id=user_id,
            family_id=family,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
        )
        with self._lock:
            self._hydrate()
            self.refresh[token_hash] = rec
            self._save_refresh(rec)
        return raw

    def rotate_refresh(self, raw_token: str, ttl_s: int) -> tuple[str, User] | None:
        """Rotate refresh token. Replay of used token revokes whole family (AUTH_020)."""
        token_hash = hash_token(raw_token)
        with self._lock:
            self._hydrate()
            rec = self.refresh.get(token_hash)
            if rec is None or rec.revoked:
                return None
            if rec.expires_at < datetime.now(UTC):
                rec.revoked = True
                self._save_refresh(rec)
                return None
            if rec.used:
                for r in self.refresh.values():
                    if r.family_id == rec.family_id:
                        r.revoked = True
                        self._save_refresh(r)
                return None
            rec.used = True
            rec.revoked = True
            self._save_refresh(rec)
            user = self.users_by_id.get(rec.user_id)
            if user is None:
                return None
            new_raw = secrets.token_urlsafe(48)
            new_hash = hash_token(new_raw)
            new_rec = RefreshRecord(
                token_hash=new_hash,
                user_id=user.id,
                family_id=rec.family_id,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
            )
            self.refresh[new_hash] = new_rec
            self._save_refresh(new_rec)
            return new_raw, user

    def revoke_refresh(self, raw_token: str) -> None:
        token_hash = hash_token(raw_token)
        with self._lock:
            self._hydrate()
            rec = self.refresh.get(token_hash)
            if rec is not None:
                rec.revoked = True
                self._save_refresh(rec)

    def revoke_all_for_user(self, user_id: str) -> None:
        with self._lock:
            self._hydrate()
            for rec in self.refresh.values():
                if rec.user_id == user_id:
                    rec.revoked = True
                    self._save_refresh(rec)


# Process-wide singleton
auth_store = AuthStore()
