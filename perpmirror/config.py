from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from perpmirror.enums import CopyMode, Exchange, MarginMode
from perpmirror.exceptions import ConfigurationError
from perpmirror.models import FollowerConfig, decimal


@dataclass(frozen=True, slots=True)
class AppConfig:
    dry_run: bool = True
    sync_on_start: bool = True
    full_reconcile_interval_seconds: Decimal = Decimal("10")
    event_debounce_seconds: Decimal = Decimal("0.3")
    ws_reconnect_max_seconds: Decimal = Decimal("30")
    heartbeat_interval_seconds: Decimal = Decimal("300")
    max_concurrent_orders: int = 5
    max_reconcile_retries: int = 3
    retry_base_delay_seconds: Decimal = Decimal("0.5")
    position_drift_threshold_percent: Decimal = Decimal("1")
    position_drift_min_usdt: Decimal = Decimal("5")


@dataclass(frozen=True, slots=True)
class AccountConfig:
    id: str
    exchange: Exchange
    api_key_env: str
    secret_key_env: str
    passphrase_env: str | None = None


@dataclass(frozen=True, slots=True)
class RiskConfig:
    allow_short: bool = True
    max_leverage: Decimal = Decimal("20")
    max_single_symbol_notional_usdt: Decimal = Decimal("2000")
    max_total_notional_usdt: Decimal = Decimal("5000")
    max_order_notional_usdt: Decimal = Decimal("1000")
    max_open_symbols: int = 10
    min_order_notional_usdt: Decimal = Decimal("5")
    symbol_allowlist: frozenset[str] = frozenset()
    symbol_blocklist: frozenset[str] = frozenset()
    kill_switch: bool = False
    kill_switch_close_positions: bool = False


@dataclass(frozen=True, slots=True)
class FeishuConfig:
    enabled: bool = False
    webhook_env: str = "FEISHU_WEBHOOK"
    secret_env: str = "FEISHU_SECRET"
    max_retries: int = 3


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppConfig
    leader: AccountConfig
    followers: tuple[FollowerConfig, ...]
    risk: RiskConfig
    feishu: FeishuConfig

    def secret(self, env_name: str | None, *, required: bool = True) -> str:
        value = os.getenv(env_name or "", "").strip()
        if required and not value:
            raise ConfigurationError(f"required environment variable is empty: {env_name}")
        return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be a mapping")
    return value


