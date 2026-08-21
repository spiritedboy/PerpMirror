from __future__ import annotations

import secrets
from decimal import Decimal

from perpmirror.enums import OrderSide, OrderStatus, PositionSide
from perpmirror.exceptions import UnknownOrderState, UnsafeOperation
from perpmirror.exchanges.base import ExchangeClient
from perpmirror.models import (
    ZERO,
    FollowerTarget,
    InstrumentInfo,
    OrderRequest,
    OrderResult,
    PositionSnapshot,
)


class ExecutionEngine:
    def __init__(self, *, dry_run: bool) -> None:
        self.dry_run = dry_run

    @staticmethod
    def client_order_id(_follower_id: str, _symbol: str, action: str) -> str:
        # OKX accepts only case-sensitive alphanumeric clOrdId values up to
        # 32 characters. This subset is also valid for Binance newClientOrderId.
        return f"pm{action[:1]}{secrets.token_hex(14)}"

    async def execute_delta(
        self,
        client: ExchangeClient,
        instrument: InstrumentInfo,
        target: FollowerTarget,
        actual: PositionSnapshot | None,
        order_notional: Decimal,
        *,
        reduce_only: bool,
    ) -> OrderResult:
        price = await client.get_mark_price(target.symbol)
        quantity = instrument.quantity_for_notional(order_notional, price)
        if reduce_only and actual is not None:
            quantity = min(quantity, actual.quantity)
        if quantity <= ZERO or quantity < instrument.min_quantity:
            raise UnsafeOperation("normalized order quantity is below the instrument minimum")
        normalized_notional = instrument.notional_for_quantity(quantity, price)
        if not reduce_only and normalized_notional < max(instrument.min_notional, ZERO):
            raise UnsafeOperation("normalized order notional is below the exchange minimum")
        position_side: PositionSide
        if reduce_only:
            if actual is None or actual.side == PositionSide.FLAT:
                raise UnsafeOperation("cannot reduce a flat position")
            side = OrderSide.SELL if actual.side == PositionSide.LONG else OrderSide.BUY
            position_side = actual.side
        else:
            side = OrderSide.BUY if target.side == PositionSide.LONG else OrderSide.SELL
            position_side = target.side
        client_id = self.client_order_id(
            target.follower_id, target.symbol, "reduce" if reduce_only else "increase"
        )
        request = OrderRequest(
            symbol=instrument.symbol,
            normalized_symbol=target.symbol,
            side=side,
            position_side=position_side,
            quantity=quantity,
            reduce_only=reduce_only,
            client_order_id=client_id,
            margin_mode=target.margin_mode,
        )
        if self.dry_run:
            return OrderResult(
                exchange=client.exchange,
                symbol=instrument.symbol,
                client_order_id=client_id,
                status=OrderStatus.DRY_RUN,
                filled_quantity=ZERO,
                raw_status=f"would_{side.value}_{quantity}",
            )
        if not reduce_only:
            await client.set_margin_mode(target.symbol, target.margin_mode)
            await client.set_leverage(target.symbol, target.target_leverage, target.margin_mode, target.side)
        try:
            return await client.place_market_order(request)
        except UnknownOrderState as exc:
            verified = await client.verify_order(instrument.symbol, exc.client_order_id)
            if verified is not None and verified.status in {
                OrderStatus.FILLED,
                OrderStatus.PARTIALLY_FILLED,
            }:
                return verified
            # Re-reading the position is intentional; callers reconcile from truth and do not blindly retry.
            await client.get_position(target.symbol)
            raise

    async def close(
        self, client: ExchangeClient, target: FollowerTarget, actual: PositionSnapshot
    ) -> OrderResult:
        client_id = self.client_order_id(target.follower_id, target.symbol, "close")
        if self.dry_run:
            return OrderResult(
                exchange=client.exchange,
                symbol=actual.symbol,
                client_order_id=client_id,
                status=OrderStatus.DRY_RUN,
                raw_status="would_close_reduce_only",
            )
        try:
            return await client.close_position(actual, client_id)
        except UnknownOrderState as exc:
            verified = await client.verify_order(actual.symbol, exc.client_order_id)
            if verified is not None and verified.status in {
                OrderStatus.FILLED,
                OrderStatus.PARTIALLY_FILLED,
            }:
                return verified
            await client.get_position(target.symbol)
            raise
