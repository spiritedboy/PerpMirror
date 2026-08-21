from dataclasses import replace
from decimal import Decimal

import pytest
from conftest import make_follower, make_position, make_reconciler

from perpmirror.config import RiskConfig
from perpmirror.copy.calculator import TargetCalculator
from perpmirror.enums import CopyMode, OrderSide, PositionSide, ReconcileAction
from perpmirror.fake.exchange import FakeExchangeClient


def target_for(notional: str, side: PositionSide = PositionSide.LONG):
    leader = make_position(notional, side)
    follower = make_follower(CopyMode.RATIO)
    # leader equity 10k, follower equity 10k => target equals leader notional
    return TargetCalculator().calculate(follower, leader, Decimal("10000"), Decimal("10000"), "BTC-USDT-PERP")


@pytest.mark.asyncio
async def test_open_then_idempotent_noop(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument])
    reconciler = make_reconciler()
    target = target_for("200")
    first = await reconciler.reconcile(client, target)
    second = await reconciler.reconcile(client, target)
    assert first.action == ReconcileAction.OPEN
    assert second.action == ReconcileAction.NOOP
    assert len(client.orders) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actual", "target", "expected", "order_side", "reduce_only"),
    [
        ("600", "1000", ReconcileAction.ADD, OrderSide.BUY, False),
        ("1000", "200", ReconcileAction.REDUCE, OrderSide.SELL, True),
    ],
)
async def test_long_add_and_reduce(instrument, actual, target, expected, order_side, reduce_only) -> None:
    client = FakeExchangeClient(instruments=[instrument], positions=[make_position(actual)])
    result = await make_reconciler().reconcile(client, target_for(target))
    assert result.action == expected
    assert client.orders[-1].side == order_side
    assert client.orders[-1].reduce_only is reduce_only


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actual", "target", "expected", "order_side", "reduce_only"),
    [
        ("200", "600", ReconcileAction.ADD, OrderSide.SELL, False),
        ("600", "200", ReconcileAction.REDUCE, OrderSide.BUY, True),
    ],
)
async def test_short_add_and_reduce(instrument, actual, target, expected, order_side, reduce_only) -> None:
    client = FakeExchangeClient(
        instruments=[instrument], positions=[make_position(actual, PositionSide.SHORT)]
    )
    result = await make_reconciler().reconcile(client, target_for(target, PositionSide.SHORT))
    assert result.action == expected
    assert client.orders[-1].side == order_side
    assert client.orders[-1].reduce_only is reduce_only


@pytest.mark.asyncio
async def test_close_is_reduce_only(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument], positions=[make_position("200")])
    target = TargetCalculator().calculate(
        make_follower(), None, Decimal("10000"), Decimal("2000"), "BTC-USDT-PERP"
    )
    result = await make_reconciler().reconcile(client, target)
    assert result.action == ReconcileAction.CLOSE
    assert client.orders[0].reduce_only is True
    assert await client.get_position("BTC-USDT-PERP") is None


@pytest.mark.asyncio
async def test_flip_closes_verifies_then_opens(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument], positions=[make_position("600")])
    result = await make_reconciler().reconcile(client, target_for("300", PositionSide.SHORT))
    final = await client.get_position("BTC-USDT-PERP")
    assert result.action == ReconcileAction.FLIP
    assert [order.reduce_only for order in client.orders] == [True, False]
    assert final is not None and final.side == PositionSide.SHORT
    assert final.abs_notional == Decimal("300")


@pytest.mark.asyncio
async def test_drift_below_threshold_is_noop(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument], positions=[make_position("996")])
    result = await make_reconciler().reconcile(client, target_for("1000"))
    assert result.action == ReconcileAction.NOOP
    assert not client.orders


@pytest.mark.asyncio
async def test_risk_blocks_increase_but_allows_close(instrument) -> None:
    risk = RiskConfig(
        max_single_symbol_notional_usdt=Decimal("500"),
        max_total_notional_usdt=Decimal("500"),
        max_order_notional_usdt=Decimal("500"),
    )
    client = FakeExchangeClient(instruments=[instrument])
    blocked = await make_reconciler(risk=risk).reconcile(client, target_for("600"))
    assert blocked.action == ReconcileAction.RISK_BLOCKED
    client = FakeExchangeClient(instruments=[instrument], positions=[make_position("600")])
    flat = TargetCalculator().calculate(
        make_follower(), None, Decimal("10000"), Decimal("1000"), "BTC-USDT-PERP"
    )
    closed = await make_reconciler(risk=risk).reconcile(client, flat)
    assert closed.action == ReconcileAction.CLOSE


@pytest.mark.asyncio
async def test_unknown_order_timeout_after_fill_does_not_duplicate(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument])
    client.timeout_after_fill = True
    result = await make_reconciler().reconcile(client, target_for("200"))
    assert result.action == ReconcileAction.OPEN
    assert result.success is True
    assert len(client.orders) == 1


@pytest.mark.asyncio
async def test_partial_fills_converge_by_reconciliation(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument], fill_ratio=Decimal("0.5"))
    result = await make_reconciler(retries=8).reconcile(client, target_for("1000"))
    final = await client.get_position("BTC-USDT-PERP")
    assert result.success is True
    assert final is not None
    assert abs(final.abs_notional - Decimal("1000")) <= Decimal("10")
    assert len(client.orders) > 1


@pytest.mark.asyncio
async def test_exchange_market_order_max_is_chunked_for_open_and_close(instrument) -> None:
    capped = replace(instrument, max_quantity=Decimal("1"))
    client = FakeExchangeClient(instruments=[capped])
    reconciler = make_reconciler(retries=12)

    opened = await reconciler.reconcile(client, target_for("1000"))
    assert opened.success is True
    assert len(client.orders) == 10
    assert all(order.quantity <= Decimal("1") for order in client.orders)

    flat = TargetCalculator().calculate(
        make_follower(), None, Decimal("10000"), Decimal("1000"), "BTC-USDT-PERP"
    )
    closed = await reconciler.reconcile(client, flat)
    assert closed.success is True
    assert await client.get_position("BTC-USDT-PERP") is None
    assert len(client.orders) == 20
    assert all(order.quantity <= Decimal("1") for order in client.orders)


@pytest.mark.asyncio
async def test_dry_run_never_mutates_exchange(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument])
    result = await make_reconciler(dry_run=True).reconcile(client, target_for("200"))
    assert result.action == ReconcileAction.OPEN
    assert not client.orders
    assert await client.get_position("BTC-USDT-PERP") is None
