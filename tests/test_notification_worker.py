import asyncio
from typing import Any

import pytest

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
    await worker.stop()
    assert notifier.calls == 1
    assert notifier.closed is True
