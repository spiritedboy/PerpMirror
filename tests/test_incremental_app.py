from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import make_position

from perpmirror.app import PerpMirrorApp
from perpmirror.config import AccountConfig, AppConfig, FeishuConfig, RiskConfig, Settings
from perpmirror.enums import CopyMode, Exchange
from perpmirror.fake.exchange import FakeExchangeClient
from perpmirror.models import FollowerConfig


def incremental_settings(state_path: Path) -> Settings:
    return Settings(
        app=AppConfig(
            dry_run=False,
            preserve_existing_positions=True,
            ownership_state_file=str(state_path),
            position_drift_min_usdt=Decimal("0"),
        ),
        leader=AccountConfig("leader01", Exchange.FAKE, "LEADER_KEY", "LEADER_SECRET"),
        followers=(
            FollowerConfig(
                id="follower1",
                exchange=Exchange.FAKE,
                api_key_env="FOLLOWER_KEY",
                secret_key_env="FOLLOWER_SECRET",
                passphrase_env=None,
                copy_mode=CopyMode.FIXED,
                fixed_margin_usdt=Decimal("20"),
                copy_ratio=None,
                copy_leverage=False,
                fixed_leverage=Decimal("1"),
                max_leverage=Decimal("1"),
                copy_margin_mode=False,
                margin_mode=make_position("20").margin_mode,
            ),
        ),
        risk=RiskConfig(
            max_leverage=Decimal("2"),
            max_single_symbol_notional_usdt=Decimal("1000"),
            max_total_notional_usdt=Decimal("1000"),
            max_order_notional_usdt=Decimal("1000"),
        ),
        feishu=FeishuConfig(),
    )


@pytest.mark.asyncio
async def test_app_preserves_existing_and_manages_only_new_position(
    monkeypatch, tmp_path: Path, instrument
) -> None:
    eth_instrument = replace(
        instrument,
        symbol="ETHUSDT",
        normalized_symbol="ETH-USDT-PERP",
        base_currency="ETH",
    )
    leader = FakeExchangeClient(
        instruments=[instrument, eth_instrument],
        positions=[make_position("100", symbol="BTC-USDT-PERP")],
    )
    protected_eth = make_position("50", symbol="ETH-USDT-PERP")
    follower = FakeExchangeClient(
        instruments=[instrument, eth_instrument],
        positions=[protected_eth],
    )
    clients = iter((leader, follower))
    monkeypatch.setattr(
        PerpMirrorApp,
        "_client",
        staticmethod(lambda exchange, api_key, secret, passphrase: next(clients)),
    )
    monkeypatch.setenv("LEADER_KEY", "x")
    monkeypatch.setenv("LEADER_SECRET", "x")
    monkeypatch.setenv("FOLLOWER_KEY", "x")
    monkeypatch.setenv("FOLLOWER_SECRET", "x")
    app = PerpMirrorApp(incremental_settings(tmp_path / "ownership.json"))
    app.mapper.add_instruments(Exchange.FAKE, await follower.get_instruments())

    await app.initialize_position_ownership()
    assert await app.full_reconcile() == []
    assert follower.orders == []
    assert (await follower.get_position("ETH-USDT-PERP")).abs_notional == Decimal("50")

    leader._positions.pop("BTC-USDT-PERP")
    assert await app.full_reconcile() == []

    leader._positions["BTC-USDT-PERP"] = make_position("100", symbol="BTC-USDT-PERP")
    opened = await app.full_reconcile()
    assert len(opened) == 1
    assert (await follower.get_position("BTC-USDT-PERP")).abs_notional == Decimal("20")
    assert (await follower.get_position("ETH-USDT-PERP")).abs_notional == Decimal("50")

    # A service restart must retain ownership of the position created above.
    restarted_clients = iter((leader, follower))
    monkeypatch.setattr(
        PerpMirrorApp,
        "_client",
        staticmethod(lambda exchange, api_key, secret, passphrase: next(restarted_clients)),
    )
    app = PerpMirrorApp(incremental_settings(tmp_path / "ownership.json"))
    app.mapper.add_instruments(Exchange.FAKE, await follower.get_instruments())
    await app.initialize_position_ownership()
    assert app.ownership is not None
    assert app.ownership.is_managed("follower1", "BTC-USDT-PERP")

    leader._positions.pop("BTC-USDT-PERP")
    closed = await app.full_reconcile()
    assert len(closed) == 1
    assert await follower.get_position("BTC-USDT-PERP") is None
    assert (await follower.get_position("ETH-USDT-PERP")).abs_notional == Decimal("50")
    assert len(follower.orders) == 2