def load_config(
    path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
    *,
    force_dry_run: bool = False,
) -> Settings:
    load_dotenv(env_path, override=False)
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigurationError(f"configuration file not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    root = _mapping(raw, "config")
    app_raw = _mapping(root.get("app", {}), "app")
    leader_raw = _mapping(root.get("leader"), "leader")
    risk_raw = _mapping(root.get("risk", {}), "risk")
    feishu_raw = _mapping(root.get("feishu", {}), "feishu")

    app = AppConfig(
        dry_run=True if force_dry_run else bool(app_raw.get("dry_run", True)),
        sync_on_start=bool(app_raw.get("sync_on_start", True)),
        full_reconcile_interval_seconds=decimal(app_raw.get("full_reconcile_interval_seconds", 10)),
        event_debounce_seconds=decimal(app_raw.get("event_debounce_seconds", "0.3")),
        ws_reconnect_max_seconds=decimal(app_raw.get("ws_reconnect_max_seconds", 30)),
        heartbeat_interval_seconds=decimal(app_raw.get("heartbeat_interval_seconds", 300)),
        max_concurrent_orders=int(app_raw.get("max_concurrent_orders", 5)),
        max_reconcile_retries=int(app_raw.get("max_reconcile_retries", 3)),
        retry_base_delay_seconds=decimal(app_raw.get("retry_base_delay_seconds", "0.5")),
        position_drift_threshold_percent=decimal(app_raw.get("position_drift_threshold_percent", 1)),
        position_drift_min_usdt=decimal(app_raw.get("position_drift_min_usdt", 5)),
    )
    leader = AccountConfig(
        id=str(leader_raw.get("id", "leader")),
        exchange=Exchange(str(leader_raw["exchange"]).lower()),
        api_key_env=str(leader_raw["api_key_env"]),
        secret_key_env=str(leader_raw["secret_key_env"]),
        passphrase_env=leader_raw.get("passphrase_env"),
    )
    if leader.exchange == Exchange.OKX and not leader.passphrase_env:
        raise ConfigurationError("leader: passphrase_env is required for OKX")
    followers_raw = root.get("followers")
    if not isinstance(followers_raw, list) or not followers_raw:
        raise ConfigurationError("followers must be a non-empty list")
    followers: list[FollowerConfig] = []
    ids: set[str] = set()
    for index, item in enumerate(followers_raw):
        row = _mapping(item, f"followers[{index}]")
        follower_id = str(row["id"])
        if follower_id in ids:
            raise ConfigurationError(f"duplicate follower id: {follower_id}")
        ids.add(follower_id)
        mode = CopyMode(str(row["copy_mode"]).lower())
        fixed_margin = decimal(row.get("fixed_margin_usdt")) if mode == CopyMode.FIXED else None
        ratio = decimal(row.get("copy_ratio")) if mode == CopyMode.RATIO else None
        if mode == CopyMode.FIXED and (fixed_margin is None or fixed_margin <= 0):
            raise ConfigurationError(f"{follower_id}: fixed_margin_usdt must be positive")
        if mode == CopyMode.RATIO and (ratio is None or ratio <= 0):
            raise ConfigurationError(f"{follower_id}: copy_ratio must be positive")
        follower_exchange = Exchange(str(row["exchange"]).lower())
        if follower_exchange == Exchange.OKX and not row.get("passphrase_env"):
            raise ConfigurationError(f"{follower_id}: passphrase_env is required for OKX")
        followers.append(
            FollowerConfig(
                id=follower_id,
                exchange=follower_exchange,
                api_key_env=str(row["api_key_env"]),
                secret_key_env=str(row["secret_key_env"]),
                passphrase_env=row.get("passphrase_env"),
                copy_mode=mode,
                fixed_margin_usdt=fixed_margin,
                copy_ratio=ratio,
                copy_leverage=bool(row.get("copy_leverage", True)),
                fixed_leverage=decimal(row.get("fixed_leverage", 5)),
                max_leverage=decimal(row.get("max_leverage", risk_raw.get("max_leverage", 20))),
                copy_margin_mode=bool(row.get("copy_margin_mode", True)),
                margin_mode=MarginMode(str(row.get("margin_mode", "cross")).lower()),
            )
        )
    risk = RiskConfig(
        allow_short=bool(risk_raw.get("allow_short", True)),
        max_leverage=decimal(risk_raw.get("max_leverage", 20)),
        max_single_symbol_notional_usdt=decimal(risk_raw.get("max_single_symbol_notional_usdt", 2000)),
        max_total_notional_usdt=decimal(risk_raw.get("max_total_notional_usdt", 5000)),
        max_order_notional_usdt=decimal(risk_raw.get("max_order_notional_usdt", 1000)),
        max_open_symbols=int(risk_raw.get("max_open_symbols", 10)),
        min_order_notional_usdt=decimal(risk_raw.get("min_order_notional_usdt", 5)),
        symbol_allowlist=frozenset(map(str, risk_raw.get("symbol_allowlist", []))),
        symbol_blocklist=frozenset(map(str, risk_raw.get("symbol_blocklist", []))),
        kill_switch=bool(risk_raw.get("kill_switch", False)),
        kill_switch_close_positions=bool(risk_raw.get("kill_switch_close_positions", False)),
    )
    feishu = FeishuConfig(
        enabled=bool(feishu_raw.get("enabled", False)),
        webhook_env=str(feishu_raw.get("webhook_env", "FEISHU_WEBHOOK")),
        secret_env=str(feishu_raw.get("secret_env", "FEISHU_SECRET")),
        max_retries=int(feishu_raw.get("max_retries", 3)),
    )
    if not app.dry_run and os.getenv("PERPMIRROR_LIVE_ACK") != "I_UNDERSTAND_REAL_FUNDS_ARE_AT_RISK":
        raise ConfigurationError(
            "LIVE refused: set PERPMIRROR_LIVE_ACK=I_UNDERSTAND_REAL_FUNDS_ARE_AT_RISK explicitly"
        )
    return Settings(app=app, leader=leader, followers=tuple(followers), risk=risk, feishu=feishu)
