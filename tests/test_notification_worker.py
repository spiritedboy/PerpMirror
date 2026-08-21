import asyncio
from typing import Any

import pytest

from perpmirror.exceptions import RetryableExchangeError
from perpmirror.notifications.base import Notifier
from perpmirror.notifications.worker import NotificationWorker


class FailingNotifier(Notifier):
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    async def send_card(self, card: dict[str, Any]) -> None:
        self.calls += 1
        raise TimeoutError("simulated Feishu timeout")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_notification_failure_is_contained_and_worker_stops() -> None:
    notifier = FailingNotifier()
    worker = NotificationWorker(notifier, max_retries=1)
    worker.start()
    worker.publish({"header": {}})
    await asyncio.wait_for(worker.queue.join(), timeout=1)
    worker.publish({"header": {"second": True}})
    await worker.stop()
    assert notifier.calls == 1
    assert notifier.closed is True


class EventuallySuccessfulNotifier(Notifier):
    def __init__(self) -> None:
        self.calls = 0

    async def send_card(self, card: dict[str, Any]) -> None:
        self.calls += 1
        if self.calls < 3:
            raise RetryableExchangeError("temporary failure")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_retryable_notification_error_still_retries() -> None:
    notifier = EventuallySuccessfulNotifier()
    worker = NotificationWorker(notifier, max_retries=3)
    worker.start()
    worker.publish({"header": {}})
    await asyncio.wait_for(worker.queue.join(), timeout=5)
    await worker.stop()
    assert notifier.calls == 3
