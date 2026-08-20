from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Notifier(ABC):
    @abstractmethod
    async def send_card(self, card: dict[str, Any]) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class NullNotifier(Notifier):
    async def send_card(self, card: dict[str, Any]) -> None:
        return None

    async def close(self) -> None:
        return None
