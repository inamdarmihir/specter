"""Specter FastAPI server.

Endpoints
---------
POST /api/chat
    Accepts a ChatRequest body, streams SSE tokens from the agent.
    Events:
        data: {"type": "token", "content": "<text>"}
        data: {"type": "done",  "content": ""}
        data: {"type": "error", "content": "<message>"}

GET /api/memory/search?q=<query>&user_id=<id>&limit=<n>
    Semantic search over long-term memory for a given user/session.
    Returns a JSON list of MemoryResult objects.

DELETE /api/sessions/{session_id}
    Deletes all memory points belonging to the session.
    Returns 204 No Content.

GET /health
    Simple liveness probe.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from dotenv import load_dotenv

load_dotenv(override=True)  # .env always wins over system env vars

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from specter.agent import SpectorAgent, create_specter
from specter.memory import QdrantStore


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------


class _AppState:
    agent: SpectorAgent
    store: QdrantStore


_state = _AppState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise the agent on startup; close the Qdrant client on shutdown."""
    agent, store = await create_specter(
        model=os.environ.get("SPECTER_MODEL", "openai:gpt-4o-mini"),
        embed_model=os.environ.get(
            "SPECTER_EMBED_MODEL", "openai:text-embedding-3-small"
        ),
        qdrant_url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        qdrant_api_key=os.environ.get("QDRANT_API_KEY"),
        qdrant_collection=os.environ.get(
            "QDRANT_COLLECTION", "specter_memory"
        ),
        temperature=float(os.environ.get("SPECTER_TEMPERATURE", "0.2")),
    )
    _state.agent = agent
    _state.store = store
    yield
    await store._client.close()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Specter", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    session_id: str
    message: str
    success_criteria: str = ""
    history: list[dict[str, str]] = []


class MemoryResult(BaseModel):
    key: str
    value: dict[str, Any]
    score: float | None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    """Stream assistant tokens as Server-Sent Events."""

    async def _generate() -> AsyncIterator[dict[str, str]]:
        try:
            async for token in _state.agent.astream_tokens(
                req.message,
                session_id=req.session_id,
                success_criteria=req.success_criteria,
                history=req.history,
            ):
                yield {"data": json.dumps({"type": "token", "content": token})}
            yield {"data": json.dumps({"type": "done", "content": ""})}
        except Exception as exc:  # noqa: BLE001
            yield {
                "data": json.dumps({"type": "error", "content": str(exc)})
            }

    return EventSourceResponse(_generate())


@app.get("/api/memory/search", response_model=list[MemoryResult])
async def memory_search(
    q: str = Query(default="", description="Semantic search query"),
    user_id: str = Query(default="", description="Session / user identifier"),
    limit: int = Query(default=5, ge=1, le=50),
) -> list[MemoryResult]:
    """Search long-term memory and return ranked results."""
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    namespace = ("specter", user_id, "memories")
    query: str | None = q.strip() or None
    results = await _state.store.asearch(namespace, query=query, limit=limit)

    return [
        MemoryResult(key=r.key, value=r.value, score=r.score) for r in results
    ]


@app.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """Delete all memory points scoped to a session."""
    namespace = ("specter", session_id, "memories")
    # List all keys in the namespace then delete them
    items = await _state.store.asearch(namespace, limit=1000)
    for item in items:
        await _state.store.adelete(namespace, item.key)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint registered as ``specter-server`` in pyproject.toml."""
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    uvicorn.run(
        "specter.server:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=bool(os.environ.get("DEV")),
    )


if __name__ == "__main__":
    main()
