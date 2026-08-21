from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from perpmirror.enums import PositionSide, ReconcileAction
from perpmirror.exceptions import PerpMirrorError
from perpmirror.exchanges.base import ExchangeClient
from perpmirror.execution.executor import ExecutionEngine
from perpmirror.models import (
    ZERO,
    FollowerTarget,
    InstrumentInfo,
    OrderResult,
    PositionSnapshot,
    ReconcileResult,
)
from perpmirror.risk.manager import RiskManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FailureCooldown:
    deadline: float
    target_side: PositionSide
    target_notional: Decimal
    request_action: ReconcileAction
    result_action: ReconcileAction
    message: str


class Reconciler:
    def __init__(
        self,
        *,
        risk_manager: RiskManager,
        executor: ExecutionEngine,
        drift_percent: Decimal,
        drift_min_usdt: Decimal,
        max_retries: int,
        retry_base_delay: Decimal,
        max_concurrent_orders: int,
        failure_cooldown: Decimal = Decimal("300"),
    ) -> None:
        self.risk_manager = risk_manager
        self.executor = executor
        self.drift_percent = drift_percent
        self.drift_min_usdt = drift_min_usdt
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.failure_cooldown = float(failure_cooldown)
        self._locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._semaphore = asyncio.Semaphore(max_concurrent_orders)
        self._failure_cooldowns: dict[tuple[str, str], FailureCooldown] = {}

    def classify(self, target: FollowerTarget, actual: PositionSnapshot | None) -> ReconcileAction:
        if target.side == PositionSide.FLAT or target.target_notional <= ZERO:
            return ReconcileAction.NOOP if actual is None else ReconcileAction.CLOSE
        if actual is None or actual.side == PositionSide.FLAT:
            return ReconcileAction.OPEN
        if actual.side != target.side:
            return ReconcileAction.FLIP
        delta = target.target_notional - actual.abs_notional
        tolerance = max(self.drift_min_usdt, target.target_notional * self.drift_percent / Decimal("100"))
        if abs(delta) <= tolerance:
            return ReconcileAction.NOOP
        return ReconcileAction.ADD if delta > ZERO else ReconcileAction.REDUCE

    async def reconcile(self, client: ExchangeClient, target: FollowerTarget) -> ReconcileResult:
        key = (target.follower_id, target.symbol)
        async with self._locks[key], self._semaphore:
            initial = await client.get_position(target.symbol)
            previous = initial.abs_notional if initial else ZERO
            orders: list[OrderResult] = []
            for attempt in range(self.max_retries + 1):
                actual = await client.get_position(target.symbol)
                action = self.classify(target, actual)
                cooldown = self._failure_cooldowns.get(key)
                if action in {ReconcileAction.OPEN, ReconcileAction.ADD} and cooldown is not None:
                    if self._same_failure_target(cooldown, target, action) and (
                        cooldown.deadline > time.monotonic()
                    ):
                        final = actual.abs_notional if actual else ZERO
                        return ReconcileResult(
                            target.follower_id,
                            target.symbol,
                            cooldown.result_action,
                            target.target_notional,
                            previous,
                            final,
                            order_results=tuple(orders),
                            success=False,
                            message=cooldown.message,
                            notification_suppressed=True,
                        )
                    self._failure_cooldowns.pop(key, None)
                elif action not in {ReconcileAction.OPEN, ReconcileAction.ADD}:
                    self._failure_cooldowns.pop(key, None)
                if action == ReconcileAction.NOOP:
                    final = actual.abs_notional if actual else ZERO
                    return ReconcileResult(
                        target.follower_id,
                        target.symbol,
                        ReconcileAction.NOOP if not orders else self.classify_executed(initial, target),
                        target.target_notional,
                        previous,
                        final,
                        tuple(orders),
                    )
                try:
                    outcome = await self._execute_action(client, target, actual, action)
                except PerpMirrorError as exc:
                    logger.error(
                        "ORDER_FAILED follower=%s symbol=%s action=%s error=%s",
                        target.follower_id,
                        target.symbol,
                        action.value,
                        exc,
                    )
                    final_position = await client.get_position(target.symbol)
                    if action in {ReconcileAction.OPEN, ReconcileAction.ADD}:
                        self._start_cooldown(
                            key,
                            target,
                            action,
                            ReconcileAction.ORDER_FAILED,
                            str(exc),
                        )
                    return ReconcileResult(
                        target.follower_id,
                        target.symbol,
                        ReconcileAction.ORDER_FAILED,
                        target.target_notional,
                        previous,
                        final_position.abs_notional if final_position else ZERO,
                        tuple(orders),
                        False,
                        str(exc),
                    )
                if isinstance(outcome, ReconcileResult):
                    if (
                        outcome.action == ReconcileAction.RISK_BLOCKED
                        and action in {ReconcileAction.OPEN, ReconcileAction.ADD}
                    ):
                        self._start_cooldown(
                            key,
                            target,
                            action,
                            ReconcileAction.RISK_BLOCKED,
                            outcome.message or "risk blocked",
                        )
                    return outcome
                orders.extend(outcome)
                self._failure_cooldowns.pop(key, None)
                if self.executor.dry_run:
                    return ReconcileResult(
                        target.follower_id,
                        target.symbol,
                        action,
                        target.target_notional,
                        previous,
                        previous,
                        tuple(orders),
                        True,
                        "dry run: no exchange state was changed",
                    )
                if attempt < self.max_retries:
                    await asyncio.sleep(float(self.retry_base_delay * (Decimal("2") ** attempt)))
            final_position = await client.get_position(target.symbol)
            final = final_position.abs_notional if final_position else ZERO
            return ReconcileResult(
                target.follower_id,
                target.symbol,
                self.classify_executed(initial, target),
                target.target_notional,
                previous,
                final,
                tuple(orders),
                self.classify(target, final_position) == ReconcileAction.NOOP,
                "maximum reconciliation retries reached",
            )

    def _start_cooldown(
        self,
        key: tuple[str, str],
        target: FollowerTarget,
        request_action: ReconcileAction,
        result_action: ReconcileAction,
        message: str,
    ) -> None:
        if self.failure_cooldown <= 0:
            return
        self._failure_cooldowns[key] = FailureCooldown(
            deadline=time.monotonic() + self.failure_cooldown,
            target_side=target.side,
            target_notional=target.target_notional,
            request_action=request_action,
            result_action=result_action,
            message=message,
        )

    def _same_failure_target(
        self,
        cooldown: FailureCooldown,
        target: FollowerTarget,
        action: ReconcileAction,
    ) -> bool:
        tolerance = max(
            self.drift_min_usdt,
            cooldown.target_notional * self.drift_percent / Decimal("100"),
        )
        return (
            cooldown.target_side == target.side
            and cooldown.request_action == action
            and abs(cooldown.target_notional - target.target_notional) <= tolerance
        )

    async def _execute_action(
        self,
        client: ExchangeClient,
        target: FollowerTarget,
        actual: PositionSnapshot | None,
        action: ReconcileAction,
    ) -> list[OrderResult] | ReconcileResult:
        positions = await client.get_positions()
        if action == ReconcileAction.FLIP:
            assert actual is not None
            reduction_decision = self.risk_manager.check(
                target, actual, positions, ReconcileAction.CLOSE, actual.abs_notional
            )
            if not reduction_decision.allowed:
                return self._risk_blocked(target, actual, reduction_decision.reason)
            close_result = await self.executor.close(client, target, actual)
            if self.executor.dry_run:
                return [close_result]
            verified = await client.get_position(target.symbol)
            if verified is not None:
                return ReconcileResult(
                    target.follower_id,
                    target.symbol,
                    ReconcileAction.ORDER_FAILED,
                    target.target_notional,
                    actual.abs_notional,
                    verified.abs_notional,
                    (close_result,),
                    False,
                    "flip stopped because the old direction was not fully closed",
                )
            open_decision = self.risk_manager.check(
                target, None, await client.get_positions(), ReconcileAction.OPEN, target.target_notional
            )
            if not open_decision.allowed:
                return ReconcileResult(
                    target.follower_id,
                    target.symbol,
                    ReconcileAction.RISK_BLOCKED,
                    target.target_notional,
                    actual.abs_notional,
                    ZERO,
                    (close_result,),
                    False,
                    f"old position closed; new side blocked: {open_decision.rule}",
                )
            instrument = await self._instrument(client, target.symbol)
            open_result = await self.executor.execute_delta(
                client, instrument, target, None, target.target_notional, reduce_only=False
            )
            return [close_result, open_result]
        if action == ReconcileAction.CLOSE:
            assert actual is not None
            decision = self.risk_manager.check(target, actual, positions, action, actual.abs_notional)
            if not decision.allowed:
                return self._risk_blocked(target, actual, decision.reason)
            return [await self.executor.close(client, target, actual)]
        assert action in {ReconcileAction.OPEN, ReconcileAction.ADD, ReconcileAction.REDUCE}
        order_notional = (
            target.target_notional if actual is None else abs(target.target_notional - actual.abs_notional)
        )
        decision = self.risk_manager.check(target, actual, positions, action, order_notional)
        if not decision.allowed:
            return self._risk_blocked(target, actual, f"{decision.rule}: {decision.reason}")
        instrument = await self._instrument(client, target.symbol)
        result = await self.executor.execute_delta(
            client,
            instrument,
            target,
            actual,
            order_notional,
            reduce_only=action == ReconcileAction.REDUCE,
        )
        return [result]

    @staticmethod
    async def _instrument(client: ExchangeClient, symbol: str) -> InstrumentInfo:
        instrument = await client.get_instrument(symbol)
        if instrument is None:
            from perpmirror.exceptions import InstrumentNotFound

            raise InstrumentNotFound(f"instrument metadata unavailable: {symbol}")
        return instrument

    @staticmethod
    def _risk_blocked(
        target: FollowerTarget, actual: PositionSnapshot | None, reason: str | None
    ) -> ReconcileResult:
        current = actual.abs_notional if actual else ZERO
        return ReconcileResult(
            target.follower_id,
            target.symbol,
            ReconcileAction.RISK_BLOCKED,
            target.target_notional,
            current,
            current,
            success=False,
            message=reason,
        )

    @staticmethod
    def classify_executed(initial: PositionSnapshot | None, target: FollowerTarget) -> ReconcileAction:
        if target.side == PositionSide.FLAT:
            return ReconcileAction.CLOSE
        if initial is None:
            return ReconcileAction.OPEN
        if initial.side != target.side:
            return ReconcileAction.FLIP
        return (
            ReconcileAction.ADD if target.target_notional > initial.abs_notional else ReconcileAction.REDUCE
        )
