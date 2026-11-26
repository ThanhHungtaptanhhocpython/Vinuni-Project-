from types import SimpleNamespace

import pytest
from services.memory import LongTermMemoryService, MemoryCandidate, parse_memory_candidates
from stores.long_term_memory import StoredMemory


class FakeEmbeddings:
    async def aembed_documents(self, texts):
        return [[float(index + 1), 0.5] for index, _ in enumerate(texts)]


class FakeQdrant:
    def __init__(self):
        self.upserts = []
        self.searches = []
        self.deletes = []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def search(self, **kwargs):
        self.searches.append(kwargs)
        return [
            SimpleNamespace(
                score=0.93,
                payload={
                    "user_id": "user_a",
                    "memory_type": "preference",
                    "content": "User prefers short answers.",
                },
            )
        ]

    def delete(self, **kwargs):
        self.deletes.append(kwargs)


class FakeMemoryStore:
    def __init__(self):
        self.memories = []
        self.deleted_sessions = []
        self.deleted_users = []

    def create(self, **kwargs):
        memory = StoredMemory(
            id=f"mem_{len(self.memories) + 1}",
            created_at="2026-08-22T00:00:00.000+00:00",
            updated_at="2026-08-22T00:00:00.000+00:00",
            deleted_at=None,
            **kwargs,
        )
        self.memories.append(memory)
        return memory

    def soft_delete_by_session(self, session_id):
        self.deleted_sessions.append(session_id)
        return [
            memory.qdrant_point_id
            for memory in self.memories
            if memory.session_id == session_id and memory.deleted_at is None
        ]

    def soft_delete_by_user(self, user_id):
        self.deleted_users.append(user_id)
        return [
            memory.qdrant_point_id
            for memory in self.memories
            if memory.user_id == user_id and memory.deleted_at is None
        ]


def _settings(**overrides):
    values = {
        "long_term_memory_enabled": True,
        "qdrant_url": "https://qdrant.example",
        "qdrant_api_key": "key",
        "qdrant_collection": "long_term_memories",
        "openai_api_key": "openai-key",
        "embedding_model": "text-embedding-3-small",
        "long_term_memory_top_k": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parse_memory_candidates_filters_invalid_items():
    raw = """
    {
      "memories": [
        {"memory_type": "preference", "content": "User prefers concise Vietnamese replies.", "importance": 0.8, "confidence": 0.9},
        {"memory_type": "temporary", "content": "Invalid type", "importance": 1, "confidence": 1},
        {"memory_type": "fact", "content": "too short"},
        {"memory_type": "decision", "content": "Team decided to use Qdrant Cloud for long-term memory.", "importance": 2, "confidence": -1}
      ]
    }
    """

    candidates = parse_memory_candidates(raw)

    assert [item.memory_type for item in candidates] == ["preference", "decision"]
    assert candidates[0].content == "User prefers concise Vietnamese replies."
    assert candidates[1].importance == 1.0
    assert candidates[1].confidence == 0.0


def test_parse_memory_candidates_rejects_bad_json():
    assert parse_memory_candidates("not json") == []
    assert parse_memory_candidates('{"memories": "wrong"}') == []


@pytest.mark.asyncio
async def test_upsert_memories_writes_metadata_and_qdrant_payload():
    store = FakeMemoryStore()
    qdrant = FakeQdrant()
    service = LongTermMemoryService(
        settings=_settings(),
        store=store,
        qdrant_client=qdrant,
        embeddings=FakeEmbeddings(),
    )

    stored = await service.upsert_memories(
        user_id="user_a",
        session_id="ses_1",
        run_id="run_1",
        candidates=[
            MemoryCandidate(
                memory_type="fact",
                content="User is building a cloud robot agent.",
                importance=0.7,
                confidence=0.8,
            )
        ],
    )

    assert len(stored) == 1
    assert store.memories[0].content == "User is building a cloud robot agent."
    payload = qdrant.upserts[0]["points"][0].payload
    assert payload["user_id"] == "user_a"
    assert payload["session_id"] == "ses_1"
    assert payload["memory_type"] == "fact"
    assert payload["content"] == "User is building a cloud robot agent."


@pytest.mark.asyncio
async def test_retrieve_memories_filters_by_user():
    qdrant = FakeQdrant()
    service = LongTermMemoryService(
        settings=_settings(),
        store=FakeMemoryStore(),
        qdrant_client=qdrant,
        embeddings=FakeEmbeddings(),
    )

    memories = await service.retrieve_memories(user_id="user_a", query="How should you answer?")

    assert memories[0]["content"] == "User prefers short answers."
    search = qdrant.searches[0]
    assert search["collection_name"] == "long_term_memories"
    assert search["limit"] == 5
    assert "user_a" in repr(search["query_filter"])


@pytest.mark.asyncio
async def test_delete_memories_by_session_and_user_soft_deletes_metadata():
    store = FakeMemoryStore()
    qdrant = FakeQdrant()
    service = LongTermMemoryService(
        settings=_settings(),
        store=store,
        qdrant_client=qdrant,
        embeddings=FakeEmbeddings(),
    )
    store.create(
        user_id="user_a",
        session_id="ses_1",
        run_id="run_1",
        memory_type="summary",
        content="The session focused on robot calibration.",
        importance=0.5,
        confidence=0.5,
        qdrant_point_id="point_1",
    )
    store.create(
        user_id="user_b",
        session_id="ses_2",
        run_id="run_2",
        memory_type="fact",
        content="User B uses a different robot.",
        importance=0.5,
        confidence=0.5,
        qdrant_point_id="point_2",
    )

    await service.delete_memories_by_session("ses_1")
    await service.delete_memories_by_user("user_b")

    assert store.deleted_sessions == ["ses_1"]
    assert store.deleted_users == ["user_b"]
    assert qdrant.deletes[0]["points_selector"] == ["point_1"]
    assert qdrant.deletes[1]["points_selector"] == ["point_2"]
