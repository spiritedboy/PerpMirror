"""Credential-free end-to-end sizing/reconciliation demonstration."""

import asyncio
from decimal import Decimal

from perpmirror.config import RiskConfig
from perpmirror.copy.calculator import TargetCalculator
from perpmirror.copy.reconciler import Reconciler
from perpmirror.enums import CopyMode, Exchange, MarginMode, PositionSide
from perpmirror.execution.executor import ExecutionEngine
from perpmirror.fake.exchange import FakeExchangeClient
from perpmirror.models import FollowerConfig, InstrumentInfo, PositionSnapshot
from perpmirror.risk.manager import RiskManager


async def run() -> None:
    instrument = InstrumentInfo(
        exchange=Exchange.FAKE,
        symbol="BTCUSDT",
        normalized_symbol="BTC-USDT-PERP",
        base_currency="BTC",
        quote_currency="USDT",
        settle_currency="USDT",
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    leader_position = PositionSnapshot(
        exchange=Exchange.FAKE,
        symbol="BTCUSDT",
        normalized_symbol="BTC-USDT-PERP",
        side=PositionSide.LONG,
        quantity=Decimal("30"),
        notional_usdt=Decimal("3000"),
        entry_price=Decimal("100"),
        mark_price=Decimal("100"),
        leverage=Decimal("10"),
        margin_mode=MarginMode.CROSS,
    )
    follower = FollowerConfig(
        id="fake_ratio",
        exchange=Exchange.FAKE,
        api_key_env="UNUSED",
        secret_key_env="UNUSED",
        passphrase_env=None,
        copy_mode=CopyMode.RATIO,
        fixed_margin_usdt=None,
        copy_ratio=Decimal("1"),
        copy_leverage=True,
        fixed_leverage=Decimal("5"),
        max_leverage=Decimal("20"),
        copy_margin_mode=True,
        margin_mode=MarginMode.CROSS,
    )
    target = TargetCalculator().calculate(
        follower,
        leader_position,
        Decimal("10000"),
        Decimal("2000"),
        "BTC-USDT-PERP",
    )
    exchange = FakeExchangeClient(equity=Decimal("2000"), instruments=[instrument])
    reconciler = Reconciler(
        risk_manager=RiskManager(
            RiskConfig(
                max_single_symbol_notional_usdt=Decimal("2000"),
                max_total_notional_usdt=Decimal("5000"),
                max_order_notional_usdt=Decimal("1000"),
            )
        ),
        executor=ExecutionEngine(dry_run=True),
        drift_percent=Decimal("1"),
        drift_min_usdt=Decimal("5"),
        max_retries=3,
        retry_base_delay=Decimal("0.5"),
        max_concurrent_orders=5,
    )
    result = await reconciler.reconcile(exchange, target)
    print(f"[DRY_RUN] action={result.action.value} target={result.target_notional}U")
    print(f"orders_submitted={len(exchange.orders)} (must be 0)")


if __name__ == "__main__":
    asyncio.run(run())
