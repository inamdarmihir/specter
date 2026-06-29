"""Specter agent — LangGraph ReAct agent with Qdrant-backed long-term memory.

Public API
----------
EvaluatorOutput : pydantic model holding the agent's self-evaluation.
SpectorAgent    : thin wrapper around a compiled LangGraph graph.
create_specter  : async factory that wires up the agent, store and tools.
"""
from __future__ import annotations

import uuid
from typing import Any, AsyncIterator, Optional

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import Annotated, TypedDict

from specter.memory import QdrantStore


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EvaluatorOutput(BaseModel):
    """Self-evaluation produced by the agent after each turn."""

    success: bool = Field(description="Whether the success criteria were met.")
    reasoning: str = Field(description="Brief explanation of the evaluation.")
    score: float = Field(
        ge=0.0, le=1.0, description="Confidence score between 0 and 1."
    )


class AgentState(TypedDict):
    """Mutable state threaded through the LangGraph graph nodes."""

    messages: Annotated[list, add_messages]
    session_id: str
    success_criteria: str
    evaluation: Optional[EvaluatorOutput]


# ---------------------------------------------------------------------------
# SpectorAgent
# ---------------------------------------------------------------------------


class SpectorAgent:
    """Thin wrapper around a compiled LangGraph graph.

    Attributes
    ----------
    graph : CompiledStateGraph
    store : QdrantStore
    """

    def __init__(self, graph: Any, store: QdrantStore) -> None:
        self.graph = graph
        self.store = store

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def astream_tokens(
        self,
        message: str,
        *,
        session_id: str,
        success_criteria: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """Yield assistant tokens one at a time as the agent responds.

        Parameters
        ----------
        message:
            The user's latest message.
        session_id:
            Opaque string that scopes memory to a conversation.
        success_criteria:
            Optional description of what "done" looks like.
        history:
            Prior turns as ``[{"role": "user"|"assistant", "content": "..."}]``.
        """
        lc_history = _to_lc_messages(history or [])
        user_msg = HumanMessage(content=message)

        init_state: AgentState = {
            "messages": lc_history + [user_msg],
            "session_id": session_id,
            "success_criteria": success_criteria,
            "evaluation": None,
        }

        config = {"configurable": {"thread_id": session_id}}

        async for event in self.graph.astream_events(
            init_state, config=config, version="v2"
        ):
            kind = event.get("event")
            if kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    yield chunk.content

    async def aget_evaluation(
        self,
        message: str,
        *,
        session_id: str,
        success_criteria: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> EvaluatorOutput:
        """Run the graph to completion and return the evaluation result."""
        lc_history = _to_lc_messages(history or [])
        user_msg = HumanMessage(content=message)

        init_state: AgentState = {
            "messages": lc_history + [user_msg],
            "session_id": session_id,
            "success_criteria": success_criteria,
            "evaluation": None,
        }

        config = {"configurable": {"thread_id": session_id}}
        final_state = await self.graph.ainvoke(init_state, config=config)
        return final_state.get("evaluation") or EvaluatorOutput(
            success=False, reasoning="No evaluation produced.", score=0.0
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


async def create_specter(
    *,
    model: str = "openai:gpt-4o-mini",
    embed_model: str = "openai:text-embedding-3-small",
    qdrant_url: str = "http://localhost:6333",
    qdrant_api_key: Optional[str] = None,
    qdrant_collection: str = "specter_memory",
    embed_dims: int = 1536,
    temperature: float = 0.2,
) -> tuple["SpectorAgent", QdrantStore]:
    """Async factory: creates the Qdrant store and LangGraph agent.

    Returns ``(agent, store)`` — the store is kept open for the application
    lifetime; call ``await store._client.close()`` on shutdown.

    Usage inside a FastAPI lifespan::

        agent, store = await create_specter(...)
        # use agent …
        await store._client.close()
    """
    from langchain.chat_models import init_chat_model
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http.models import Distance, VectorParams

    # Support :memory: and local file paths in addition to HTTP URLs.
    # AsyncQdrantClient(location=...) accepts all three; url= only takes HTTP.
    if qdrant_url.startswith("http://") or qdrant_url.startswith("https://"):
        client_kwargs: dict[str, Any] = {"url": qdrant_url}
        if qdrant_api_key:
            client_kwargs["api_key"] = qdrant_api_key
        qdrant_client = AsyncQdrantClient(**client_kwargs)
    else:
        qdrant_client = AsyncQdrantClient(location=qdrant_url)

    # Ensure collection exists
    existing = await qdrant_client.get_collections()
    existing_names = {c.name for c in existing.collections}
    if qdrant_collection not in existing_names:
        await qdrant_client.create_collection(
            collection_name=qdrant_collection,
            vectors_config=VectorParams(size=embed_dims, distance=Distance.COSINE),
        )

    from langchain.embeddings import init_embeddings

    embeddings = init_embeddings(embed_model)
    store = QdrantStore(
        client=qdrant_client,
        collection=qdrant_collection,
        embeddings=embeddings,
        dims=embed_dims,
    )

    llm = init_chat_model(model, temperature=temperature)
    graph = _build_graph(llm, store)
    agent = SpectorAgent(graph=graph, store=store)
    return agent, store


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are Specter, an intelligent AI personal co-worker.
You help users accomplish tasks, answer questions, and remember important context
across sessions using your long-term memory.

When the user provides a success_criteria, self-evaluate at the end of each reply
and steer toward fulfilling it.

Be concise, clear, and action-oriented."""


def _build_graph(llm: Any, store: QdrantStore) -> Any:
    """Construct and compile the LangGraph StateGraph."""

    # ------------------------------------------------------------------ tools
    @tool
    async def remember(content: str, key: str, session_id: str) -> str:
        """Persist a piece of information to long-term memory.

        Args:
            content: The information to remember.
            key: A short, unique identifier for this memory.
            session_id: The current session identifier.
        """
        namespace = ("specter", session_id, "memories")
        await store.aput(namespace, key, {"content": content})
        return f"Remembered: {key}"

    @tool
    async def recall(query: str, session_id: str, limit: int = 5) -> str:
        """Search long-term memory for relevant past context.

        Args:
            query: Natural-language search query.
            session_id: The current session identifier.
            limit: Maximum number of results to return.
        """
        namespace = ("specter", session_id, "memories")
        results = await store.asearch(namespace, query=query, limit=limit)
        if not results:
            return "No relevant memories found."
        lines = []
        for r in results:
            val = r.value.get("content", str(r.value))
            score_str = f" [{r.score:.2f}]" if r.score is not None else ""
            lines.append(f"[{r.key}]{score_str}: {val}")
        return "\n".join(lines)

    tools = [remember, recall]
    llm_with_tools = llm.bind_tools(tools)

    # ------------------------------------------------------------------ nodes

    async def call_model(state: AgentState) -> dict:
        system = SystemMessage(content=_SYSTEM_PROMPT)
        response = await llm_with_tools.ainvoke([system] + state["messages"])
        return {"messages": [response]}

    async def run_tools(state: AgentState) -> dict:
        """Execute any tool calls requested by the model."""
        from langchain_core.messages import ToolMessage

        last = state["messages"][-1]
        new_messages = []
        for tc in getattr(last, "tool_calls", []):
            fn_name = tc["name"]
            fn_args = dict(tc["args"])
            # Inject session_id automatically
            fn_args.setdefault("session_id", state["session_id"])
            tool_map = {t.name: t for t in tools}
            if fn_name in tool_map:
                result = await tool_map[fn_name].ainvoke(fn_args)
            else:
                result = f"Unknown tool: {fn_name}"
            new_messages.append(
                ToolMessage(content=str(result), tool_call_id=tc["id"])
            )
        return {"messages": new_messages}

    async def evaluate(state: AgentState) -> dict:
        """Self-evaluate against the success_criteria (if provided)."""
        criteria = state.get("success_criteria", "").strip()
        if not criteria:
            return {
                "evaluation": EvaluatorOutput(
                    success=True,
                    reasoning="No success criteria specified.",
                    score=1.0,
                )
            }

        eval_llm = llm.with_structured_output(EvaluatorOutput)
        last_ai = next(
            (
                m
                for m in reversed(state["messages"])
                if isinstance(m, AIMessage)
            ),
            None,
        )
        last_content = last_ai.content if last_ai else "(no response)"
        prompt = (
            f"Success criteria: {criteria}\n\n"
            f"Assistant's last response:\n{last_content}\n\n"
            "Evaluate whether the criteria are met."
        )
        result: EvaluatorOutput = await eval_llm.ainvoke(prompt)
        return {"evaluation": result}

    # ------------------------------------------------------------------ routing

    def should_use_tools(state: AgentState) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return "evaluate"

    # ------------------------------------------------------------------ graph

    builder: StateGraph = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", run_tools)
    builder.add_node("evaluate", evaluate)

    builder.set_entry_point("agent")
    builder.add_conditional_edges("agent", should_use_tools)
    builder.add_edge("tools", "agent")
    builder.add_edge("evaluate", END)

    return builder.compile()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_lc_messages(history: list[dict[str, str]]) -> list:
    """Convert plain role/content dicts to LangChain message objects."""
    out = []
    for item in history:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role == "user":
            out.append(HumanMessage(content=content))
        else:
            out.append(AIMessage(content=content))
    return out
