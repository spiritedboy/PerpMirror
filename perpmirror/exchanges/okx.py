from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta
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


class OkxSwapClient(ExchangeClient):
    exchange = Exchange.OKX

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str,
        *,
        base_url: str = "https://openapi.okx.com",
        ws_url: str = "wss://ws.okx.com:8443/ws/v5/private",
    ) -> None:
        self.api_key = api_key
        self._secret_key = secret_key.encode()
        self._passphrase = passphrase
        self.http = ReliableHttpClient(base_url)
        self.ws_url = ws_url
        self._clock_offset_ms = 0
        self._instruments: dict[str, InstrumentInfo] = {}
        self._native_to_normalized: dict[str, str] = {}
        self._position_mode: PositionMode | None = None
        self._closed = asyncio.Event()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        *,
        private: bool = False,
        order_submission: bool = False,
        timestamp_retry: bool = True,
    ) -> Any:
        query = urlencode(params or {})
        request_path = f"{path}?{query}" if query else path
        body_text = json.dumps(body, separators=(",", ":")) if body else ""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if private:
            timestamp = self._iso_timestamp()
            signature = self._sign(f"{timestamp}{method}{request_path}{body_text}")
            headers.update(
                {
                    "OK-ACCESS-KEY": self.api_key,
                    "OK-ACCESS-SIGN": signature,
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": self._passphrase,
                }
            )
        try:
            response = await self.http.request(
                method,
                request_path,
                content=body_text or None,
                headers=headers,
                retry=not order_submission,
            )
        except RetryableExchangeError as exc:
            if order_submission:
                client_id = str((body or {}).get("clOrdId", "unknown"))
                raise UnknownOrderState(client_id, "OKX order submission response was not received") from exc
            raise
        data = response.json()
        code = str(data.get("code", "0"))
        if code != "0":
            if code == "50102" and timestamp_retry:
                await self.sync_time()
                return await self._request(
                    method,
                    path,
                    params,
                    body,
                    private=private,
                    order_submission=order_submission,
                    timestamp_retry=False,
                )
            if code in {"50103", "50104", "50105", "50113"}:
                raise AuthenticationError(f"OKX authentication failed ({code})")
            raise NonRetryableExchangeError(f"OKX error {code}: {data.get('msg', '')}")
        return data.get("data", [])

    def _sign(self, prehash: str) -> str:
        digest = hmac.new(self._secret_key, prehash.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def _iso_timestamp(self) -> str:
        now = datetime.now(UTC) + timedelta(milliseconds=self._clock_offset_ms)
        return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    async def get_server_time(self) -> int:
        data = await self._request("GET", "/api/v5/public/time")
        return int(data[0]["ts"])

    async def sync_time(self) -> int:
        before = int(time.time() * 1000)
        server = await self.get_server_time()
        after = int(time.time() * 1000)
        self._clock_offset_ms = server - ((before + after) // 2)
        return self._clock_offset_ms

    async def get_equity(self) -> Decimal:
        data = await self._request("GET", "/api/v5/account/balance", private=True)
        if not data:
            return ZERO
        return decimal(data[0].get("totalEq", data[0].get("adjEq")))

    async def get_instruments(self) -> dict[str, InstrumentInfo]:
        if self._instruments:
            return dict(self._instruments)
        rows = await self._request("GET", "/api/v5/public/instruments", {"instType": "SWAP"})
        for row in rows:
            if row.get("instType") != "SWAP" or row.get("settleCcy") != "USDT":
                continue
            if row.get("ctType") != "linear":
                continue
            # For OKX derivatives baseCcy/quoteCcy are empty. ctValCcy is the
            # authoritative base unit of one linear contract (for example BTC).
            base = str(row.get("ctValCcy", "")).strip()
            if not base or base == "USDT":
                continue
            normalized = f"{base}-USDT-PERP"
            item = InstrumentInfo(
                exchange=self.exchange,
                symbol=str(row["instId"]),
                normalized_symbol=normalized,
                base_currency=base,
                quote_currency="USDT",
                settle_currency="USDT",
                quantity_step=decimal(row.get("lotSz")),
                min_quantity=decimal(row.get("minSz")),
                max_quantity=decimal(row.get("maxMktSz")) if row.get("maxMktSz") else None,
                tick_size=decimal(row.get("tickSz")),
                contract_value=decimal(row.get("ctVal")),
                contract_multiplier=decimal(row.get("ctMult"), Decimal("1")),
                contract_type=str(row.get("ctType", "linear")),
                active=row.get("state") == "live",
            )
            if item.quantity_step > ZERO and item.base_per_quantity > ZERO:
                self._instruments[normalized] = item
                self._native_to_normalized[item.symbol] = normalized
        return dict(self._instruments)

    async def get_mark_price(self, normalized_symbol: str) -> Decimal:
        instrument = await self.get_instrument(normalized_symbol)
        if instrument is None:
            raise NonRetryableExchangeError(f"OKX instrument not found: {normalized_symbol}")
        rows = await self._request(
            "GET", "/api/v5/public/mark-price", {"instType": "SWAP", "instId": instrument.symbol}
        )
        if not rows:
            raise NonRetryableExchangeError(f"OKX mark price missing: {normalized_symbol}")
        return decimal(rows[0]["markPx"])

    async def get_positions(self) -> dict[str, PositionSnapshot]:
        await self.get_instruments()
        rows = await self._request("GET", "/api/v5/account/positions", {"instType": "SWAP"}, private=True)
        positions: dict[str, PositionSnapshot] = {}
        for row in rows:
            native = str(row.get("instId", ""))
            normalized = self._native_to_normalized.get(native)
            if normalized is None:
                continue
            pos = decimal(row.get("pos"))
            if pos == ZERO:
                continue
            pos_side = str(row.get("posSide", "net"))
            if pos_side == "long":
                side = PositionSide.LONG
            elif pos_side == "short":
                side = PositionSide.SHORT
            else:
                side = PositionSide.LONG if pos > ZERO else PositionSide.SHORT
            if normalized in positions:
                raise UnsafeOperation(
                    f"both OKX long/short legs are open for {normalized}; reconciliation is ambiguous"
                )
            mark = decimal(row.get("markPx"))
            instrument = self._instruments[normalized]
            notional = abs(decimal(row.get("notionalUsd")))
            if notional == ZERO:
                notional = instrument.notional_for_quantity(abs(pos), mark)
            positions[normalized] = PositionSnapshot(
                exchange=self.exchange,
                symbol=native,
                normalized_symbol=normalized,
                side=side,
                quantity=abs(pos),
                notional_usdt=notional,
                entry_price=decimal(row.get("avgPx")) or None,
                mark_price=mark,
                leverage=decimal(row.get("lever"), Decimal("1")),
                margin_mode=MarginMode(str(row.get("mgnMode", "cross"))),
                unrealized_pnl=decimal(row.get("upl")),
                liquidation_price=decimal(row.get("liqPx")) or None,
            )
        return positions

    async def get_position_mode(self) -> PositionMode:
        rows = await self._request("GET", "/api/v5/account/config", private=True)
        mode = rows[0].get("posMode") if rows else None
        self._position_mode = PositionMode.HEDGE if mode == "long_short_mode" else PositionMode.ONE_WAY
        return self._position_mode

    async def get_margin_mode(self, normalized_symbol: str) -> MarginMode | None:
        position = await self.get_position(normalized_symbol)
        return position.margin_mode if position else None

    async def set_leverage(
        self, normalized_symbol: str, leverage: Decimal, margin_mode: MarginMode, side: PositionSide
    ) -> None:
        instrument = await self.get_instrument(normalized_symbol)
        if instrument is None:
            raise NonRetryableExchangeError(f"OKX instrument not found: {normalized_symbol}")
        body: dict[str, str] = {
            "instId": instrument.symbol,
            "lever": format(leverage, "f"),
            "mgnMode": margin_mode.value,
        }
        mode = self._position_mode or await self.get_position_mode()
        if mode == PositionMode.HEDGE and margin_mode == MarginMode.ISOLATED:
            body["posSide"] = side.value
        await self._request("POST", "/api/v5/account/set-leverage", body=body, private=True)

    async def set_margin_mode(self, normalized_symbol: str, margin_mode: MarginMode) -> None:
        # OKX selects cross/isolated with tdMode on each order.
        # No account-wide mutation is needed or attempted.
        if await self.get_instrument(normalized_symbol) is None:
            raise NonRetryableExchangeError(f"OKX instrument not found: {normalized_symbol}")

    async def place_market_order(self, request: OrderRequest) -> OrderResult:
        mode = self._position_mode or await self.get_position_mode()
        body: dict[str, Any] = {
            "instId": request.symbol,
            "tdMode": request.margin_mode.value,
            "side": request.side.value,
            "ordType": "market",
            "sz": format(request.quantity, "f"),
            "clOrdId": request.client_order_id,
        }
        if mode == PositionMode.HEDGE:
            body["posSide"] = request.position_side.value
        else:
            body["posSide"] = "net"
            body["reduceOnly"] = request.reduce_only
        rows = await self._request(
            "POST", "/api/v5/trade/order", body=body, private=True, order_submission=True
        )
        if not rows:
            raise UnknownOrderState(request.client_order_id, "OKX returned no order acknowledgement")
        row = rows[0]
        if str(row.get("sCode", "0")) != "0":
            raise NonRetryableExchangeError(f"OKX order rejected {row.get('sCode')}: {row.get('sMsg', '')}")
        # ACK does not prove a fill. Query once, then reconciliation verifies the authoritative position.
        verified = await self.verify_order(request.symbol, request.client_order_id)
        return verified or OrderResult(
            exchange=self.exchange,
            symbol=request.symbol,
            client_order_id=request.client_order_id,
            order_id=str(row.get("ordId")) if row.get("ordId") else None,
            status=OrderStatus.NEW,
        )

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
            rows = await self._request(
                "GET",
                "/api/v5/trade/order",
                {"instId": symbol, "clOrdId": client_order_id},
                private=True,
            )
        except NonRetryableExchangeError as exc:
            if "51603" in str(exc):
                return None
            raise
        if not rows:
            return None
        row = rows[0]
        raw = str(row.get("state", "live"))
        statuses = {
            "live": OrderStatus.NEW,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.REJECTED,
        }
        return OrderResult(
            exchange=self.exchange,
            symbol=symbol,
            client_order_id=client_order_id,
            order_id=str(row.get("ordId")) if row.get("ordId") else None,
            status=statuses.get(raw, OrderStatus.UNKNOWN),
            filled_quantity=decimal(row.get("accFillSz")),
            average_price=decimal(row.get("avgPx")) or None,
            raw_status=raw,
        )

    async def connect_private_ws(
        self, on_event: EventCallback, on_state: StateCallback | None = None
    ) -> None:
        backoff = 1.0
        connected_once = False
        while not self._closed.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=None, close_timeout=5) as ws:
                    timestamp = str(int(time.time() + self._clock_offset_ms / 1000))
                    sign = self._sign(f"{timestamp}GET/users/self/verify")
                    await ws.send(
                        json.dumps(
                            {
                                "op": "login",
                                "args": [
                                    {
                                        "apiKey": self.api_key,
                                        "passphrase": self._passphrase,
                                        "timestamp": timestamp,
                                        "sign": sign,
                                    }
                                ],
                            }
                        )
                    )
                    login = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if str(login.get("code", "0")) != "0":
                        raise AuthenticationError("OKX private WebSocket login failed")
                    await ws.send(
                        json.dumps(
                            {
                                "op": "subscribe",
                                "args": [
                                    {"channel": "account"},
                                    {"channel": "positions", "instType": "SWAP"},
                                    {"channel": "orders", "instType": "SWAP"},
                                ],
                            }
                        )
                    )
                    state = "reconnected" if connected_once else "connected"
                    connected_once = True
                    if on_state:
                        await on_state(state)
                    backoff = 1.0
                    while not self._closed.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=25)
                        except TimeoutError:
                            await ws.send("ping")
                            continue
                        if raw == "pong":
                            continue
                        message = json.loads(raw)
                        if message.get("arg", {}).get("channel") in {"account", "positions", "orders"}:
                            await on_event()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("OKX private WS disconnected error=%s retry_in=%s", exc, backoff)
                if on_state:
                    await on_state("disconnected")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def close(self) -> None:
        self._closed.set()
        await self.http.close()
