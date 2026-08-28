"""
backend/tools/subprocess_compat.py
Event-loop tolerant subprocess helpers for J.A.R.V.I.S.

Background:
  On Windows, uvicorn's `--reload` supervisor uses a SelectorEventLoop, where
  asyncio's integrated subprocess transports are NOT supported — every call to
  `asyncio.create_subprocess_exec/_shell` raises a bare NotImplementedError.
  These helpers keep every tool functional under ANY event loop type.
"""

import asyncio
import os
from typing import Any, Awaitable, Callable, Optional, Sequence

import subprocess


def _is_windows() -> bool:
    return os.name == "nt"


async def run_process(
    args: Sequence[str],
    timeout: float = 60.0,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """
    Runs a simple blocking subprocess without needing loop-integrated transports.

    Usable from BOTH Proactor and Selector loops because the blocking call is
    offloaded to a worker thread. Returns subprocess.CompletedProcess with
    .stdout/.stderr bytes (empty when capture_output=False).
    """
    cmd = [str(a) for a in args]

    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )

    return await asyncio.to_thread(_run)


def decode_stream(raw: Optional[bytes]) -> str:
    """Safely decodes subprocess byte streams into trimmed human text."""
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace").strip()


async def run_in_subprocess_capable_loop(factory: Callable[[], Awaitable[Any]]) -> Any:
    """
    Executes a coroutine-factory inside a dedicated worker thread whose PRIVATE
    event loop always supports subprocess transports on Windows (Proactor).

    Callers may freely use create_subprocess_exec, stream reads and wait_for()
    inside the provided async factory, exactly as they would originally — this
    bridge exists only to escape a host loop (uvicorn --reload) that lacks
    subprocess support. Results/exceptions propagate transparently.
    """

    def _runner() -> Any:
        loop: asyncio.AbstractEventLoop
        if _is_windows():
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(factory())
        finally:
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass
            loop.close()

    return await asyncio.to_thread(_runner)
