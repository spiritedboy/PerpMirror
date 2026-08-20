from __future__ import annotations

from decimal import Decimal

from perpmirror.enums import CopyMode, MarginMode, PositionSide
from perpmirror.exceptions import UnsafeOperation
from perpmirror.models import ZERO, FollowerConfig, FollowerTarget, PositionSnapshot


class TargetCalculator:
    def calculate(
        self,
        follower: FollowerConfig,
        leader_position: PositionSnapshot | None,
        leader_equity: Decimal,
        follower_equity: Decimal,
        normalized_symbol: str,
    ) -> FollowerTarget:
        side = leader_position.side if leader_position else PositionSide.FLAT
        leader_notional = leader_position.abs_notional if leader_position else ZERO
        leader_leverage = leader_position.leverage if leader_position else follower.fixed_leverage
        leverage = min(
            leader_leverage if follower.copy_leverage else follower.fixed_leverage,
            follower.max_leverage,
        )
        if leverage <= ZERO:
            raise UnsafeOperation("calculated follower leverage is not positive")
        margin_mode = (
            leader_position.margin_mode
            if leader_position is not None and follower.copy_margin_mode
            else follower.margin_mode
        )
        if follower.copy_mode == CopyMode.FIXED:
            return self.calculate_fixed_target(
                follower,
                normalized_symbol,
                side,
                leader_notional,
                leader_equity,
                follower_equity,
                leverage,
                margin_mode,
            )
        return self.calculate_ratio_target(
            follower,
            normalized_symbol,
            side,
            leader_notional,
            leader_equity,
            follower_equity,
            leverage,
            margin_mode,
        )

    @staticmethod
    def calculate_fixed_target(
        follower: FollowerConfig,
        normalized_symbol: str,
        side: PositionSide,
        leader_notional: Decimal,
        leader_equity: Decimal,
        follower_equity: Decimal,
        leverage: Decimal,
        margin_mode: MarginMode,
    ) -> FollowerTarget:
        fixed_margin = follower.fixed_margin_usdt or ZERO
        target_notional = fixed_margin * leverage if side != PositionSide.FLAT else ZERO
        return FollowerTarget(
            follower_id=follower.id,
            exchange=follower.exchange,
            symbol=normalized_symbol,
            side=side,
            copy_mode=CopyMode.FIXED,
            target_notional=target_notional,
            target_margin=fixed_margin if side != PositionSide.FLAT else ZERO,
            target_leverage=leverage,
            margin_mode=margin_mode,
            leader_notional=leader_notional,
            leader_equity=leader_equity,
            leader_exposure_ratio=(leader_notional / leader_equity if leader_equity > ZERO else None),
            follower_equity=follower_equity,
            fixed_margin=fixed_margin,
        )

    @staticmethod
    def calculate_ratio_target(
        follower: FollowerConfig,
        normalized_symbol: str,
        side: PositionSide,
        leader_notional: Decimal,
        leader_equity: Decimal,
        follower_equity: Decimal,
        leverage: Decimal,
        margin_mode: MarginMode,
    ) -> FollowerTarget:
        if side != PositionSide.FLAT and leader_equity <= ZERO:
            raise UnsafeOperation("leader equity must be positive for ratio sizing")
        if follower_equity < ZERO:
            raise UnsafeOperation("follower equity cannot be negative")
        exposure = leader_notional / leader_equity if leader_equity > ZERO else ZERO
        ratio = follower.copy_ratio or ZERO
        target_notional = follower_equity * exposure * ratio if side != PositionSide.FLAT else ZERO
        return FollowerTarget(
            follower_id=follower.id,
            exchange=follower.exchange,
            symbol=normalized_symbol,
            side=side,
            copy_mode=CopyMode.RATIO,
            target_notional=target_notional,
            target_margin=(target_notional / leverage if leverage > ZERO else None),
            target_leverage=leverage,
            margin_mode=margin_mode,
            leader_notional=leader_notional,
            leader_equity=leader_equity,
            leader_exposure_ratio=exposure,
            follower_equity=follower_equity,
            copy_ratio=ratio,
        )
