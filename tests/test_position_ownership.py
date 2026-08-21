import json
from pathlib import Path

import pytest

from perpmirror.exceptions import ConfigurationError, UnsafeOperation
from perpmirror.monitoring.ownership import PositionOwnership

IDENTITY = {
    "leader": {"id": "leader01", "exchange": "binance"},
    "followers": {"okx_fixed": "okx"},
}


def ownership(path: Path, *, persist: bool = True) -> PositionOwnership:
    return PositionOwnership(path, identity=IDENTITY, persist_changes=persist)


def test_first_start_protects_follower_and_blocks_existing_leader(tmp_path: Path) -> None:
    state = ownership(tmp_path / "ownership.json")
    state.initialize({"BTC-USDT-PERP"}, {"okx_fixed": {"ETH-USDT-PERP"}})

    assert state.is_protected("okx_fixed", "ETH-USDT-PERP")
    assert state.candidate_symbols(
        "okx_fixed", {"BTC-USDT-PERP"}
    ) == set()
    assert (tmp_path / "ownership.json").exists()


def test_leader_startup_symbol_only_becomes_eligible_after_flat(tmp_path: Path) -> None:
    state = ownership(tmp_path / "ownership.json")
    state.initialize({"BTC-USDT-PERP"}, {"okx_fixed": set()})

    assert not state.observe_leader({"BTC-USDT-PERP"})
    assert state.observe_leader(set()) == {"BTC-USDT-PERP"}
    assert state.candidate_symbols("okx_fixed", {"BTC-USDT-PERP"}) == {
        "BTC-USDT-PERP"
    }


def test_managed_symbol_survives_restart_and_is_not_reprotected(tmp_path: Path) -> None:
    path = tmp_path / "ownership.json"
    first = ownership(path)
    first.initialize(set(), {"okx_fixed": set()})
    first.claim("okx_fixed", "BTC-USDT-PERP")

    restarted = ownership(path)
    restarted.initialize({"BTC-USDT-PERP"}, {"okx_fixed": {"BTC-USDT-PERP"}})

    assert restarted.is_managed("okx_fixed", "BTC-USDT-PERP")
    assert not restarted.is_protected("okx_fixed", "BTC-USDT-PERP")
    assert restarted.candidate_symbols("okx_fixed", {"BTC-USDT-PERP"}) == {
        "BTC-USDT-PERP"
    }


def test_unmanaged_position_discovered_after_start_is_protected(tmp_path: Path) -> None:
    state = ownership(tmp_path / "ownership.json")
    state.initialize(set(), {"okx_fixed": set()})

    discovered = state.protect_unmanaged("okx_fixed", {"SOL-USDT-PERP"})

    assert discovered == {"SOL-USDT-PERP"}
    assert state.is_protected("okx_fixed", "SOL-USDT-PERP")
    assert state.candidate_symbols("okx_fixed", {"SOL-USDT-PERP"}) == set()
    with pytest.raises(UnsafeOperation, match="protected"):
        state.claim("okx_fixed", "SOL-USDT-PERP")


def test_release_allows_future_cycle_or_manual_protection(tmp_path: Path) -> None:
    state = ownership(tmp_path / "ownership.json")
    state.initialize(set(), {"okx_fixed": set()})
    state.claim("okx_fixed", "BTC-USDT-PERP")
    state.release("okx_fixed", "BTC-USDT-PERP")

    assert not state.is_managed("okx_fixed", "BTC-USDT-PERP")
    state.protect_unmanaged("okx_fixed", {"BTC-USDT-PERP"})
    assert state.is_protected("okx_fixed", "BTC-USDT-PERP")


def test_dry_run_never_creates_or_updates_state_file(tmp_path: Path) -> None:
    path = tmp_path / "ownership.json"
    state = ownership(path, persist=False)
    state.initialize({"BTC-USDT-PERP"}, {"okx_fixed": set()})
    state.observe_leader(set())
    state.claim("okx_fixed", "BTC-USDT-PERP")

    assert not path.exists()


def test_state_rejects_different_account_identity(tmp_path: Path) -> None:
    path = tmp_path / "ownership.json"
    state = ownership(path)
    state.initialize(set(), {"okx_fixed": set()})
    payload = json.loads(path.read_text())
    assert payload["identity"] == IDENTITY

    changed = PositionOwnership(
        path,
        identity={
            "leader": {"id": "other", "exchange": "binance"},
            "followers": {"okx_fixed": "okx"},
        },
        persist_changes=True,
    )
    with pytest.raises(ConfigurationError, match="identity"):
        changed.initialize(set(), {"okx_fixed": set()})
