from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from decimal import Decimal

from perpmirror.config import Settings
from perpmirror.copy.calculator import TargetCalculator
from perpmirror.copy.reconciler import Reconciler
from perpmirror.enums import Exchange, ReconcileAction
from perpmirror.exceptions import ConfigurationError, PerpMirrorError
from perpmirror.exchanges import BinanceFuturesClient, ExchangeClient, OkxSwapClient
from perpmirror.execution.executor import ExecutionEngine
from perpmirror.models import ZERO, FollowerTarget, ReconcileResult, TradeNotification
from perpmirror.monitoring.coalescer import ReconcileCoalescer
from perpmirror.monitoring.ownership import PositionOwnership
from perpmirror.notifications.base import NullNotifier
from perpmirror.notifications.cards import FeishuCardBuilder
from perpmirror.notifications.feishu import FeishuNotifier
from perpmirror.notifications.worker import NotificationWorker
from perpmirror.risk.manager import RiskManager
from perpmirror.symbols.mapper import SymbolMapper

logger = logging.getLogger(__name__)


class PerpMirrorApp:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.leader = self._client(
            settings.leader.exchange,
            settings.secret(settings.leader.api_key_env),
            settings.secret(settings.leader.secret_key_env),
            settings.secret(settings.leader.passphrase_env)
            if settings.leader.exchange == Exchange.OKX
            else "",
        )
        self.followers: dict[str, ExchangeClient] = {
            follower.id: self._client(
                follower.exchange,
                settings.secret(follower.api_key_env),
                settings.secret(follower.secret_key_env),
                settings.secret(follower.passphrase_env) if follower.exchange == Exchange.OKX else "",
            )
            for follower in settings.followers
        }
        self.mapper = SymbolMapper()
        self.calculator = TargetCalculator()
        self.reconciler = Reconciler(
            risk_manager=RiskManager(settings.risk),
            executor=ExecutionEngine(dry_run=settings.app.dry_run),
            drift_percent=settings.app.position_drift_threshold_percent,
            drift_min_usdt=settings.app.position_drift_min_usdt,
            max_retries=settings.app.max_reconcile_retries,
            retry_base_delay=settings.app.retry_base_delay_seconds,
            max_concurrent_orders=settings.app.max_concurrent_orders,
            failure_cooldown=settings.app.order_failure_cooldown_seconds,
        )
        notifier = (
            FeishuNotifier(
                settings.secret(settings.feishu.webhook_env),
                settings.secret(settings.feishu.secret_env, required=False),
            )
            if settings.feishu.enabled
            else NullNotifier()
        )
        self.notifications = NotificationWorker(notifier, max_retries=settings.feishu.max_retries)
        self.cards = FeishuCardBuilder()
        self.coalescer = ReconcileCoalescer(
            self._coalesced_reconcile, float(settings.app.event_debounce_seconds)
        )
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._full_reconcile_lock = asyncio.Lock()
        self.ownership = (
            PositionOwnership(
                settings.app.ownership_state_file,
                identity={
                    "leader": {
                        "id": settings.leader.id,
                        "exchange": settings.leader.exchange.value,
                    },
                    "followers": {
                        follower.id: follower.exchange.value for follower in settings.followers
                    },
                },
                persist_changes=not settings.app.dry_run,
            )
            if settings.app.preserve_existing_positions
            else None
        )

    @staticmethod
    def _client(exchange: Exchange, api_key: str, secret: str, passphrase: str) -> ExchangeClient:
        if exchange == Exchange.BINANCE:
            return BinanceFuturesClient(api_key, secret)
        if exchange == Exchange.OKX:
            if not passphrase:
                raise ConfigurationError("OKX account requires a passphrase")
            return OkxSwapClient(api_key, secret, passphrase)
        raise ConfigurationError(f"unsupported configured exchange: {exchange.value}")

    async def startup_checks(self) -> None:
        accounts = [
            ("leader", self.settings.leader.id, self.leader),
            *(("follower", follower.id, self.followers[follower.id]) for follower in self.settings.followers),
        ]
        for role, account_id, client in accounts:
            logger.info(
                "STARTUP_CHECK_BEGIN role=%s account_id=%s exchange=%s",
                role,
                account_id,
                client.exchange.value,
            )
            try:
                offset = await client.sync_time()
                instruments = await client.get_instruments()
                if not instruments:
                    raise ConfigurationError(f"{client.exchange.value}: no active USDT perpetual instruments")
                self.mapper.add_instruments(client.exchange, instruments)
                mode = await client.get_position_mode()
                equity = await client.get_equity()
                await client.get_positions()
                if equity < ZERO:
                    raise ConfigurationError(f"{client.exchange.value}: account equity is negative")
            except PerpMirrorError as exc:
                raise ConfigurationError(
                    f"startup check failed role={role} account_id={account_id} "
                    f"exchange={client.exchange.value}: {exc}"
                ) from exc
            logger.info(
                "STARTUP_CHECK_OK role=%s account_id=%s exchange=%s clock_offset_ms=%s "
                "instruments=%s position_mode=%s equity=%s",
                role,
                account_id,
                client.exchange.value,
                offset,
                len(instruments),
                mode.value,
                equity,
            )

    async def initialize_position_ownership(self) -> None:
        if self.ownership is None:
            return
        leader_positions = await self.leader.get_positions()
        follower_positions = {
            follower.id: set(await self.followers[follower.id].get_positions())
            for follower in self.settings.followers
        }
        self.ownership.initialize(set(leader_positions), follower_positions)

    async def full_reconcile(self) -> list[ReconcileResult]:
        if self._stop.is_set():
            return []
        async with self._full_reconcile_lock:
            leader_equity = await self.leader.get_equity()
            leader_positions = await self.leader.get_positions()
            if self.ownership is not None:
                self.ownership.observe_leader(set(leader_positions))
            results: list[ReconcileResult] = []
            for follower_config in self.settings.followers:
                client = self.followers[follower_config.id]
                follower_equity = await client.get_equity()
                follower_positions = await client.get_positions()
                if self.ownership is None:
                    symbols = set(leader_positions) | set(follower_positions)
                else:
                    self.ownership.protect_unmanaged(follower_config.id, set(follower_positions))
                    symbols = self.ownership.candidate_symbols(
                        follower_config.id, set(leader_positions)
                    )
                coroutines = []
                targets = []
                target_symbols = []
                for symbol in sorted(symbols):
                    leader_position = leader_positions.get(symbol)
                    if leader_position is not None and not self.mapper.supported(client.exchange, symbol):
                        logger.warning(
                            "SYMBOL_UNSUPPORTED follower=%s exchange=%s symbol=%s action=skip",
                            follower_config.id,
                            client.exchange.value,
                            symbol,
                        )
                        continue
                    if (
                        self.ownership is not None
                        and not self.ownership.is_managed(follower_config.id, symbol)
                    ):
                        if leader_position is None:
                            continue
                        self.ownership.claim(follower_config.id, symbol)
                    target = self.calculator.calculate(
                        follower_config,
                        leader_position,
                        leader_equity,
                        follower_equity,
                        symbol,
                    )
                    targets.append(target)
                    target_symbols.append(symbol)
                    coroutines.append(self.reconciler.reconcile(client, target))
                if not coroutines:
                    continue
                follower_results = await asyncio.gather(*coroutines)
                results.extend(follower_results)
                for symbol, target, result in zip(
                    target_symbols, targets, follower_results, strict=True
                ):
                    self._log_result(result)
                    if (
                        self.ownership is not None
                        and leader_positions.get(symbol) is None
                        and result.success
                        and result.final_notional == ZERO
                    ):
                        self.ownership.release(follower_config.id, symbol)
                    if (
                        result.action != ReconcileAction.NOOP
                        and not result.notification_suppressed
                    ):
                        self.notifications.publish(
                            self.cards.trade(
                                self._notification(target, result), dry_run=self.settings.app.dry_run
                            )
                        )
            return results

    async def _coalesced_reconcile(self) -> None:
        await self.full_reconcile()

    @staticmethod
    def _log_result(result: ReconcileResult) -> None:
        logger.info(
            "RECONCILE follower=%s symbol=%s action=%s target=%s before=%s after=%s success=%s",
            result.follower_id,
            result.symbol,
            result.action.value,
            result.target_notional,
            result.previous_notional,
            result.final_notional,
            result.success,
        )

    @staticmethod
    def _notification(target: FollowerTarget, result: ReconcileResult) -> TradeNotification:
        if result.action == ReconcileAction.CLOSE:
            order_notional = result.previous_notional
        elif result.action == ReconcileAction.FLIP:
            order_notional = result.previous_notional + result.target_notional
        else:
            order_notional = abs(result.target_notional - result.previous_notional)
        order_id = next((item.order_id for item in reversed(result.order_results) if item.order_id), None)
        return TradeNotification(
            follower_id=target.follower_id,
            exchange=target.exchange,
            symbol=target.symbol,
            action=result.action,
            side=target.side,
            copy_mode=target.copy_mode,
            leader_equity=target.leader_equity,
            leader_notional=target.leader_notional,
            leader_exposure_ratio=target.leader_exposure_ratio,
            follower_equity=target.follower_equity,
            copy_ratio=target.copy_ratio,
            fixed_margin=target.fixed_margin,
            leverage=target.target_leverage,
            previous_notional=result.previous_notional,
            target_notional=result.target_notional,
            order_notional=order_notional,
            final_notional=result.final_notional,
            order_id=order_id,
            success=result.success,
            error_message=result.message,
        )

    async def send_startup_card(self) -> None:
        leader_equity = await self.leader.get_equity()
        leader_positions = await self.leader.get_positions()
        followers: list[tuple[str, str, str, Decimal, int]] = []
        for config in self.settings.followers:
            client = self.followers[config.id]
            followers.append(
                (
                    config.id,
                    config.exchange.value,
                    config.copy_mode.value,
                    await client.get_equity(),
                    len(await client.get_positions()),
                )
            )
        self.notifications.publish(
            self.cards.startup(
                dry_run=self.settings.app.dry_run,
                leader_exchange=self.settings.leader.exchange.value,
                leader_equity=leader_equity,
                leader_positions=len(leader_positions),
                followers=followers,
                reconcile_interval=self.settings.app.full_reconcile_interval_seconds,
            )
        )

    async def _periodic_loop(self) -> None:
        interval = float(self.settings.app.full_reconcile_interval_seconds)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                await self.coalescer.trigger()

    async def _heartbeat_loop(self) -> None:
        interval = float(self.settings.app.heartbeat_interval_seconds)
        if interval <= 0:
            return
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                logger.info("HEARTBEAT mode=%s", "DRY_RUN" if self.settings.app.dry_run else "LIVE")

    async def _ws_state(self, state: str) -> None:
        logger.warning("LEADER_WS state=%s", state)
        if state == "reconnected":
            # Reconnection must immediately fetch authoritative REST snapshots.
            await self.coalescer.trigger()

    async def run(self, *, once: bool = False, check_only: bool = False) -> None:
        self.notifications.start()
        await self.startup_checks()
        if check_only:
            return
        await self.initialize_position_ownership()
        await self.send_startup_card()
        if self.settings.app.sync_on_start or once:
            await self.full_reconcile()
        if once:
            return
        self.coalescer.start()
        self._tasks = {
            asyncio.create_task(self._periodic_loop(), name="periodic-reconcile"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(
                self.leader.connect_private_ws(self.coalescer.trigger, self._ws_state),
                name="leader-private-ws",
            ),
        }
        await self._stop.wait()

    def request_stop(self) -> None:
        self._stop.set()

    async def close(self) -> None:
        self._stop.set()
        await self.coalescer.stop()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.gather(self.leader.close(), *(client.close() for client in self.followers.values()))
        with suppress(Exception):
            await self.notifications.stop()
