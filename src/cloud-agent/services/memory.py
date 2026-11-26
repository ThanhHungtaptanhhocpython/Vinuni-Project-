"""Long-term memory extraction, embedding, and Qdrant retrieval."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from config import Settings, get_settings
from langchain_core.messages import HumanMessage, SystemMessage
from stores.long_term_memory import MEMORY_TYPES, LongTermMemoryStore, StoredMemory, long_term_memory_store

from services.llm import get_llm

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM_PROMPT = """Extract durable long-term memories from a chat turn.
Return only JSON with a top-level "memories" array.
Allowed memory_type values: summary, fact, preference, decision.
Keep only useful durable information for future conversations. Skip trivial, temporary, or unsafe guesses.
Each item must have: memory_type, content, importance, confidence.
importance and confidence are numbers from 0 to 1."""


@dataclass(frozen=True)
class MemoryCandidate:
    memory_type: str
    content: str
    importance: float = 0.5
    confidence: float = 0.5


def parse_memory_candidates(raw: str) -> list[MemoryCandidate]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = data.get("memories") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    candidates: list[MemoryCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        memory_type = item.get("memory_type")
        content = item.get("content")
        if memory_type not in MEMORY_TYPES or not isinstance(content, str):
            continue
        content = " ".join(content.split())
        if len(content) < 12:
            continue
        candidates.append(
            MemoryCandidate(
                memory_type=memory_type,
                content=content,
                importance=_clamp_float(item.get("importance"), default=0.5),
                confidence=_clamp_float(item.get("confidence"), default=0.5),
            )
        )
    return candidates


def render_long_term_memory(memories: list[dict[str, Any]] | None) -> str:
    if not memories:
        return "## Long-term memory\n- Khong co memory lien quan."
    lines = []
    for memory in memories:
        memory_type = memory.get("memory_type", "memory")
        content = str(memory.get("content") or "").strip()
        if content:
            lines.append(f"- [{memory_type}] {content}")
    if not lines:
        lines.append("- Khong co memory lien quan.")
    return (
        "## Long-term memory\n"
        + "\n".join(lines[:10])
        + "\n\nQuy tac: dung long-term memory nhu boi canh tham khao; neu no mau thuan voi tin nhan moi, uu tien tin nhan moi."
    )


class LongTermMemoryService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        store: LongTermMemoryStore | None = None,
        qdrant_client: Any | None = None,
        embeddings: Any | None = None,
        llm: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or long_term_memory_store
        self._qdrant_client = qdrant_client
        self._embeddings = embeddings
        self._llm = llm

    def available(self) -> bool:
        return bool(
            self.settings.long_term_memory_enabled
            and self.settings.qdrant_url
            and self.settings.qdrant_api_key
            and self.settings.openai_api_key
        )

    async def extract_memories(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        task_memory: dict[str, Any] | None = None,
    ) -> list[MemoryCandidate]:
        if not self.settings.long_term_memory_enabled:
            return []
        llm = self._llm or get_llm(max_tokens=700, settings=self.settings)
        task_memory_text = json.dumps(task_memory or {}, ensure_ascii=False)[:4000]
        prompt = (
            f"User message:\n{user_message}\n\n"
            f"Assistant reply:\n{assistant_reply}\n\n"
            f"Current session task memory JSON:\n{task_memory_text}"
        )
        response = await llm.ainvoke([SystemMessage(content=EXTRACT_SYSTEM_PROMPT), HumanMessage(content=prompt)])
        raw = getattr(response, "content", response)
        if not isinstance(raw, str):
            raw = str(raw)
        return parse_memory_candidates(raw)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self._embeddings
        if embeddings is None:
            from langchain_openai import OpenAIEmbeddings

            embeddings = OpenAIEmbeddings(
                model=self.settings.embedding_model,
                api_key=self.settings.openai_api_key,
            )
        if hasattr(embeddings, "aembed_documents"):
            return await embeddings.aembed_documents(texts)
        return await asyncio.to_thread(embeddings.embed_documents, texts)

    async def upsert_memories(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        candidates: list[MemoryCandidate],
    ) -> list[StoredMemory]:
        if not candidates:
            return []
        vectors = await self.embed_texts([candidate.content for candidate in candidates])
        stored: list[StoredMemory] = []
        points = []
        for candidate, vector in zip(candidates, vectors, strict=False):
            point_id = str(uuid.uuid4())
            memory = self.store.create(
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                memory_type=candidate.memory_type,
                content=candidate.content,
                importance=candidate.importance,
                confidence=candidate.confidence,
                qdrant_point_id=point_id,
            )
            stored.append(memory)
            points.append(self._point_struct(point_id, vector, memory.to_payload()))
        if points:
            await self._ensure_collection(vector_size=len(vectors[0]))
            await asyncio.to_thread(
                self.client.upsert,
                collection_name=self.settings.qdrant_collection,
                points=points,
            )
        return stored

    async def retrieve_memories(self, *, user_id: str, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        if not self.available():
            return []
        vectors = await self.embed_texts([query])
        if not vectors:
            return []
        result = await asyncio.to_thread(
            self.client.search,
            collection_name=self.settings.qdrant_collection,
            query_vector=vectors[0],
            query_filter=self._user_filter(user_id),
            limit=top_k or self.settings.long_term_memory_top_k,
            with_payload=True,
        )
        memories: list[dict[str, Any]] = []
        for item in result or []:
            payload = getattr(item, "payload", None) or {}
            if payload.get("content"):
                memories.append({**payload, "score": getattr(item, "score", None)})
        return memories

    async def record_run_memory(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        user_message: str,
        assistant_reply: str,
        task_memory: dict[str, Any] | None,
    ) -> None:
        if not self.available():
            return
        try:
            candidates = await self.extract_memories(
                user_message=user_message,
                assistant_reply=assistant_reply,
                task_memory=task_memory,
            )
            await self.upsert_memories(
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                candidates=candidates,
            )
        except Exception:
            logger.warning("long-term memory update skipped", exc_info=True)

    async def delete_memories_by_session(self, session_id: str) -> None:
        point_ids = self.store.soft_delete_by_session(session_id)
        await self._delete_points(point_ids=point_ids, payload_field="session_id", payload_value=session_id)

    async def delete_memories_by_user(self, user_id: str) -> None:
        point_ids = self.store.soft_delete_by_user(user_id)
        await self._delete_points(point_ids=point_ids, payload_field="user_id", payload_value=user_id)

    async def _delete_points(self, *, point_ids: list[str], payload_field: str, payload_value: str) -> None:
        if not self.settings.long_term_memory_enabled or not self.settings.qdrant_url:
            return
        try:
            selector = point_ids or self._field_filter(payload_field, payload_value)
            await asyncio.to_thread(
                self.client.delete,
                collection_name=self.settings.qdrant_collection,
                points_selector=selector,
            )
        except Exception:
            logger.warning("long-term memory vector delete skipped", exc_info=True)

    @property
    def client(self) -> Any:
        if self._qdrant_client is None:
            from qdrant_client import QdrantClient

            self._qdrant_client = QdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key,
            )
        return self._qdrant_client

    def _point_struct(self, point_id: str, vector: list[float], payload: dict[str, Any]) -> Any:
        try:
            from qdrant_client.models import PointStruct

            return PointStruct(id=point_id, vector=vector, payload=payload)
        except Exception:
            return {"id": point_id, "vector": vector, "payload": payload}

    async def _ensure_collection(self, *, vector_size: int) -> None:
        client = self.client
        if not hasattr(client, "get_collection"):
            return
        try:
            await asyncio.to_thread(client.get_collection, self.settings.qdrant_collection)
            return
        except Exception:
            pass
        from qdrant_client.models import Distance, VectorParams

        await asyncio.to_thread(
            client.create_collection,
            collection_name=self.settings.qdrant_collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )

    def _user_filter(self, user_id: str) -> Any:
        return self._field_filter("user_id", user_id)

    def _field_filter(self, field: str, value: str) -> Any:
        try:
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            return Filter(must=[FieldCondition(key=field, match=MatchValue(value=value))])
        except Exception:
            return {"must": [{"key": field, "match": {"value": value}}]}


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


long_term_memory_service = LongTermMemoryService()
