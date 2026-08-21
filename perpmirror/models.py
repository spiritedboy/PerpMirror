from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from perpmirror.enums import (
    CopyMode,
    Exchange,
    MarginMode,
    OrderSide,
    OrderStatus,
    PositionMode,
    PositionSide,
    ReconcileAction,
)

ZERO = Decimal("0")


def decimal(value: Any, default: Decimal = ZERO) -> Decimal:
    if value is None or value == "":
        return default
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    """Tradable instrument metadata with an explicit native-order quantity unit."""
    exchange: Exchange
    symbol: str
    normalized_symbol: str
    base_currency: str
    quote_currency: str
    settle_currency: str
    quantity_step: Decimal
    min_quantity: Decimal
    max_quantity: Decimal | None = None
    min_notional: Decimal = ZERO
    tick_size: Decimal = ZERO
    contract_value: Decimal = Decimal("1")
    contract_multiplier: Decimal = Decimal("1")
    contract_type: str = "linear"
    active: bool = True

    @property
    def base_per_quantity(self) -> Decimal:
        return self.contract_value * self.contract_multiplier

    def quantity_for_notional(self, notional: Decimal, price: Decimal) -> Decimal:
        if price <= ZERO or self.base_per_quantity <= ZERO:
            raise ValueError("price and contract value must be positive")
        raw = abs(notional) / (price * self.base_per_quantity)
        return self.floor_quantity(raw)

    def notional_for_quantity(self, quantity: Decimal, price: Decimal) -> Decimal:
        return abs(quantity) * price * self.base_per_quantity

    def floor_quantity(self, quantity: Decimal) -> Decimal:
        if self.quantity_step <= ZERO:
            raise ValueError("quantity step must be positive")
        units = (abs(quantity) / self.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        return units * self.quantity_step


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Position normalized to USDT while retaining native exchange order quantity.

    ``quantity`` is base-asset quantity on Binance USD-M and contract count on
    OKX SWAP. ``notional_usdt`` is the cross-exchange field used for sizing and
    risk. Keeping both prevents an OKX contract count from being mistaken for
    an underlying-asset quantity when closing a position.
    """
    exchange: Exchange
    symbol: str
    normalized_symbol: str
    side: PositionSide
    quantity: Decimal
    notional_usdt: Decimal
    entry_price: Decimal | None
    mark_price: Decimal
    leverage: Decimal
    margin_mode: MarginMode
    unrealized_pnl: Decimal | None = None
    liquidation_price: Decimal | None = None

    @property
    def abs_notional(self) -> Decimal:
        return abs(self.notional_usdt)

    @property
    def signed_notional(self) -> Decimal:
        if self.side == PositionSide.LONG:
            return self.abs_notional
        if self.side == PositionSide.SHORT:
            return -self.abs_notional
        return ZERO


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    exchange: Exchange
    equity_usdt: Decimal
    positions: tuple[PositionSnapshot, ...] = ()
    position_mode: PositionMode = PositionMode.ONE_WAY
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class FollowerConfig:
    id: str
    exchange: Exchange
    api_key_env: str
    secret_key_env: str
    passphrase_env: str | None
    copy_mode: CopyMode
    fixed_margin_usdt: Decimal | None
    copy_ratio: Decimal | None
    copy_leverage: bool
    fixed_leverage: Decimal
    max_leverage: Decimal
    copy_margin_mode: bool
    margin_mode: MarginMode


@dataclass(frozen=True, slots=True)
class FollowerTarget:
    follower_id: str
    exchange: Exchange
    symbol: str
    side: PositionSide
    copy_mode: CopyMode
    target_notional: Decimal
    target_margin: Decimal | None
    target_leverage: Decimal
    margin_mode: MarginMode
    leader_notional: Decimal
    leader_equity: Decimal
    leader_exposure_ratio: Decimal | None
    follower_equity: Decimal
    copy_ratio: Decimal | None = None
    fixed_margin: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    normalized_symbol: str
    side: OrderSide
    position_side: PositionSide
    quantity: Decimal
    reduce_only: bool
    client_order_id: str
    margin_mode: MarginMode


@dataclass(frozen=True, slots=True)
class OrderResult:
    exchange: Exchange
    symbol: str
    client_order_id: str
    status: OrderStatus
    order_id: str | None = None
    filled_quantity: Decimal = ZERO
    average_price: Decimal | None = None
    raw_status: str | None = None


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    rule: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    follower_id: str
    symbol: str
    action: ReconcileAction
    target_notional: Decimal
    previous_notional: Decimal
    final_notional: Decimal | None
    order_results: tuple[OrderResult, ...] = ()
    success: bool = True
    message: str | None = None
    notification_suppressed: bool = False


@dataclass(frozen=True, slots=True)
class TradeNotification:
    follower_id: str
    exchange: Exchange
    symbol: str
    action: ReconcileAction
    side: PositionSide
    copy_mode: CopyMode
    leader_equity: Decimal | None
    leader_notional: Decimal | None
    leader_exposure_ratio: Decimal | None
    follower_equity: Decimal | None
    copy_ratio: Decimal | None
    fixed_margin: Decimal | None
    leverage: Decimal | None
    previous_notional: Decimal
    target_notional: Decimal
    order_notional: Decimal
    final_notional: Decimal | None
    entry_price: Decimal | None = None
    exit_price: Decimal | None = None
    realized_pnl: Decimal | None = None
    order_id: str | None = None
    success: bool = True
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
