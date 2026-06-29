"""Browser tools for Specter — thin async wrappers around the agent-browser CLI.

Each tool calls the ``agent-browser`` binary directly via
``asyncio.create_subprocess_exec``.  The agent-browser daemon manages the
browser session across calls, so tools are fully stateless from Python's
perspective.

Only loaded when ``AGENT_BROWSER_ENABLED=true`` is set.
"""
from __future__ import annotations

import asyncio
import shutil
from typing import Optional

from langchain_core.tools import tool


async def _run(*args: str, timeout: float = 30.0) -> str:
    """Run ``agent-browser <args>`` in a thread and return stdout."""
    import subprocess

    ab = shutil.which("agent-browser")
    if not ab:
        return "Error: agent-browser not found on PATH"

    try:
        # asyncio.create_subprocess_exec can deadlock inside uvicorn on Windows.
        # Running subprocess.run in a thread pool is safe on all platforms.
        proc: subprocess.CompletedProcess = await asyncio.to_thread(
            subprocess.run,
            [ab, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (proc.stdout or "").strip()
        if not output:
            output = (proc.stderr or "").strip()
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: agent-browser timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return f"Error running agent-browser: {exc}"


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------


@tool
async def browser_open(url: str) -> str:
    """Open a URL in the browser. Use this before reading or interacting with a page.

    Args:
        url: The URL to navigate to (e.g. https://example.com).
    """
    return await _run("open", url)


@tool
async def browser_read(url: Optional[str] = None) -> str:
    """Fetch readable text from a URL (no browser launch needed) or from the
    currently open page if no URL is given.

    Args:
        url: URL to fetch. Omit to read the active browser tab.
    """
    args = ["read"] + ([url] if url else [])
    return await _run(*args, timeout=20.0)


@tool
async def browser_snapshot() -> str:
    """Get the accessibility tree of the current page with element refs (@e1, @e2, …).
    Use this after opening a page to discover what elements are available before
    clicking or filling.
    """
    return await _run("snapshot", "-i")  # -i = interactive elements only


@tool
async def browser_click(selector: str) -> str:
    """Click an element on the current page.

    Args:
        selector: An element ref from snapshot (@e2) or a CSS / ARIA selector.
    """
    return await _run("click", selector)


@tool
async def browser_fill(selector: str, text: str) -> str:
    """Clear and fill an input field on the current page.

    Args:
        selector: An element ref from snapshot (@e3) or a CSS selector.
        text: The text to type into the field.
    """
    return await _run("fill", selector, text)


@tool
async def browser_screenshot() -> str:
    """Take a screenshot of the current page and return its file path."""
    return await _run("screenshot")


@tool
async def browser_get_text(selector: str) -> str:
    """Get the visible text content of an element.

    Args:
        selector: An element ref from snapshot (@e1) or a CSS selector.
    """
    return await _run("get", "text", selector)


@tool
async def browser_scroll(direction: str) -> str:
    """Scroll the page.

    Args:
        direction: One of 'up', 'down', 'left', 'right'.
    """
    return await _run("scroll", direction)


@tool
async def browser_wait(milliseconds: int = 1000) -> str:
    """Wait for a number of milliseconds (useful after navigation or interactions).

    Args:
        milliseconds: How long to wait (default 1000 ms).
    """
    return await _run("wait", str(milliseconds))


@tool
async def browser_close() -> str:
    """Close the current browser session."""
    return await _run("close")


# ---------------------------------------------------------------------------
# Public list
# ---------------------------------------------------------------------------

BROWSER_TOOLS = [
    browser_open,
    browser_read,
    browser_snapshot,
    browser_click,
    browser_fill,
    browser_screenshot,
    browser_get_text,
    browser_scroll,
    browser_wait,
    browser_close,
]
