from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from decimal import Decimal

from perpmirror.enums import Exchange, MarginMode, PositionMode, PositionSide
from perpmirror.models import InstrumentInfo, OrderRequest, OrderResult, PositionSnapshot

EventCallback = Callable[[], Awaitable[None]]
StateCallback = Callable[[str], Awaitable[None]]


class ExchangeClient(ABC):
    exchange: Exchange

    @abstractmethod
    async def get_server_time(self) -> int: ...

    @abstractmethod
    async def sync_time(self) -> int: ...

    @abstractmethod
    async def get_equity(self) -> Decimal: ...

    @abstractmethod
    async def get_positions(self) -> dict[str, PositionSnapshot]: ...

    async def get_position(self, normalized_symbol: str) -> PositionSnapshot | None:
        return (await self.get_positions()).get(normalized_symbol)

    @abstractmethod
    async def get_mark_price(self, normalized_symbol: str) -> Decimal: ...

    @abstractmethod
    async def get_instruments(self) -> dict[str, InstrumentInfo]: ...

    async def get_instrument(self, normalized_symbol: str) -> InstrumentInfo | None:
        return (await self.get_instruments()).get(normalized_symbol)

    @abstractmethod
    async def get_position_mode(self) -> PositionMode: ...

    @abstractmethod
    async def get_margin_mode(self, normalized_symbol: str) -> MarginMode | None: ...

    @abstractmethod
    async def set_leverage(
        self, normalized_symbol: str, leverage: Decimal, margin_mode: MarginMode, side: PositionSide
    ) -> None: ...

    @abstractmethod
    async def set_margin_mode(self, normalized_symbol: str, margin_mode: MarginMode) -> None: ...

    @abstractmethod
    async def place_market_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    async def close_position(self, position: PositionSnapshot, client_order_id: str) -> OrderResult: ...

    @abstractmethod
    async def verify_order(self, symbol: str, client_order_id: str) -> OrderResult | None: ...

    @abstractmethod
    async def connect_private_ws(
        self, on_event: EventCallback, on_state: StateCallback | None = None
    ) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
