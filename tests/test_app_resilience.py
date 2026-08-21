from decimal import Decimal

import pytest
from conftest import make_position

from perpmirror.app import PerpMirrorApp
from perpmirror.config import AccountConfig, AppConfig, FeishuConfig, RiskConfig, Settings
from perpmirror.enums import CopyMode, Exchange, MarginMode
from perpmirror.exceptions import ConfigurationError, NonRetryableExchangeError
from perpmirror.fake.exchange import FakeExchangeClient
from perpmirror.models import FollowerConfig


class PermissionFake(FakeExchangeClient):
    def __init__(self, *args, permissions: frozenset[str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.permissions = permissions

    async def get_api_permissions(self) -> frozenset[str] | None:
        return self.permissions


class SnapshotFailingFake(FakeExchangeClient):
    async def get_equity(self) -> Decimal:
        raise NonRetryableExchangeError("simulated follower outage")


def follower(follower_id: str) -> FollowerConfig:
    return FollowerConfig(
        id=follower_id,
        exchange=Exchange.FAKE,
        api_key_env=f"{follower_id.upper()}_KEY",
        secret_key_env=f"{follower_id.upper()}_SECRET",
        passphrase_env=None,
        copy_mode=CopyMode.FIXED,
        fixed_margin_usdt=Decimal("20"),
        copy_ratio=None,
        copy_leverage=False,
        fixed_leverage=Decimal("1"),
        max_leverage=Decimal("1"),
        copy_margin_mode=False,
        margin_mode=MarginMode.CROSS,
    )


def settings(*follower_ids: str) -> Settings:
    return Settings(
        app=AppConfig(dry_run=False, position_drift_min_usdt=Decimal("0")),
        leader=AccountConfig("leader", Exchange.FAKE, "LEADER_KEY", "LEADER_SECRET"),
        followers=tuple(follower(item) for item in follower_ids),
        risk=RiskConfig(
            max_leverage=Decimal("2"),
            max_single_symbol_notional_usdt=Decimal("1000"),
            max_total_notional_usdt=Decimal("2000"),
            max_order_notional_usdt=Decimal("1000"),
        ),
        feishu=FeishuConfig(),
    )


def install_env(monkeypatch, *follower_ids: str) -> None:
    monkeypatch.setenv("LEADER_KEY", "x")
    monkeypatch.setenv("LEADER_SECRET", "x")
    for follower_id in follower_ids:
        monkeypatch.setenv(f"{follower_id.upper()}_KEY", "x")
        monkeypatch.setenv(f"{follower_id.upper()}_SECRET", "x")


@pytest.mark.asyncio
async def test_runtime_follower_snapshot_failure_does_not_block_other_follower(
    monkeypatch, instrument
) -> None:
    leader = FakeExchangeClient(instruments=[instrument], positions=[make_position("100")])
    unavailable = SnapshotFailingFake(instruments=[instrument])
    healthy = FakeExchangeClient(instruments=[instrument])
    clients = iter((leader, unavailable, healthy))
    monkeypatch.setattr(
        PerpMirrorApp,
        "_client",
        staticmethod(lambda exchange, api_key, secret, passphrase: next(clients)),
    )
    install_env(monkeypatch, "bad", "good")
    app = PerpMirrorApp(settings("bad", "good"))
    app.mapper.add_instruments(Exchange.FAKE, await healthy.get_instruments())

    results = await app.full_reconcile()

    assert [result.follower_id for result in results] == ["good"]
    assert len(healthy.orders) == 1
    assert await unavailable.get_position("BTC-USDT-PERP") is None


@pytest.mark.asyncio
async def test_startup_refuses_follower_without_trade_permission(monkeypatch, instrument) -> None:
    leader = PermissionFake(instruments=[instrument])
    read_only = PermissionFake(instruments=[instrument], permissions=frozenset({"read_only"}))
    clients = iter((leader, read_only))
    monkeypatch.setattr(
        PerpMirrorApp,
        "_client",
        staticmethod(lambda exchange, api_key, secret, passphrase: next(clients)),
    )
    install_env(monkeypatch, "follower")
    app = PerpMirrorApp(settings("follower"))

    with pytest.raises(ConfigurationError, match="follower API key lacks trade permission"):
        await app.startup_checks()


@pytest.mark.asyncio
async def test_startup_refuses_withdraw_permission(monkeypatch, instrument) -> None:
    unsafe_leader = PermissionFake(
        instruments=[instrument], permissions=frozenset({"read_only", "withdraw"})
    )
    follower_client = PermissionFake(
        instruments=[instrument], permissions=frozenset({"read_only", "trade"})
    )
    clients = iter((unsafe_leader, follower_client))
    monkeypatch.setattr(
        PerpMirrorApp,
        "_client",
        staticmethod(lambda exchange, api_key, secret, passphrase: next(clients)),
    )
    install_env(monkeypatch, "follower")
    app = PerpMirrorApp(settings("follower"))

    with pytest.raises(ConfigurationError, match="forbidden withdraw permission"):
        await app.startup_checks()
