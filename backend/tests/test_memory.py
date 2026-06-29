"""Unit tests for specter.memory helpers.

These tests exercise the pure-function helpers (_point_id, _ns_filter)
and the QdrantStore methods through a mocked AsyncQdrantClient, so they run
without a live Qdrant instance.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from specter.memory import QdrantStore, _ns_filter, _point_id


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_point_id_deterministic():
    pid1 = _point_id(("a", "b"), "key")
    pid2 = _point_id(("a", "b"), "key")
    assert pid1 == pid2


def test_point_id_different_inputs():
    pid1 = _point_id(("a",), "k1")
    pid2 = _point_id(("a",), "k2")
    pid3 = _point_id(("b",), "k1")
    assert len({pid1, pid2, pid3}) == 3


def test_ns_filter_structure():
    f = _ns_filter(("x", "y"))
    conditions = f.must
    assert len(conditions) == 2
    assert conditions[0].key == "ns[0]"
    assert conditions[0].match.value == "x"
    assert conditions[1].key == "ns[1]"
    assert conditions[1].match.value == "y"


# ---------------------------------------------------------------------------
# QdrantStore helpers
# ---------------------------------------------------------------------------


def _make_client(*, retrieve_result=None, search_result=None, scroll_result=None):
    client = MagicMock()
    client.retrieve = AsyncMock(return_value=retrieve_result or [])
    client.upsert = AsyncMock(return_value=None)
    client.delete = AsyncMock(return_value=None)
    client.search = AsyncMock(return_value=search_result or [])
    # scroll returns (points, next_offset); default: empty page, no next page
    client.scroll = AsyncMock(return_value=(scroll_result or [], None))
    return client


def _make_embeddings(vector=None):
    emb = MagicMock()
    vec = vector or [0.1] * 8
    emb.aembed_documents = AsyncMock(return_value=[vec])
    emb.aembed_query = AsyncMock(return_value=vec)
    return emb


def _make_store(*, retrieve_result=None, search_result=None, scroll_result=None):
    client = _make_client(
        retrieve_result=retrieve_result,
        search_result=search_result,
        scroll_result=scroll_result,
    )
    emb = _make_embeddings()
    return QdrantStore(client=client, collection="test", embeddings=emb, dims=8), client


# ---------------------------------------------------------------------------
# aput
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aput_upserts_new_point():
    store, client = _make_store()
    await store.aput(("ns",), "k", {"x": 1})
    client.upsert.assert_called_once()
    call_args = client.upsert.call_args
    point = call_args.kwargs["points"][0]
    assert point.payload["key"] == "k"
    assert point.payload["val"] == {"x": 1}
    assert "created_at" in point.payload
    assert "updated_at" in point.payload


@pytest.mark.asyncio
async def test_aput_preserves_created_at():
    existing_payload = MagicMock()
    existing_payload.payload = {
        "ns": ["ns"],
        "key": "k",
        "val": {},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    store, client = _make_store(retrieve_result=[existing_payload])
    await store.aput(("ns",), "k", {"x": 2})
    point = client.upsert.call_args.kwargs["points"][0]
    assert point.payload["created_at"] == "2024-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# aget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aget_returns_none_when_missing():
    store, _ = _make_store()
    result = await store.aget(("ns",), "missing")
    assert result is None


@pytest.mark.asyncio
async def test_aget_returns_item():
    payload = MagicMock()
    payload.payload = {
        "ns": ["ns"],
        "key": "k",
        "val": {"hello": "world"},
        "created_at": "2024-06-01T00:00:00+00:00",
        "updated_at": "2024-06-01T01:00:00+00:00",
    }
    store, _ = _make_store(retrieve_result=[payload])
    item = await store.aget(("ns",), "k")
    assert item is not None
    assert item.key == "k"
    assert item.value == {"hello": "world"}


# ---------------------------------------------------------------------------
# adelete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adelete_calls_client():
    store, client = _make_store()
    await store.adelete(("ns",), "k")
    client.delete.assert_called_once()


# ---------------------------------------------------------------------------
# asearch with query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asearch_with_query():
    hit = MagicMock()
    hit.payload = {
        "ns": ["ns"],
        "key": "k",
        "val": {"info": "data"},
        "created_at": "2024-06-01T00:00:00+00:00",
        "updated_at": "2024-06-01T00:00:00+00:00",
    }
    hit.score = 0.88
    store, client = _make_store(search_result=[hit])
    results = await store.asearch(("ns",), query="hello", limit=3)
    assert len(results) == 1
    assert results[0].key == "k"
    assert results[0].score == pytest.approx(0.88)
    client.search.assert_called_once()


@pytest.mark.asyncio
async def test_asearch_without_query_scrolls():
    store, client = _make_store()
    results = await store.asearch(("ns",), limit=5)
    assert results == []
    client.scroll.assert_called()
    client.search.assert_not_called()


# ---------------------------------------------------------------------------
# abatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abatch_get_op():
    store, _ = _make_store()

    class GetOp:
        namespace = ("ns",)
        key = "k"

    results = await store.abatch([GetOp()])
    assert results == [None]  # retrieve returned []


@pytest.mark.asyncio
async def test_abatch_put_op():
    store, client = _make_store()

    class PutOp:
        namespace = ("ns",)
        key = "k"
        value = {"a": 1}

    results = await store.abatch([PutOp()])
    assert results == [None]
    client.upsert.assert_called_once()
