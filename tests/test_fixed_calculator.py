from decimal import Decimal

from conftest import make_follower, make_position

from perpmirror.copy.calculator import TargetCalculator
from perpmirror.enums import PositionSide


def test_fixed_target_uses_margin_times_capped_leverage() -> None:
    leader = make_position("5000")
    target = TargetCalculator().calculate(
        make_follower(), leader, Decimal("10000"), Decimal("2000"), "BTC-USDT-PERP"
    )
    assert target.side == PositionSide.LONG
    assert target.target_notional == Decimal("200")
    assert target.target_margin == Decimal("20")


def test_fixed_target_does_not_change_when_leader_adds() -> None:
    calculator = TargetCalculator()
    follower = make_follower()
    first = calculator.calculate(
        follower, make_position("1000"), Decimal("10000"), Decimal("2000"), "BTC-USDT-PERP"
    )
    second = calculator.calculate(
        follower, make_position("5000"), Decimal("10000"), Decimal("2000"), "BTC-USDT-PERP"
    )
    assert first.target_notional == second.target_notional == Decimal("200")


def test_fixed_leader_flat_means_follower_flat() -> None:
    target = TargetCalculator().calculate(
        make_follower(), None, Decimal("10000"), Decimal("2000"), "BTC-USDT-PERP"
    )
    assert target.side == PositionSide.FLAT
    assert target.target_notional == 0
