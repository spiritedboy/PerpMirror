from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import websockets

from perpmirror.enums import (
    Exchange,
    MarginMode,
    OrderSide,
    OrderStatus,
    PositionMode,
    PositionSide,
)
from perpmirror.exceptions import (
    AuthenticationError,
    NonRetryableExchangeError,
    RetryableExchangeError,
    UnknownOrderState,
    UnsafeOperation,
)
from perpmirror.exchanges.base import EventCallback, ExchangeClient, StateCallback
from perpmirror.exchanges.http import ReliableHttpClient
from perpmirror.models import ZERO, InstrumentInfo, OrderRequest, OrderResult, PositionSnapshot, decimal

logger = logging.getLogger(__name__)


class BinanceFuturesClient(ExchangeClient):
    exchange = Exchange.BINANCE

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        base_url: str = "https://fapi.binance.com",
        ws_base_url: str = "wss://fstream.binance.com/ws",
    ) -> None:
        self.api_key = api_key
        self._secret_key = secret_key.encode()
        self.http = ReliableHttpClient(base_url)
        self.ws_base_url = ws_base_url.rstrip("/")
        self._clock_offset_ms = 0
        self._instruments: dict[str, InstrumentInfo] = {}
        self._native_to_normalized: dict[str, str] = {}
        self._position_mode: PositionMode | None = None
        self._closed = asyncio.Event()

    async def _public(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self.http.request("GET", path, params=params)
        return response.json()

    async def _signed(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        order_submission: bool = False,
        timestamp_retry: bool = True,
    ) -> Any:
        values = dict(params or {})
        values.setdefault("recvWindow", 5000)
        values["timestamp"] = int(time.time() * 1000) + self._clock_offset_ms
        query = urlencode(values)
        signature = hmac.new(self._secret_key, query.encode(), hashlib.sha256).hexdigest()
        payload = f"{query}&signature={signature}"
        headers = {"X-MBX-APIKEY": self.api_key}
        try:
            if method == "GET":
                response = await self.http.request(
                    method,
                    path,
                    params={**values, "signature": signature},
                    headers=headers,
                    retry=not order_submission,
                )
            else:
                response = await self.http.request(
                    method,
                    path,
                    content=payload,
                    headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                    retry=not order_submission,
                )
        except RetryableExchangeError as exc:
            if order_submission:
                client_id = str(values.get("newClientOrderId", "unknown"))
                raise UnknownOrderState(
                    client_id, "Binance order submission response was not received"
                ) from exc
            raise
        data = response.json()
        if isinstance(data, dict) and int(data.get("code", 0)) < 0:
            code = int(data["code"])
            if code == -1021 and timestamp_retry:
                await self.sync_time()
                return await self._signed(
                    method, path, params, order_submission=order_submission, timestamp_retry=False
                )
            message = str(data.get("msg", "exchange error"))
            if code in {-2014, -2015}:
                raise AuthenticationError(f"Binance authentication failed ({code})")
            raise NonRetryableExchangeError(f"Binance error {code}: {message}")
        return data

    async def get_server_time(self) -> int:
        data = await self._public("/fapi/v1/time")
        return int(data["serverTime"])

    async def sync_time(self) -> int:
        before = int(time.time() * 1000)
        server = await self.get_server_time()
        after = int(time.time() * 1000)
        self._clock_offset_ms = server - ((before + after) // 2)
        return self._clock_offset_ms

    async def get_equity(self) -> Decimal:
        data = await self._signed("GET", "/fapi/v3/account")
        return decimal(data.get("totalMarginBalance", data.get("totalWalletBalance")))

    async def get_instruments(self) -> dict[str, InstrumentInfo]:
        if self._instruments:
            return dict(self._instruments)
        data = await self._public("/fapi/v1/exchangeInfo")
        for row in data.get("symbols", []):
            if (
                row.get("contractType") != "PERPETUAL"
                or row.get("quoteAsset") != "USDT"
                or row.get("marginAsset") != "USDT"
            ):
                continue
            filters = {item["filterType"]: item for item in row.get("filters", [])}
            market_lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE") or {}
            notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
            base = str(row["baseAsset"])
            normalized = f"{base}-USDT-PERP"
            item = InstrumentInfo(
                exchange=self.exchange,
                symbol=str(row["symbol"]),
                normalized_symbol=normalized,
                base_currency=base,
                quote_currency="USDT",
                settle_currency="USDT",
                quantity_step=decimal(market_lot.get("stepSize")),
                min_quantity=decimal(market_lot.get("minQty")),
                max_quantity=decimal(market_lot.get("maxQty")) if market_lot.get("maxQty") else None,
                min_notional=decimal(notional_filter.get("notional", notional_filter.get("minNotional"))),
                tick_size=decimal(filters.get("PRICE_FILTER", {}).get("tickSize")),
                active=row.get("status") == "TRADING",
            )
            if item.quantity_step > ZERO:
                self._instruments[normalized] = item
                self._native_to_normalized[item.symbol] = normalized
        return dict(self._instruments)

    async def get_mark_price(self, normalized_symbol: str) -> Decimal:
        instrument = await self.get_instrument(normalized_symbol)
        if instrument is None:
            raise NonRetryableExchangeError(f"Binance instrument not found: {normalized_symbol}")
        data = await self._public("/fapi/v1/premiumIndex", {"symbol": instrument.symbol})
        return decimal(data["markPrice"])

    async def get_positions(self) -> dict[str, PositionSnapshot]:
        await self.get_instruments()
        rows = await self._signed("GET", "/fapi/v3/positionRisk")
        positions: dict[str, PositionSnapshot] = {}
        for row in rows:
            quantity_signed = decimal(row.get("positionAmt"))
            if quantity_signed == ZERO:
                continue
            native = str(row["symbol"])
            normalized = self._native_to_normalized.get(native)
            if normalized is None:
                continue
            side = PositionSide.LONG if quantity_signed > ZERO else PositionSide.SHORT
            if normalized in positions:
                raise UnsafeOperation(
                    f"both hedge legs are open for {normalized}; PerpMirror refuses ambiguous reconciliation"
                )
            mark = decimal(row.get("markPrice"))
            notional = abs(decimal(row.get("notional")))
            if notional == ZERO:
                notional = abs(quantity_signed) * mark
            positions[normalized] = PositionSnapshot(
                exchange=self.exchange,
                symbol=native,
                normalized_symbol=normalized,
                side=side,
                quantity=abs(quantity_signed),
                notional_usdt=notional,
                entry_price=decimal(row.get("entryPrice")) or None,
                mark_price=mark,
                leverage=decimal(row.get("leverage"), Decimal("1")),
                margin_mode=MarginMode.ISOLATED if bool(row.get("isolated")) else MarginMode.CROSS,
                unrealized_pnl=decimal(row.get("unRealizedProfit")),
                liquidation_price=decimal(row.get("liquidationPrice")) or None,
            )
        return positions

    async def get_position_mode(self) -> PositionMode:
        data = await self._signed("GET", "/fapi/v1/positionSide/dual")
        self._position_mode = (
            PositionMode.HEDGE if bool(data.get("dualSidePosition")) else PositionMode.ONE_WAY
        )
        return self._position_mode

    async def get_margin_mode(self, normalized_symbol: str) -> MarginMode | None:
        position = await self.get_position(normalized_symbol)
        return position.margin_mode if position else None

    async def set_leverage(
        self, normalized_symbol: str, leverage: Decimal, margin_mode: MarginMode, side: PositionSide
    ) -> None:
        instrument = await self.get_instrument(normalized_symbol)
        if instrument is None:
            raise NonRetryableExchangeError(f"Binance instrument not found: {normalized_symbol}")
        await self._signed(
            "POST", "/fapi/v1/leverage", {"symbol": instrument.symbol, "leverage": int(leverage)}
        )

    async def set_margin_mode(self, normalized_symbol: str, margin_mode: MarginMode) -> None:
        instrument = await self.get_instrument(normalized_symbol)
        if instrument is None:
            raise NonRetryableExchangeError(f"Binance instrument not found: {normalized_symbol}")
        current = await self.get_margin_mode(normalized_symbol)
        if current == margin_mode:
            return
        try:
            await self._signed(
                "POST",
                "/fapi/v1/marginType",
                {"symbol": instrument.symbol, "marginType": margin_mode.value.upper()},
            )
        except NonRetryableExchangeError as exc:
            if "No need to change margin type" not in str(exc):
                raise

    async def place_market_order(self, request: OrderRequest) -> OrderResult:
        mode = self._position_mode or await self.get_position_mode()
        params: dict[str, Any] = {
            "symbol": request.symbol,
            "side": request.side.value.upper(),
            "type": "MARKET",
            "quantity": format(request.quantity, "f"),
            "newClientOrderId": request.client_order_id,
            "newOrderRespType": "RESULT",
        }
        if mode == PositionMode.HEDGE:
            params["positionSide"] = request.position_side.value.upper()
            # Binance forbids reduceOnly in Hedge Mode; side + positionSide closes only that leg.
        else:
            params["positionSide"] = "BOTH"
            params["reduceOnly"] = "true" if request.reduce_only else "false"
        data = await self._signed("POST", "/fapi/v1/order", params, order_submission=True)
        return self._parse_order(data, request.client_order_id)

    async def close_position(self, position: PositionSnapshot, client_order_id: str) -> OrderResult:
        return await self.place_market_order(
            OrderRequest(
                symbol=position.symbol,
                normalized_symbol=position.normalized_symbol,
                side=OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY,
                position_side=position.side,
                quantity=position.quantity,
                reduce_only=True,
                client_order_id=client_order_id,
                margin_mode=position.margin_mode,
            )
        )

    async def verify_order(self, symbol: str, client_order_id: str) -> OrderResult | None:
        try:
            data = await self._signed(
                "GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_order_id}
            )
        except NonRetryableExchangeError as exc:
            if "-2013" in str(exc):
                return None
            raise
        return self._parse_order(data, client_order_id)

    def _parse_order(self, data: dict[str, Any], client_id: str) -> OrderResult:
        raw = str(data.get("status", "NEW"))
        statuses = {
            "NEW": OrderStatus.NEW,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.REJECTED,
        }
        return OrderResult(
            exchange=self.exchange,
            symbol=str(data.get("symbol", "")),
            client_order_id=client_id,
            order_id=str(data.get("orderId")) if data.get("orderId") is not None else None,
            status=statuses.get(raw, OrderStatus.UNKNOWN),
            filled_quantity=decimal(data.get("executedQty")),
            average_price=decimal(data.get("avgPrice")) or None,
            raw_status=raw,
        )

    async def _listen_key(self) -> str:
        response = await self.http.request(
            "POST", "/fapi/v1/listenKey", headers={"X-MBX-APIKEY": self.api_key}
        )
        return str(response.json()["listenKey"])

    async def connect_private_ws(
        self, on_event: EventCallback, on_state: StateCallback | None = None
    ) -> None:
        backoff = 1.0
        connected_once = False
        while not self._closed.is_set():
            try:
                listen_key = await self._listen_key()
                async with websockets.connect(
                    f"{self.ws_base_url}/{listen_key}", ping_interval=20, ping_timeout=20, close_timeout=5
                ) as ws:
                    keepalive = asyncio.create_task(self._keepalive_listen_key(), name="binance-listen-key")
                    try:
                        state = "reconnected" if connected_once else "connected"
                        connected_once = True
                        if on_state:
                            await on_state(state)
                        backoff = 1.0
                        async for raw in ws:
                            message = json.loads(raw)
                            if message.get("e") == "listenKeyExpired":
                                break
                            if message.get("e") in {
                                "ACCOUNT_UPDATE",
                                "ORDER_TRADE_UPDATE",
                                "MARGIN_CALL",
                            }:
                                await on_event()
                    finally:
                        keepalive.cancel()
                        await asyncio.gather(keepalive, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Binance private WS disconnected error=%s retry_in=%s", exc, backoff)
                if on_state:
                    await on_state("disconnected")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _keepalive_listen_key(self) -> None:
        while not self._closed.is_set():
            await asyncio.sleep(45 * 60)
            await self.http.request("PUT", "/fapi/v1/listenKey", headers={"X-MBX-APIKEY": self.api_key})

    async def close(self) -> None:
        self._closed.set()
        await self.http.close()
