import json
from dataclasses import replace
from decimal import Decimal

from perpmirror.enums import CopyMode, Exchange, PositionSide, ReconcileAction
from perpmirror.models import TradeNotification
from perpmirror.notifications.cards import FeishuCardBuilder


def event(action: ReconcileAction) -> TradeNotification:
    return TradeNotification(
        follower_id="okx_ratio",
        exchange=Exchange.OKX,
        symbol="BTC-USDT-PERP",
        action=action,
        side=PositionSide.LONG,
        copy_mode=CopyMode.RATIO,
        leader_equity=Decimal("10000"),
        leader_notional=Decimal("3000"),
        leader_exposure_ratio=Decimal("0.3"),
        follower_equity=Decimal("2000"),
        copy_ratio=Decimal("1"),
        fixed_margin=None,
        leverage=Decimal("10"),
        previous_notional=Decimal("0"),
        target_notional=Decimal("600"),
        order_notional=Decimal("600"),
        final_notional=Decimal("598.8"),
        order_id="123",
    )


def test_all_trade_and_failure_cards_are_valid_json() -> None:
    builder = FeishuCardBuilder()
    for action in (
        ReconcileAction.OPEN,
        ReconcileAction.ADD,
        ReconcileAction.REDUCE,
        ReconcileAction.CLOSE,
        ReconcileAction.FLIP,
        ReconcileAction.ORDER_FAILED,
        ReconcileAction.RISK_BLOCKED,
    ):
        card = builder.trade(event(action))
        encoded = json.dumps(card, ensure_ascii=False)
        assert "header" in card and "elements" in card
        assert "BTC-USDT-PERP" in encoded


def test_error_card_redacts_secrets() -> None:
    unsafe = replace(
        event(ReconcileAction.ORDER_FAILED),
        error_message="api_key=VISIBLE secret=VISIBLE signature=VISIBLE",
    )
    encoded = json.dumps(FeishuCardBuilder().trade(unsafe))
    assert "VISIBLE" not in encoded


def test_startup_card_marks_dry_run() -> None:
    card = FeishuCardBuilder.startup(
        dry_run=True,
        leader_exchange="binance",
        leader_equity=Decimal("10000"),
        leader_positions=1,
        followers=[("f1", "okx", "fixed", Decimal("2000"), 0)],
        reconcile_interval=Decimal("10"),
    )
    assert "DRY RUN" in json.dumps(card, ensure_ascii=False)
