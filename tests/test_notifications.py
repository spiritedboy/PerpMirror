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
        assert card["schema"] == "2.0"
        assert "header" in card and "elements" in card["body"]
        assert "BTC-USDT-PERP" in encoded
        assert "okx_ratio" in encoded
        assert "多 · 0.00 U" in encoded
        assert "目标 600.00 U" in encoded
        assert "Order ID" not in encoded
        assert "Leader Equity" not in encoded
        assert "Follower Equity" not in encoded
        assert "Realized PnL" not in encoded


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
    assert card["schema"] == "2.0"
    encoded = json.dumps(card, ensure_ascii=False)
    assert "DRY RUN" in encoded
    assert "Leader · BINANCE · 1 仓" in encoded
    assert "f1 · OKX/FIXED · 0 仓" in encoded
    assert "Equity" not in encoded
    assert "WebSocket State" not in encoded


def test_dry_run_uses_target_as_compact_preview() -> None:
    encoded = json.dumps(
        FeishuCardBuilder().trade(event(ReconcileAction.OPEN), dry_run=True),
        ensure_ascii=False,
    )
    assert "模拟开仓" in encoded
    assert "0.00 U → 600.00 U" in encoded


def test_long_error_is_compacted() -> None:
    unsafe = replace(
        event(ReconcileAction.ORDER_FAILED),
        error_message="api_key=VISIBLE\n" + "failure " * 100,
    )
    encoded = json.dumps(FeishuCardBuilder().trade(unsafe), ensure_ascii=False)
    assert "VISIBLE" not in encoded
    assert len(encoded) < 1200


def test_fixed_card_shows_margin_times_leverage_as_notional() -> None:
    fixed = replace(
        event(ReconcileAction.OPEN),
        copy_mode=CopyMode.FIXED,
        fixed_margin=Decimal("20"),
        leverage=Decimal("20"),
        target_notional=Decimal("400"),
        order_notional=Decimal("400"),
        final_notional=Decimal("399"),
    )
    encoded = json.dumps(FeishuCardBuilder().trade(fixed), ensure_ascii=False)
    assert "保证金 20.00 U × 20.00x = 400.00 U" in encoded
