from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal

from perpmirror.enums import (
    Exchange,
    MarginMode,
    OrderSide,
    OrderStatus,
    PositionMode,
    PositionSide,
)
from perpmirror.exceptions import NonRetryableExchangeError, UnknownOrderState
from perpmirror.exchanges.base import EventCallback, ExchangeClient, StateCallback
from perpmirror.models import ZERO, InstrumentInfo, OrderRequest, OrderResult, PositionSnapshot


class FakeExchangeClient(ExchangeClient):
    exchange = Exchange.FAKE

    def __init__(
        self,
        *,
        equity: Decimal = Decimal("10000"),
        instruments: Iterable[InstrumentInfo] = (),
        positions: Iterable[PositionSnapshot] = (),
        position_mode: PositionMode = PositionMode.ONE_WAY,
        fill_ratio: Decimal = Decimal("1"),
    ) -> None:
        self.equity = equity
        self._instruments = {item.normalized_symbol: item for item in instruments}
        self._positions = {item.normalized_symbol: item for item in positions}
        self.position_mode = position_mode
        self.fill_ratio = fill_ratio
        self.orders: list[OrderRequest] = []
        self.order_results: dict[str, OrderResult] = {}
        self.timeout_after_fill = False
        self.reject_orders = False
        self._closed = asyncio.Event()

    async def get_server_time(self) -> int:
        return 0

    async def sync_time(self) -> int:
        return 0

    async def get_equity(self) -> Decimal:
        return self.equity

    async def get_positions(self) -> dict[str, PositionSnapshot]:
        return dict(self._positions)

    async def get_mark_price(self, normalized_symbol: str) -> Decimal:
        position = self._positions.get(normalized_symbol)
        if position is not None:
            return position.mark_price
        instrument = self._instruments.get(normalized_symbol)
        if instrument is None:
            raise NonRetryableExchangeError(f"unknown fake symbol: {normalized_symbol}")
        return Decimal("100")

    async def get_instruments(self) -> dict[str, InstrumentInfo]:
        return dict(self._instruments)

    async def get_position_mode(self) -> PositionMode:
        return self.position_mode

    async def get_margin_mode(self, normalized_symbol: str) -> MarginMode | None:
        position = self._positions.get(normalized_symbol)
        return position.margin_mode if position else None

    async def set_leverage(
        self, normalized_symbol: str, leverage: Decimal, margin_mode: MarginMode, side: PositionSide
    ) -> None:
        position = self._positions.get(normalized_symbol)
        if position:
            self._positions[normalized_symbol] = replace(position, leverage=leverage, margin_mode=margin_mode)

    async def set_margin_mode(self, normalized_symbol: str, margin_mode: MarginMode) -> None:
        position = self._positions.get(normalized_symbol)
        if position:
            self._positions[normalized_symbol] = replace(position, margin_mode=margin_mode)

    async def place_market_order(self, request: OrderRequest) -> OrderResult:
        self.orders.append(request)
        if self.reject_orders:
            raise NonRetryableExchangeError("simulated insufficient balance")
        instrument = self._instruments[request.normalized_symbol]
        price = await self.get_mark_price(request.normalized_symbol)
        filled = instrument.floor_quantity(request.quantity * self.fill_ratio)
        if filled <= ZERO:
            raise NonRetryableExchangeError("simulated zero fill")
        signed_delta = filled if request.side == OrderSide.BUY else -filled
        current = self._positions.get(request.normalized_symbol)
        current_signed = ZERO
        if current:
            current_signed = current.quantity if current.side == PositionSide.LONG else -current.quantity
        if request.reduce_only:
            if current_signed == ZERO or current_signed * signed_delta >= ZERO:
                raise NonRetryableExchangeError("reduce-only order cannot increase or flip a position")
            if abs(signed_delta) > abs(current_signed):
                signed_delta = -current_signed
                filled = abs(signed_delta)
        final_signed = current_signed + signed_delta
        if final_signed == ZERO:
            self._positions.pop(request.normalized_symbol, None)
        else:
            side = PositionSide.LONG if final_signed > ZERO else PositionSide.SHORT
            self._positions[request.normalized_symbol] = PositionSnapshot(
                exchange=self.exchange,
                symbol=instrument.symbol,
                normalized_symbol=instrument.normalized_symbol,
                side=side,
                quantity=abs(final_signed),
                notional_usdt=instrument.notional_for_quantity(abs(final_signed), price),
                entry_price=price,
                mark_price=price,
                leverage=current.leverage if current else Decimal("1"),
                margin_mode=request.margin_mode,
            )
        status = OrderStatus.FILLED if self.fill_ratio >= Decimal("1") else OrderStatus.PARTIALLY_FILLED
        result = OrderResult(
            exchange=self.exchange,
            symbol=request.symbol,
            client_order_id=request.client_order_id,
            order_id=str(len(self.orders)),
            status=status,
            filled_quantity=filled,
            average_price=price,
        )
        self.order_results[request.client_order_id] = result
        if self.timeout_after_fill:
            raise UnknownOrderState(request.client_order_id, "simulated timeout after exchange fill")
        return result

    async def close_position(self, position: PositionSnapshot, client_order_id: str) -> OrderResult:
        side = OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY
        return await self.place_market_order(
            OrderRequest(
                symbol=position.symbol,
                normalized_symbol=position.normalized_symbol,
                side=side,
                position_side=position.side,
                quantity=position.quantity,
                reduce_only=True,
                client_order_id=client_order_id,
                margin_mode=position.margin_mode,
            )
        )

    async def verify_order(self, symbol: str, client_order_id: str) -> OrderResult | None:
        return self.order_results.get(client_order_id)

    async def connect_private_ws(
        self, on_event: EventCallback, on_state: StateCallback | None = None
    ) -> None:
        if on_state:
            await on_state("connected")
        await self._closed.wait()

    async def close(self) -> None:
        self._closed.set()
