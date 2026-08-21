from __future__ import annotations

import asyncio
import logging
from typing import Any

from perpmirror.exceptions import RetryableExchangeError
from perpmirror.notifications.base import Notifier

logger = logging.getLogger(__name__)


class NotificationWorker:
    def __init__(self, notifier: Notifier, *, max_retries: int = 3, queue_size: int = 1000) -> None:
        self.notifier = notifier
        self.max_retries = max_retries
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task[None] | None = None
        self._disabled = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="notification-worker")

    def publish(self, card: dict[str, Any]) -> None:
        title = self._title(card)
        if self._disabled:
            logger.error("NOTIFICATION_DROPPED reason=channel_disabled title=%s", title)
            return
        try:
            self.queue.put_nowait(card)
            logger.info(
                "NOTIFICATION_QUEUED title=%s queue_size=%s",
                title,
                self.queue.qsize(),
            )
        except asyncio.QueueFull:
            logger.error("NOTIFICATION_DROPPED reason=queue_full title=%s", title)

    async def _run(self) -> None:
        while True:
            card = await self.queue.get()
            try:
                if card is None:
                    return
                if self._disabled:
                    continue
                for attempt in range(self.max_retries):
                    try:
                        await self.notifier.send_card(card)
                        logger.info("NOTIFICATION_DELIVERED title=%s", self._title(card))
                        break
                    except RetryableExchangeError as exc:
                        if attempt + 1 >= self.max_retries:
                            logger.error("notification_delivery_failed error=%s", exc)
                            break
                        await asyncio.sleep(float(2**attempt))
                    except Exception as exc:
                        self._disabled = True
                        logger.error("notification_disabled permanent_error=true error=%s", exc)
                        break
            finally:
                self.queue.task_done()

    @staticmethod
    def _title(card: dict[str, Any]) -> str:
        header = card.get("header")
        if not isinstance(header, dict):
            return "unknown"
        title = header.get("title")
        if not isinstance(title, dict):
            return "unknown"
        content = str(title.get("content", "unknown"))
        return " ".join(content.split())[:100]

    async def stop(self) -> None:
        if self._task is None:
            await self.notifier.close()
            return
        await self.queue.join()
        await self.queue.put(None)
        await self._task
        self._task = None
        await self.notifier.close()
