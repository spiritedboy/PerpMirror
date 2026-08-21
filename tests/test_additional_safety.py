from decimal import Decimal

import pytest
from conftest import make_follower, make_position, make_reconciler

from perpmirror.config import RiskConfig
from perpmirror.copy.calculator import TargetCalculator
from perpmirror.enums import CopyMode, PositionSide, ReconcileAction
from perpmirror.fake.exchange import FakeExchangeClient


def target(notional: str, side: PositionSide):
    return TargetCalculator().calculate(
        make_follower(CopyMode.RATIO),
        make_position(notional, side),
        Decimal("10000"),
        Decimal("10000"),
        "BTC-USDT-PERP",
    )


@pytest.mark.asyncio
async def test_flat_to_short(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument])
    result = await make_reconciler().reconcile(client, target("200", PositionSide.SHORT))
    assert result.action == ReconcileAction.OPEN
    assert (await client.get_position("BTC-USDT-PERP")).side == PositionSide.SHORT


@pytest.mark.asyncio
async def test_short_to_long_flip(instrument) -> None:
    client = FakeExchangeClient(
        instruments=[instrument], positions=[make_position("200", PositionSide.SHORT)]
    )
    result = await make_reconciler().reconcile(client, target("100", PositionSide.LONG))
    assert result.action == ReconcileAction.FLIP
    assert [order.reduce_only for order in client.orders] == [True, False]


@pytest.mark.asyncio
async def test_restart_with_matching_real_position_is_noop(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument], positions=[make_position("600")])
    result = await make_reconciler().reconcile(client, target("600", PositionSide.LONG))
    assert result.action == ReconcileAction.NOOP
    assert client.orders == []


@pytest.mark.asyncio
async def test_insufficient_balance_is_order_failed_without_loop(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument])
    client.reject_orders = True
    result = await make_reconciler().reconcile(client, target("200", PositionSide.LONG))
    assert result.action == ReconcileAction.ORDER_FAILED
    assert len(client.orders) == 1


@pytest.mark.asyncio
async def test_repeated_order_failure_enters_notification_cooldown(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument])
    client.reject_orders = True
    reconciler = make_reconciler()
    first = await reconciler.reconcile(client, target("200", PositionSide.LONG))
    second = await reconciler.reconcile(client, target("200", PositionSide.LONG))

    assert first.action == ReconcileAction.ORDER_FAILED
    assert first.notification_suppressed is False
    assert second.action == ReconcileAction.ORDER_FAILED
    assert second.notification_suppressed is True
    assert len(client.orders) == 1


@pytest.mark.asyncio
async def test_failure_cooldown_does_not_block_changed_target(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument])
    client.reject_orders = True
    reconciler = make_reconciler()
    await reconciler.reconcile(client, target("200", PositionSide.LONG))
    await reconciler.reconcile(client, target("300", PositionSide.LONG))

    assert len(client.orders) == 2


@pytest.mark.asyncio
async def test_failure_cooldown_ignores_small_ratio_target_drift(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument])
    client.reject_orders = True
    reconciler = make_reconciler()
    await reconciler.reconcile(client, target("200", PositionSide.LONG))
    second = await reconciler.reconcile(client, target("203", PositionSide.LONG))

    assert second.notification_suppressed is True
    assert len(client.orders) == 1


@pytest.mark.asyncio
async def test_failure_cooldown_never_blocks_position_close(instrument) -> None:
    client = FakeExchangeClient(instruments=[instrument])
    client.reject_orders = True
    reconciler = make_reconciler()
    await reconciler.reconcile(client, target("200", PositionSide.LONG))

    client.reject_orders = False
    client._positions["BTC-USDT-PERP"] = make_position("100")
    flat = TargetCalculator().calculate(
        make_follower(), None, Decimal("10000"), Decimal("1000"), "BTC-USDT-PERP"
    )
    result = await reconciler.reconcile(client, flat)

    assert result.action == ReconcileAction.CLOSE
    assert await client.get_position("BTC-USDT-PERP") is None


@pytest.mark.asyncio
async def test_allow_short_false_blocks_short_open(instrument) -> None:
    risk = RiskConfig(
        allow_short=False,
        max_single_symbol_notional_usdt=Decimal("1000"),
        max_total_notional_usdt=Decimal("1000"),
        max_order_notional_usdt=Decimal("1000"),
    )
    client = FakeExchangeClient(instruments=[instrument])
    result = await make_reconciler(risk=risk).reconcile(client, target("200", PositionSide.SHORT))
    assert result.action == ReconcileAction.RISK_BLOCKED
    assert not client.orders


@pytest.mark.asyncio
async def test_kill_switch_blocks_increase(instrument) -> None:
    risk = RiskConfig(
        kill_switch=True,
        kill_switch_close_positions=True,
        max_single_symbol_notional_usdt=Decimal("1000"),
        max_total_notional_usdt=Decimal("1000"),
        max_order_notional_usdt=Decimal("1000"),
    )
    client = FakeExchangeClient(instruments=[instrument])
    result = await make_reconciler(risk=risk).reconcile(client, target("200", PositionSide.LONG))
    assert result.action == ReconcileAction.RISK_BLOCKED


@pytest.mark.asyncio
async def test_kill_switch_close_positions_allows_close(instrument) -> None:
    risk = RiskConfig(
        kill_switch=True,
        kill_switch_close_positions=True,
        max_single_symbol_notional_usdt=Decimal("1000"),
        max_total_notional_usdt=Decimal("1000"),
        max_order_notional_usdt=Decimal("1000"),
    )
    client = FakeExchangeClient(instruments=[instrument], positions=[make_position("200")])
    flat = TargetCalculator().calculate(
        make_follower(), None, Decimal("10000"), Decimal("1000"), "BTC-USDT-PERP"
    )
    result = await make_reconciler(risk=risk).reconcile(client, flat)
    assert result.action == ReconcileAction.CLOSE
