from decimal import Decimal

import httpx
import pytest

from perpmirror.enums import Exchange, MarginMode, OrderSide, PositionMode, PositionSide
from perpmirror.exceptions import AuthenticationError, NonRetryableExchangeError
from perpmirror.exchanges.binance import BinanceFuturesClient
from perpmirror.exchanges.okx import OkxSwapClient
from perpmirror.models import InstrumentInfo, OrderRequest


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


@pytest.mark.asyncio
async def test_okx_top_level_failure_includes_item_error(monkeypatch) -> None:
    client = OkxSwapClient("key", "secret", "pass")

    async def request(method, path, **kwargs):
        return httpx.Response(
            200,
            json={
                "code": "1",
                "msg": "All operations failed",
                "data": [
                    {
                        "sCode": "51008",
                        "sMsg": "Order failed. Insufficient available balance",
                    }
                ],
            },
        )

    monkeypatch.setattr(client.http, "request", request)
    with pytest.raises(
        NonRetryableExchangeError,
        match=r"All operations failed.*51008.*Insufficient available balance",
    ):
        await client._request("POST", "/api/v5/trade/order", body={"sz": "1"})
    await client.close()


@pytest.mark.asyncio
async def test_okx_rejects_non_object_payload(monkeypatch) -> None:
    client = OkxSwapClient("key", "secret", "pass")

    async def request(method, path, **kwargs):
        return httpx.Response(200, json=[])

    monkeypatch.setattr(client.http, "request", request)
    with pytest.raises(NonRetryableExchangeError, match="invalid payload"):
        await client._request("GET", "/api/v5/account/config", private=True)
    await client.close()


@pytest.mark.asyncio
async def test_okx_signature_fixed_vector() -> None:
    client = OkxSwapClient("key", "test-secret", "pass")
    prehash = "2026-08-21T04:00:00.000ZGET/api/v5/account/config"
    assert client._sign(prehash) == "AwWsvG5HZ3cEdhoufWR8D9FqKZxjxFuthgtJs5MCtU0="
    await client.close()


@pytest.mark.asyncio
async def test_binance_signature_fixed_vector(monkeypatch) -> None:
    client = BinanceFuturesClient("key", "test-secret")
    captured = {}

    async def request(method, path, **kwargs):
        captured.update(kwargs.get("params") or {})
        return httpx.Response(200, json={})

    monkeypatch.setattr("perpmirror.exchanges.binance.time.time", lambda: 1787284800)
    monkeypatch.setattr(client.http, "request", request)
    await client._signed("GET", "/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})
    assert captured["signature"] == (
        "3828c04bcaa963418d2ebcfba6bf4ebf290fbf3b5ce45dc68bc70e10a9eb100c"
    )
    await client.close()


@pytest.mark.asyncio
async def test_binance_positions_use_v2_leverage_and_margin_type(monkeypatch) -> None:
    client = BinanceFuturesClient("key", "secret")
    instrument = InstrumentInfo(
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        normalized_symbol="BTC-USDT-PERP",
        base_currency="BTC",
        quote_currency="USDT",
        settle_currency="USDT",
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
    )
    client._instruments = {instrument.normalized_symbol: instrument}
    client._native_to_normalized = {instrument.symbol: instrument.normalized_symbol}

    async def signed(method, path, params=None, **kwargs):
        assert path == "/fapi/v2/positionRisk"
        return [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.01",
                "entryPrice": "59000",
                "markPrice": "60000",
                "notional": "600",
                "leverage": "20",
                "marginType": "isolated",
                "unRealizedProfit": "10",
                "liquidationPrice": "55000",
            }
        ]

    monkeypatch.setattr(client, "_signed", signed)
    position = (await client.get_positions())["BTC-USDT-PERP"]
    assert position.quantity == Decimal("0.01")
    assert position.abs_notional == Decimal("600")
    assert position.leverage == Decimal("20")
    assert position.margin_mode == MarginMode.ISOLATED
    await client.close()


@pytest.mark.asyncio
async def test_binance_open_position_never_guesses_missing_leverage(monkeypatch) -> None:
    client = BinanceFuturesClient("key", "secret")
    instrument = InstrumentInfo(
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        normalized_symbol="BTC-USDT-PERP",
        base_currency="BTC",
        quote_currency="USDT",
        settle_currency="USDT",
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
    )
    client._instruments = {instrument.normalized_symbol: instrument}
    client._native_to_normalized = {instrument.symbol: instrument.normalized_symbol}

    async def signed(method, path, params=None, **kwargs):
        return [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.01",
                "markPrice": "60000",
                "notional": "600",
                "marginType": "cross",
            }
        ]

    monkeypatch.setattr(client, "_signed", signed)
    with pytest.raises(NonRetryableExchangeError, match="leverage is missing or invalid"):
        await client.get_positions()
    await client.close()


@pytest.mark.asyncio
async def test_okx_position_keeps_contract_count_and_normalized_notional(monkeypatch) -> None:
    client = OkxSwapClient("key", "secret", "pass")

    async def request(method, path, params=None, body=None, **kwargs):
        if path == "/api/v5/public/instruments":
            return [
                {
                    "instId": "HYPE-USDT-SWAP",
                    "instType": "SWAP",
                    "settleCcy": "USDT",
                    "ctValCcy": "HYPE",
                    "ctType": "linear",
                    "ctVal": "10",
                    "ctMult": "1",
                    "lotSz": "1",
                    "minSz": "1",
                    "maxMktSz": "1000",
                    "tickSz": "0.001",
                    "state": "live",
                }
            ]
        assert path == "/api/v5/account/positions"
        return [
            {
                "instId": "HYPE-USDT-SWAP",
                "pos": "3",
                "posSide": "long",
                "markPx": "25",
                "notionalUsd": "750",
                "avgPx": "24",
                "lever": "5",
                "mgnMode": "cross",
                "upl": "30",
                "liqPx": "20",
            }
        ]

    monkeypatch.setattr(client, "_request", request)
    position = (await client.get_positions())["HYPE-USDT-PERP"]
    assert position.quantity == Decimal("3")  # native OKX contract count, used by sz
    assert position.abs_notional == Decimal("750")
    assert position.abs_notional != position.quantity * position.mark_price
    await client.close()


@pytest.mark.asyncio
async def test_okx_account_configuration_reuses_permissions_snapshot(monkeypatch) -> None:
    client = OkxSwapClient("key", "secret", "pass")
    calls = 0

    async def request(method, path, params=None, body=None, **kwargs):
        nonlocal calls
        calls += 1
        return [{"posMode": "long_short_mode", "perm": "read_only,trade"}]

    monkeypatch.setattr(client, "_request", request)
    assert await client.get_position_mode() == PositionMode.HEDGE
    assert await client.get_api_permissions() == frozenset({"read_only", "trade"})
    assert calls == 1
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
