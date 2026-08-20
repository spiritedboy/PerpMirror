from __future__ import annotations

from decimal import Decimal

from perpmirror.config import RiskConfig
from perpmirror.enums import PositionSide, ReconcileAction
from perpmirror.models import ZERO, FollowerTarget, PositionSnapshot, RiskDecision


class RiskManager:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def check(
        self,
        target: FollowerTarget,
        actual: PositionSnapshot | None,
        all_positions: dict[str, PositionSnapshot],
        action: ReconcileAction,
        order_notional: Decimal,
    ) -> RiskDecision:
        increasing = action in {ReconcileAction.OPEN, ReconcileAction.ADD}
        if action in {ReconcileAction.CLOSE, ReconcileAction.REDUCE}:
            if self.config.kill_switch and not self.config.kill_switch_close_positions:
                return RiskDecision(False, "KILL_SWITCH", "kill switch does not allow automatic reductions")
            return RiskDecision(True)
        if not increasing:
            return RiskDecision(True)
        if self.config.kill_switch:
            return RiskDecision(False, "KILL_SWITCH", "new risk is disabled")
        if target.side == PositionSide.SHORT and not self.config.allow_short:
            return RiskDecision(False, "ALLOW_SHORT", "short exposure is disabled")
        if self.config.symbol_allowlist and target.symbol not in self.config.symbol_allowlist:
            return RiskDecision(False, "SYMBOL_ALLOWLIST", "symbol is not allowlisted")
        if target.symbol in self.config.symbol_blocklist:
            return RiskDecision(False, "SYMBOL_BLOCKLIST", "symbol is blocked")
        if target.target_leverage > self.config.max_leverage:
            return RiskDecision(False, "MAX_LEVERAGE", "target leverage exceeds risk limit")
        if target.target_notional > self.config.max_single_symbol_notional_usdt:
            return RiskDecision(False, "MAX_SINGLE_SYMBOL_NOTIONAL_USDT", "target exceeds symbol limit")
        if order_notional > self.config.max_order_notional_usdt:
            return RiskDecision(False, "MAX_ORDER_NOTIONAL_USDT", "planned order exceeds per-order limit")
        current_total = sum((position.abs_notional for position in all_positions.values()), ZERO)
        current_symbol = actual.abs_notional if actual else ZERO
        projected_total = current_total - current_symbol + target.target_notional
        if projected_total > self.config.max_total_notional_usdt:
            return RiskDecision(False, "MAX_TOTAL_NOTIONAL_USDT", "projected portfolio exceeds total limit")
        if actual is None and len(all_positions) >= self.config.max_open_symbols:
            return RiskDecision(False, "MAX_OPEN_SYMBOLS", "open symbol count limit reached")
        if order_notional < self.config.min_order_notional_usdt:
            return RiskDecision(False, "MIN_ORDER_NOTIONAL_USDT", "planned increase is below minimum")
        return RiskDecision(True)
