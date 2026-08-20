from dataclasses import replace
from decimal import Decimal

from conftest import make_follower, make_position

from perpmirror.copy.calculator import TargetCalculator
from perpmirror.enums import CopyMode, PositionSide


def test_ratio_target_uses_equity_exposure() -> None:
    target = TargetCalculator().calculate(
        make_follower(CopyMode.RATIO),
        make_position("3000"),
        Decimal("10000"),
        Decimal("2000"),
        "BTC-USDT-PERP",
    )
    assert target.target_notional == Decimal("600")
    assert target.leader_exposure_ratio == Decimal("0.3")


def test_ratio_copy_ratio_and_leverage_independence() -> None:
    follower = replace(make_follower(CopyMode.RATIO), copy_ratio=Decimal("0.5"), fixed_leverage=Decimal("2"))
    long_target = TargetCalculator().calculate(
        follower, make_position("3000"), Decimal("10000"), Decimal("2000"), "BTC-USDT-PERP"
    )
    high_leverage_leader = replace(make_position("3000"), leverage=Decimal("20"))
    high_target = TargetCalculator().calculate(
        follower, high_leverage_leader, Decimal("10000"), Decimal("2000"), "BTC-USDT-PERP"
    )
    assert long_target.target_notional == high_target.target_notional == Decimal("300")


def test_ratio_short_preserves_direction() -> None:
    target = TargetCalculator().calculate(
        make_follower(CopyMode.RATIO),
        make_position("1000", PositionSide.SHORT),
        Decimal("10000"),
        Decimal("2000"),
        "BTC-USDT-PERP",
    )
    assert target.side == PositionSide.SHORT
    assert target.target_notional == Decimal("200")
