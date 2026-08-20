from __future__ import annotations

from decimal import Decimal

import pytest

from perpmirror.config import RiskConfig
from perpmirror.copy.reconciler import Reconciler
from perpmirror.enums import CopyMode, Exchange, MarginMode, PositionSide
from perpmirror.execution.executor import ExecutionEngine
from perpmirror.models import FollowerConfig, InstrumentInfo, PositionSnapshot
from perpmirror.risk.manager import RiskManager


@pytest.fixture
def instrument() -> InstrumentInfo:
    return InstrumentInfo(
        exchange=Exchange.FAKE,
        symbol="BTCUSDT",
        normalized_symbol="BTC-USDT-PERP",
        base_currency="BTC",
        quote_currency="USDT",
        settle_currency="USDT",
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
        tick_size=Decimal("0.1"),
    )


def make_position(
    notional: str,
    side: PositionSide = PositionSide.LONG,
    *,
    price: str = "100",
    symbol: str = "BTC-USDT-PERP",
) -> PositionSnapshot:
    value = Decimal(notional)
    mark = Decimal(price)
    return PositionSnapshot(
        exchange=Exchange.FAKE,
        symbol="BTCUSDT",
        normalized_symbol=symbol,
        side=side,
        quantity=value / mark,
        notional_usdt=value,
        entry_price=mark,
        mark_price=mark,
        leverage=Decimal("10"),
        margin_mode=MarginMode.CROSS,
    )


def make_follower(mode: CopyMode = CopyMode.FIXED) -> FollowerConfig:
    return FollowerConfig(
        id="follower1",
        exchange=Exchange.FAKE,
        api_key_env="KEY",
        secret_key_env="SECRET",
        passphrase_env=None,
        copy_mode=mode,
        fixed_margin_usdt=Decimal("20") if mode == CopyMode.FIXED else None,
        copy_ratio=Decimal("1") if mode == CopyMode.RATIO else None,
        copy_leverage=True,
        fixed_leverage=Decimal("5"),
        max_leverage=Decimal("20"),
        copy_margin_mode=True,
        margin_mode=MarginMode.CROSS,
    )


def make_reconciler(
    *,
    risk: RiskConfig | None = None,
    dry_run: bool = False,
    retries: int = 3,
) -> Reconciler:
    return Reconciler(
        risk_manager=RiskManager(
            risk
            or RiskConfig(
                max_single_symbol_notional_usdt=Decimal("10000"),
                max_total_notional_usdt=Decimal("20000"),
                max_order_notional_usdt=Decimal("10000"),
            )
        ),
        executor=ExecutionEngine(dry_run=dry_run),
        drift_percent=Decimal("1"),
        drift_min_usdt=Decimal("5"),
        max_retries=retries,
        retry_base_delay=Decimal("0"),
        max_concurrent_orders=5,
    )
