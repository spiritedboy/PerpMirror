from __future__ import annotations

import asyncio
import logging
from typing import Any

from perpmirror.notifications.base import Notifier

logger = logging.getLogger(__name__)


class NotificationWorker:
    def __init__(self, notifier: Notifier, *, max_retries: int = 3, queue_size: int = 1000) -> None:
        self.notifier = notifier
        self.max_retries = max_retries
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="notification-worker")

    def publish(self, card: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(card)
        except asyncio.QueueFull:
            logger.error("notification_queue_full card_dropped=true")

    async def _run(self) -> None:
        while True:
            card = await self.queue.get()
            try:
                if card is None:
                    return
                for attempt in range(self.max_retries):
                    try:
                        await self.notifier.send_card(card)
                        break
                    except Exception as exc:
                        if attempt + 1 >= self.max_retries:
                            logger.error("notification_delivery_failed error=%s", exc)
                            break
                        await asyncio.sleep(float(2**attempt))
            finally:
                self.queue.task_done()

    async def stop(self) -> None:
        if self._task is None:
            await self.notifier.close()
            return
        await self.queue.join()
        await self.queue.put(None)
        await self._task
        self._task = None
        await self.notifier.close()
