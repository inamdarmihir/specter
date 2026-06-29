"""Tests for the Specter FastAPI server endpoints.

Mocks out both the agent and the Qdrant store so tests run without any
external services.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from specter.server import app


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_store_mock(search_results: list | None = None) -> MagicMock:
    store = MagicMock()
    store.asearch = AsyncMock(return_value=search_results or [])
    store.adelete = AsyncMock(return_value=None)
    store._client = MagicMock()
    store._client.close = AsyncMock()
    return store


def _make_agent_mock(tokens: list[str] | None = None) -> MagicMock:
    tokens = tokens or ["Hello", ", ", "world", "!"]

    async def _stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        for t in tokens:
            yield t

    agent = MagicMock()
    agent.astream_tokens = _stream
    return agent


# ---------------------------------------------------------------------------
# Fixtures that patch the global _state object
# ---------------------------------------------------------------------------


@pytest.fixture()
def patched_app(monkeypatch: pytest.MonkeyPatch):
    """Patch app state so tests don't need a live agent or Qdrant."""
    import specter.server as srv

    monkeypatch.setattr(srv._state, "agent", _make_agent_mock())
    monkeypatch.setattr(srv._state, "store", _make_store_mock())
    return app


@pytest.fixture()
def client(patched_app):
    return TestClient(patched_app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/chat
# ---------------------------------------------------------------------------


def test_chat_streams_tokens(client: TestClient):
    """SSE stream must contain token events followed by a done event."""
    payload = {
        "session_id": "test-session",
        "message": "Hello",
        "success_criteria": "",
        "history": [],
    }
    with client.stream("POST", "/api/chat", json=payload) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        events = []
        for line in resp.iter_lines():
            line = line.strip()
            if line.startswith("data:"):
                raw = line[len("data:"):].strip()
                if raw:
                    events.append(json.loads(raw))

    # Must have at least one token and end with a done
    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert token_events, "Expected token events in SSE stream"
    assert done_events, "Expected done event in SSE stream"
    full_text = "".join(e["content"] for e in token_events)
    assert "Hello" in full_text


def test_chat_error_propagated(monkeypatch: pytest.MonkeyPatch):
    """When the agent raises, the SSE stream must emit an error event."""
    import specter.server as srv

    async def _boom(*a: Any, **kw: Any) -> AsyncIterator[str]:
        raise RuntimeError("agent blew up")
        yield  # make it a generator

    agent = MagicMock()
    agent.astream_tokens = _boom
    monkeypatch.setattr(srv._state, "agent", agent)
    monkeypatch.setattr(srv._state, "store", _make_store_mock())

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat",
            json={"session_id": "s", "message": "hi", "history": []},
        ) as resp:
            events = []
            for line in resp.iter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    raw = line[len("data:"):].strip()
                    if raw:
                        events.append(json.loads(raw))

    error_events = [e for e in events if e["type"] == "error"]
    assert error_events, "Expected error event when agent raises"
    assert "agent blew up" in error_events[0]["content"]


# ---------------------------------------------------------------------------
# /api/memory/search
# ---------------------------------------------------------------------------


def test_memory_search_requires_user_id(client: TestClient):
    r = client.get("/api/memory/search?q=hello")
    assert r.status_code == 400


def test_memory_search_returns_results(monkeypatch: pytest.MonkeyPatch):
    """Results returned by the store are serialised correctly."""
    import specter.server as srv
    from langgraph.store.base import SearchItem

    fake_item = MagicMock(spec=SearchItem)
    fake_item.key = "my-key"
    fake_item.value = {"content": "some info"}
    fake_item.score = 0.91

    store = _make_store_mock(search_results=[fake_item])
    monkeypatch.setattr(srv._state, "agent", _make_agent_mock())
    monkeypatch.setattr(srv._state, "store", store)

    with TestClient(app) as client:
        r = client.get("/api/memory/search?q=info&user_id=abc&limit=3")

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["key"] == "my-key"
    assert data[0]["score"] == pytest.approx(0.91)


def test_memory_search_empty(client: TestClient):
    r = client.get("/api/memory/search?user_id=nobody&limit=5")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# /api/sessions/{session_id}
# ---------------------------------------------------------------------------


def test_delete_session_no_memories(client: TestClient):
    """DELETE returns 204 even when there is nothing to delete."""
    r = client.delete("/api/sessions/empty-session")
    assert r.status_code == 204


def test_delete_session_removes_memories(monkeypatch: pytest.MonkeyPatch):
    """DELETE calls adelete for every memory returned by the store."""
    import specter.server as srv
    from langgraph.store.base import SearchItem

    items = []
    for k in ("a", "b", "c"):
        item = MagicMock(spec=SearchItem)
        item.key = k
        item.value = {"content": k}
        item.score = None
        items.append(item)

    store = _make_store_mock(search_results=items)
    monkeypatch.setattr(srv._state, "agent", _make_agent_mock())
    monkeypatch.setattr(srv._state, "store", store)

    with TestClient(app) as client:
        r = client.delete("/api/sessions/my-session")

    assert r.status_code == 204
    assert store.adelete.call_count == 3
