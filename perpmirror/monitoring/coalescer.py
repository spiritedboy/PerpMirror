from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class ReconcileCoalescer:
    """Coalesces bursts and guarantees one pending run after an in-flight run."""

    def __init__(self, callback: Callable[[], Awaitable[None]], debounce_seconds: float) -> None:
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._event = asyncio.Event()
        self._stopped = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="reconcile-coalescer")

    async def trigger(self) -> None:
        self._event.set()

    async def _run(self) -> None:
        while not self._stopped.is_set():
            await self._event.wait()
            if self._stopped.is_set():
                return
            self._event.clear()
            if self.debounce_seconds > 0:
                await asyncio.sleep(self.debounce_seconds)
                self._event.clear()
            try:
                await self.callback()
            except Exception:
                logger.exception("coalesced reconciliation failed")
            # A trigger during callback stays set and causes a fresh full snapshot.

    async def stop(self) -> None:
        self._stopped.set()
        self._event.set()
        if self._task:
            await self._task
            self._task = None
