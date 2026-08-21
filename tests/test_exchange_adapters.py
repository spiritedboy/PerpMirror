from decimal import Decimal

import httpx
import pytest

from perpmirror.enums import MarginMode, OrderSide, PositionMode, PositionSide
from perpmirror.exceptions import AuthenticationError
from perpmirror.exchanges.binance import BinanceFuturesClient
from perpmirror.exchanges.okx import OkxSwapClient
from perpmirror.models import OrderRequest


@pytest.mark.asyncio
async def test_binance_exchange_info_prefers_market_lot_size(monkeypatch) -> None:
    client = BinanceFuturesClient("key", "secret")

    async def public(path, params=None):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {
                            "filterType": "MARKET_LOT_SIZE",
                            "stepSize": "0.01",
                            "minQty": "0.01",
                            "maxQty": "100",
                        },
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    ],
                }
            ]
        }

    monkeypatch.setattr(client, "_public", public)
    instrument = (await client.get_instruments())["BTC-USDT-PERP"]
    assert instrument.quantity_step == Decimal("0.01")
    assert instrument.min_notional == Decimal("5")
    await client.close()


@pytest.mark.asyncio
async def test_okx_instrument_metadata_preserves_contract_units(monkeypatch) -> None:
    client = OkxSwapClient("key", "secret", "pass")

    async def request(method, path, params=None, body=None, **kwargs):
        return [
            {
                "instId": "BTC-USDT-SWAP",
                "instType": "SWAP",
                "baseCcy": "",
                "quoteCcy": "",
                "settleCcy": "USDT",
                "ctValCcy": "BTC",
                "ctType": "linear",
                "ctVal": "0.01",
                "ctMult": "1",
                "lotSz": "1",
                "minSz": "1",
                "maxMktSz": "1000",
                "tickSz": "0.1",
                "state": "live",
            }
        ]

    monkeypatch.setattr(client, "_request", request)
    instrument = (await client.get_instruments())["BTC-USDT-PERP"]
    assert instrument.base_per_quantity == Decimal("0.01")
    assert instrument.quantity_for_notional(Decimal("600"), Decimal("60000")) == 1
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("50105", "passphrase incorrect"),
        ("50110", "Invalid IP"),
        ("50111", "Invalid OK_ACCESS_KEY"),
        ("50113", "Invalid signature"),
    ],
)
async def test_okx_http_401_preserves_authentication_reason(monkeypatch, code, message) -> None:
    client = OkxSwapClient("key", "secret", "pass")

    async def request(method, path, **kwargs):
        assert kwargs["allow_error_response"] is True
        return httpx.Response(401, json={"code": code, "msg": message})

    monkeypatch.setattr(client.http, "request", request)
    with pytest.raises(AuthenticationError, match=rf"{code}.*{message}"):
        await client.get_position_mode()
    await client.close()


def order_request(*, reduce_only: bool = True) -> OrderRequest:
    return OrderRequest(
        symbol="BTCUSDT",
        normalized_symbol="BTC-USDT-PERP",
        side=OrderSide.SELL,
        position_side=PositionSide.LONG,
        quantity=Decimal("1"),
        reduce_only=reduce_only,
        client_order_id="pm_test",
        margin_mode=MarginMode.CROSS,
    )


@pytest.mark.asyncio
async def test_binance_hedge_mode_uses_position_side_without_reduce_only(monkeypatch) -> None:
    client = BinanceFuturesClient("key", "secret")
    client._position_mode = PositionMode.HEDGE
    captured = {}

    async def signed(method, path, params=None, **kwargs):
        captured.update(params or {})
        return {"symbol": "BTCUSDT", "status": "FILLED", "executedQty": "1"}

    monkeypatch.setattr(client, "_signed", signed)
    await client.place_market_order(order_request())
    assert captured["positionSide"] == "LONG"
    assert "reduceOnly" not in captured
    await client.close()


@pytest.mark.asyncio
async def test_binance_one_way_reduce_has_both_and_reduce_only(monkeypatch) -> None:
    client = BinanceFuturesClient("key", "secret")
    client._position_mode = PositionMode.ONE_WAY
    captured = {}

    async def signed(method, path, params=None, **kwargs):
        captured.update(params or {})
        return {"symbol": "BTCUSDT", "status": "FILLED", "executedQty": "1"}

    monkeypatch.setattr(client, "_signed", signed)
    await client.place_market_order(order_request())
    assert captured["positionSide"] == "BOTH"
    assert captured["reduceOnly"] == "true"
    await client.close()


@pytest.mark.asyncio
async def test_okx_net_mode_uses_posside_and_reduce_only(monkeypatch) -> None:
    client = OkxSwapClient("key", "secret", "pass")
    client._position_mode = PositionMode.ONE_WAY
    captured = {}

    async def request(method, path, params=None, body=None, **kwargs):
        if method == "POST":
            captured.update(body or {})
            return [{"sCode": "0", "ordId": "1"}]
        return [{"state": "filled", "ordId": "1", "accFillSz": "1", "avgPx": "100"}]

    monkeypatch.setattr(client, "_request", request)
    await client.place_market_order(order_request())
    assert captured["posSide"] == "net"
    assert captured["reduceOnly"] is True
    assert captured["tdMode"] == "cross"
    await client.close()
