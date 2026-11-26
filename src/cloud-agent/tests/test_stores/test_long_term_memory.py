import pytest
from stores.long_term_memory import LongTermMemoryStore


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT qdrant_point_id" in sql:
            return FakeResult([{"qdrant_point_id": "point_1"}])
        return FakeResult()

    def commit(self):
        self.calls.append(("COMMIT", None))


def test_list_active_does_not_use_sqlite_rowid(monkeypatch):
    conn = FakeConnection()
    store = LongTermMemoryStore()
    monkeypatch.setattr(store, "_db", lambda: conn)

    store.list_active(user_id="user_1")

    sql = conn.calls[0][0]
    assert "rowid" not in sql.lower()
    assert "ORDER BY created_at DESC, id DESC" in sql


def test_soft_delete_uses_whitelisted_columns(monkeypatch):
    conn = FakeConnection()
    store = LongTermMemoryStore()
    monkeypatch.setattr(store, "_db", lambda: conn)

    point_ids = store.soft_delete_by_session("ses_1")

    assert point_ids == ["point_1"]
    assert "session_id = ?" in conn.calls[0][0]
    assert "session_id = ?" in conn.calls[1][0]


def test_soft_delete_rejects_unknown_field():
    store = LongTermMemoryStore()

    with pytest.raises(ValueError):
        store._soft_delete("session_id; DROP TABLE long_term_memories; --", "ses_1")
