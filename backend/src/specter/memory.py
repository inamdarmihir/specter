"""Qdrant-backed LangGraph BaseStore for Specter.

Provides `QdrantStore`, a full async `BaseStore` implementation that stores
agent memories and task summaries in a Qdrant vector collection, with
semantic search powered by LangChain embeddings.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable, Optional, Sequence

from langchain.embeddings import init_embeddings
from langgraph.store.base import BaseStore, Item, SearchItem
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.models import Distance, PointStruct, VectorParams

# Stable UUID namespace for deterministic point IDs
_UUID_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _point_id(namespace: tuple[str, ...], key: str) -> str:
    """Return a deterministic UUID string for a (namespace, key) pair."""
    seed = json.dumps(list(namespace)) + ":" + key
    return str(uuid.uuid5(_UUID_NS, seed))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ns_filter(namespace_prefix: tuple[str, ...]) -> models.Filter:
    """Build a Qdrant filter that matches all points whose ns starts with *namespace_prefix*."""
    must = [
        models.FieldCondition(
            key=f"ns[{i}]",
            match=models.MatchValue(value=v),
        )
        for i, v in enumerate(namespace_prefix)
    ]
    return models.Filter(must=must)


class QdrantStore(BaseStore):
    """LangGraph BaseStore backed by AsyncQdrantClient with semantic search."""

    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str,
        embeddings: Any,
        dims: int = 1536,
    ) -> None:
        self._client = client
        self._collection = collection
        self._embeddings = embeddings
        self._dims = dims

    # ------------------------------------------------------------------
    # Async core operations
    # ------------------------------------------------------------------

    async def aput(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        *,
        index: Optional[list[str]] = None,
    ) -> None:
        """Upsert a value into the store under (namespace, key).

        Preserves the original `created_at` timestamp if the point already exists.
        """
        point_id = _point_id(namespace, key)
        now = _now_iso()

        # Check if point already exists to preserve created_at
        existing = await self._client.retrieve(
            collection_name=self._collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        created_at = existing[0].payload.get("created_at", now) if existing else now

        text = json.dumps(value)
        vectors = await self._embeddings.aembed_documents([text])
        vector = vectors[0]

        payload = {
            "ns": list(namespace),
            "key": key,
            "val": value,
            "created_at": created_at,
            "updated_at": now,
        }

        await self._client.upsert(
            collection_name=self._collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )

    async def aget(
        self,
        namespace: tuple[str, ...],
        key: str,
    ) -> Optional[Item]:
        """Retrieve an item by (namespace, key). Returns None if not found."""
        point_id = _point_id(namespace, key)
        results = await self._client.retrieve(
            collection_name=self._collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        if not results:
            return None
        p = results[0].payload
        return Item(
            namespace=tuple(p["ns"]),
            key=p["key"],
            value=p["val"],
            created_at=datetime.fromisoformat(p["created_at"]),
            updated_at=datetime.fromisoformat(p["updated_at"]),
        )

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        """Delete a point by (namespace, key)."""
        point_id = _point_id(namespace, key)
        await self._client.delete(
            collection_name=self._collection,
            points_selector=models.PointIdsList(points=[point_id]),
        )

    async def asearch(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        query: Optional[str] = None,
        filter: Optional[dict] = None,
        limit: int = 10,
        offset: Optional[int] = None,
    ) -> list[SearchItem]:
        """Search by semantic similarity (query given) or scroll (no query)."""
        ns_filter = _ns_filter(namespace_prefix)

        if query is not None:
            vector = await self._embeddings.aembed_query(query)
            hits = await self._client.search(
                collection_name=self._collection,
                query_vector=vector,
                query_filter=ns_filter,
                limit=limit,
                with_payload=True,
            )
            return [
                SearchItem(
                    namespace=tuple(r.payload["ns"]),
                    key=r.payload["key"],
                    value=r.payload["val"],
                    created_at=datetime.fromisoformat(r.payload["created_at"]),
                    updated_at=datetime.fromisoformat(r.payload["updated_at"]),
                    score=r.score,
                )
                for r in hits
            ]
        else:
            # Scroll without semantic scoring
            scroll_offset = None
            results: list[SearchItem] = []
            while True:
                points, next_offset = await self._client.scroll(
                    collection_name=self._collection,
                    scroll_filter=ns_filter,
                    limit=limit,
                    offset=scroll_offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for p in points:
                    pay = p.payload
                    results.append(
                        SearchItem(
                            namespace=tuple(pay["ns"]),
                            key=pay["key"],
                            value=pay["val"],
                            created_at=datetime.fromisoformat(pay["created_at"]),
                            updated_at=datetime.fromisoformat(pay["updated_at"]),
                            score=None,
                        )
                    )
                if next_offset is None or len(results) >= limit:
                    break
                scroll_offset = next_offset
            return results[:limit]

    async def alist_namespaces(
        self,
        *,
        prefix: Optional[tuple[str, ...]] = None,
        suffix: Optional[tuple[str, ...]] = None,
        max_depth: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[tuple[str, ...]]:
        """List unique namespaces, optionally filtered by prefix/suffix/depth."""
        seen: set[tuple[str, ...]] = set()
        scroll_offset = None
        ns_filter = _ns_filter(prefix) if prefix else None

        while True:
            points, next_offset = await self._client.scroll(
                collection_name=self._collection,
                scroll_filter=ns_filter,
                limit=100,
                offset=scroll_offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                ns = tuple(p.payload.get("ns", []))
                if max_depth is not None:
                    ns = ns[:max_depth]
                if suffix and not ns[-len(suffix):] == suffix:
                    continue
                seen.add(ns)
            if next_offset is None:
                break
            scroll_offset = next_offset

        sorted_ns = sorted(seen)
        return sorted_ns[offset: offset + limit]

    # ------------------------------------------------------------------
    # Sync shims (required by BaseStore ABC)
    # ------------------------------------------------------------------

    def put(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self.aput(namespace, key, value))
        except RuntimeError:
            asyncio.run(self.aput(namespace, key, value))

    def get(self, namespace: tuple[str, ...], key: str) -> Optional[Item]:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.aget(namespace, key))
        except RuntimeError:
            return asyncio.run(self.aget(namespace, key))

    def delete(self, namespace: tuple[str, ...], key: str) -> None:
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self.adelete(namespace, key))
        except RuntimeError:
            asyncio.run(self.adelete(namespace, key))

    def search(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        query: Optional[str] = None,
        limit: int = 10,
    ) -> list[SearchItem]:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                self.asearch(namespace_prefix, query=query, limit=limit)
            )
        except RuntimeError:
            return asyncio.run(
                self.asearch(namespace_prefix, query=query, limit=limit)
            )

    def list_namespaces(
        self,
        *,
        prefix: Optional[tuple[str, ...]] = None,
        suffix: Optional[tuple[str, ...]] = None,
        max_depth: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[tuple[str, ...]]:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                self.alist_namespaces(
                    prefix=prefix, suffix=suffix, max_depth=max_depth,
                    limit=limit, offset=offset,
                )
            )
        except RuntimeError:
            return asyncio.run(
                self.alist_namespaces(
                    prefix=prefix, suffix=suffix, max_depth=max_depth,
                    limit=limit, offset=offset,
                )
            )

    # ------------------------------------------------------------------
    # Batch operations (required by BaseStore ABC)
    # ------------------------------------------------------------------

    async def abatch(self, ops: Iterable) -> list:
        """Execute a batch of store operations."""
        results = []
        for op in ops:
            op_type = type(op).__name__
            if op_type == "GetOp":
                results.append(await self.aget(op.namespace, op.key))
            elif op_type == "PutOp":
                await self.aput(op.namespace, op.key, op.value)
                results.append(None)
            elif op_type == "DeleteOp":
                await self.adelete(op.namespace, op.key)
                results.append(None)
            elif op_type == "SearchOp":
                results.append(
                    await self.asearch(
                        op.namespace_prefix,
                        query=getattr(op, "query", None),
                        limit=getattr(op, "limit", 10),
                    )
                )
            elif op_type == "ListNamespacesOp":
                results.append(
                    await self.alist_namespaces(
                        prefix=getattr(op, "prefix", None),
                        suffix=getattr(op, "suffix", None),
                        max_depth=getattr(op, "max_depth", None),
                    )
                )
            else:
                results.append(None)
        return results

    def batch(self, ops: Iterable) -> list:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.abatch(ops))
        except RuntimeError:
            return asyncio.run(self.abatch(ops))

    # ------------------------------------------------------------------
    # Factory classmethod
    # ------------------------------------------------------------------

    @classmethod
    @asynccontextmanager
    async def from_config(
        cls,
        url: str = "http://localhost:6333",
        api_key: Optional[str] = None,
        collection: str = "specter_memory",
        embed_model: str = "openai:text-embedding-3-small",
        dims: int = 1536,
    ) -> AsyncIterator["QdrantStore"]:
        """Async context manager that creates the Qdrant collection if missing, then yields a QdrantStore.

        Usage::

            async with QdrantStore.from_config(url=..., embed_model=...) as store:
                await store.aput(...)
        """
        client_kwargs: dict[str, Any] = {"url": url}
        if api_key:
            client_kwargs["api_key"] = api_key

        client = AsyncQdrantClient(**client_kwargs)
        embeddings = init_embeddings(embed_model)

        # Create collection if it doesn't already exist
        existing = await client.get_collections()
        existing_names = {c.name for c in existing.collections}
        if collection not in existing_names:
            await client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
            )

        store = cls(client=client, collection=collection, embeddings=embeddings, dims=dims)
        try:
            yield store
        finally:
            await client.close()
