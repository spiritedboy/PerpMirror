from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from perpmirror.exceptions import ConfigurationError, UnsafeOperation

logger = logging.getLogger(__name__)

STATE_VERSION = 2


@dataclass(slots=True)
class FollowerOwnership:
    protected_symbols: set[str] = field(default_factory=set)
    managed_symbols: set[str] = field(default_factory=set)
    observed_managed_symbols: set[str] = field(default_factory=set)
    suspended_symbols: set[str] = field(default_factory=set)


class PositionOwnership:
    """Persisted symbol ownership for new-position-only copy mode.

    Exchange position snapshots are aggregate values and cannot identify which
    system created part of a position.  The safe isolation boundary is therefore
    the normalized symbol: a symbol is either protected or managed, never both.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        identity: dict[str, Any],
        persist_changes: bool,
    ) -> None:
        self.path = Path(path)
        self.identity = identity
        self.persist_changes = persist_changes
        self.blocked_leader_symbols: set[str] = set()
        self.followers: dict[str, FollowerOwnership] = {}
        self.initialized = False
        self._loaded_version = STATE_VERSION

    def initialize(
        self,
        leader_symbols: set[str],
        follower_symbols: dict[str, set[str]],
    ) -> None:
        if self.initialized:
            return
        if self.path.exists():
            self._load()
            logger.info(
                "OWNERSHIP_STATE_LOADED path=%s blocked_leader_symbols=%s",
                self.path,
                len(self.blocked_leader_symbols),
            )
        else:
            self.blocked_leader_symbols = set(leader_symbols)
            self.followers = {
                follower_id: FollowerOwnership(protected_symbols=set(symbols))
                for follower_id, symbols in follower_symbols.items()
            }
            self._save()
            logger.info(
                "OWNERSHIP_STATE_CREATED path=%s protected_follower_symbols=%s "
                "blocked_leader_symbols=%s",
                self.path,
                sum(len(item.protected_symbols) for item in self.followers.values()),
                len(self.blocked_leader_symbols),
            )
            for symbol in sorted(self.blocked_leader_symbols):
                logger.info(
                    "LEADER_POSITION_BLOCKED symbol=%s reason=startup_existing wait_until_flat=true",
                    symbol,
                )
            for follower_id, ownership in sorted(self.followers.items()):
                for symbol in sorted(ownership.protected_symbols):
                    logger.info(
                        "FOLLOWER_POSITION_PROTECTED follower=%s symbol=%s "
                        "reason=startup_existing",
                        follower_id,
                        symbol,
                    )
        self._validate_follower_ids(follower_symbols)
        self.initialized = True
        if self._loaded_version < STATE_VERSION:
            self._save()
            logger.info(
                "OWNERSHIP_STATE_MIGRATED path=%s from_version=%s to_version=%s",
                self.path,
                self._loaded_version,
                STATE_VERSION,
            )
            self._loaded_version = STATE_VERSION
        for follower_id, symbols in follower_symbols.items():
            self.protect_unmanaged(follower_id, symbols)

    def observe_leader(self, current_symbols: set[str]) -> set[str]:
        self._require_initialized()
        released = self.blocked_leader_symbols - current_symbols
        resumed: list[tuple[str, str]] = []
        if released:
            self.blocked_leader_symbols.difference_update(released)
            for symbol in sorted(released):
                logger.info("LEADER_BASELINE_RELEASED symbol=%s eligible_on_next_open=true", symbol)
        for follower_id, ownership in self.followers.items():
            follower_resumed = ownership.suspended_symbols - current_symbols
            if follower_resumed:
                ownership.suspended_symbols.difference_update(follower_resumed)
                resumed.extend((follower_id, symbol) for symbol in sorted(follower_resumed))
        if released or resumed:
            self._save()
        for follower_id, symbol in resumed:
            logger.info(
                "FOLLOWER_COPY_RESUMED follower=%s symbol=%s reason=leader_flat "
                "eligible_on_next_open=true",
                follower_id,
                symbol,
            )
        return released

    def observe_follower_positions(
        self,
        follower_id: str,
        current_symbols: set[str],
        leader_symbols: set[str],
    ) -> set[str]:
        """Suspend a managed symbol that disappears while its leader leg is still open."""
        self._require_initialized()
        ownership = self._follower(follower_id)
        newly_observed = (ownership.managed_symbols & current_symbols) - (
            ownership.observed_managed_symbols
        )
        disappeared = ownership.observed_managed_symbols - current_symbols
        suspended = disappeared & leader_symbols
        if newly_observed:
            ownership.observed_managed_symbols.update(newly_observed)
        if suspended:
            ownership.managed_symbols.difference_update(suspended)
            ownership.observed_managed_symbols.difference_update(suspended)
            ownership.suspended_symbols.update(suspended)
        if newly_observed or suspended:
            self._save()
        for symbol in sorted(suspended):
            logger.warning(
                "FOLLOWER_POSITION_DISAPPEARED follower=%s symbol=%s "
                "action=suspend_until_leader_flat",
                follower_id,
                symbol,
            )
        return suspended

    def mark_position_observed(self, follower_id: str, symbol: str) -> None:
        self._require_initialized()
        ownership = self._follower(follower_id)
        if symbol in ownership.managed_symbols and symbol not in ownership.observed_managed_symbols:
            ownership.observed_managed_symbols.add(symbol)
            self._save()

    def protect_unmanaged(self, follower_id: str, current_symbols: set[str]) -> set[str]:
        self._require_initialized()
        ownership = self._follower(follower_id)
        discovered = (
            current_symbols
            - ownership.managed_symbols
            - ownership.protected_symbols
            - ownership.suspended_symbols
        )
        if discovered:
            ownership.protected_symbols.update(discovered)
            self._save()
            for symbol in sorted(discovered):
                logger.warning(
                    "FOLLOWER_POSITION_PROTECTED follower=%s symbol=%s reason=unmanaged_position",
                    follower_id,
                    symbol,
                )
        return discovered

    def candidate_symbols(self, follower_id: str, leader_symbols: set[str]) -> set[str]:
        self._require_initialized()
        ownership = self._follower(follower_id)
        eligible_leader = leader_symbols - self.blocked_leader_symbols
        return ownership.managed_symbols | (
            eligible_leader - ownership.protected_symbols - ownership.suspended_symbols
        )

    def is_managed(self, follower_id: str, symbol: str) -> bool:
        self._require_initialized()
        return symbol in self._follower(follower_id).managed_symbols

    def is_protected(self, follower_id: str, symbol: str) -> bool:
        self._require_initialized()
        return symbol in self._follower(follower_id).protected_symbols

    def is_suspended(self, follower_id: str, symbol: str) -> bool:
        self._require_initialized()
        return symbol in self._follower(follower_id).suspended_symbols

    def claim(self, follower_id: str, symbol: str) -> None:
        self._require_initialized()
        ownership = self._follower(follower_id)
        if symbol in ownership.protected_symbols:
            raise UnsafeOperation(f"cannot manage protected follower position: {follower_id} {symbol}")
        if symbol in ownership.suspended_symbols:
            raise UnsafeOperation(
                f"cannot manage follower position suspended until leader flat: {follower_id} {symbol}"
            )
        if symbol in self.blocked_leader_symbols:
            raise UnsafeOperation(f"cannot manage leader startup position before flat: {symbol}")
        if symbol not in ownership.managed_symbols:
            ownership.managed_symbols.add(symbol)
            self._save()
            logger.info("FOLLOWER_SYMBOL_CLAIMED follower=%s symbol=%s", follower_id, symbol)

    def release(self, follower_id: str, symbol: str) -> None:
        self._require_initialized()
        ownership = self._follower(follower_id)
        if symbol in ownership.managed_symbols:
            ownership.managed_symbols.remove(symbol)
            ownership.observed_managed_symbols.discard(symbol)
            self._save()
            logger.info("FOLLOWER_SYMBOL_RELEASED follower=%s symbol=%s", follower_id, symbol)

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") not in {1, STATE_VERSION}:
                raise ValueError("unsupported state version")
            version = int(raw["version"])
            if raw.get("identity") != self.identity:
                raise ConfigurationError(
                    f"ownership state identity does not match current accounts: {self.path}"
                )
            blocked = self._string_set(raw.get("blocked_leader_symbols"), "blocked_leader_symbols")
            followers_raw = raw.get("followers")
            if not isinstance(followers_raw, dict):
                raise ValueError("followers must be an object")
            followers: dict[str, FollowerOwnership] = {}
            for follower_id, value in followers_raw.items():
                if not isinstance(follower_id, str) or not isinstance(value, dict):
                    raise ValueError("invalid follower ownership entry")
                protected = self._string_set(value.get("protected_symbols"), "protected_symbols")
                managed = self._string_set(value.get("managed_symbols"), "managed_symbols")
                if version == 1:
                    # A v1 managed symbol may have been live before the upgrade.
                    # Treat it as observed so a position missing during downtime
                    # is never reopened without an intervening leader flat state.
                    observed = set(managed)
                    suspended: set[str] = set()
                else:
                    observed = self._string_set(
                        value.get("observed_managed_symbols"), "observed_managed_symbols"
                    )
                    suspended = self._string_set(
                        value.get("suspended_symbols"), "suspended_symbols"
                    )
                overlap = protected & managed
                suspended_overlap = suspended & (protected | managed)
                if overlap or suspended_overlap or not observed <= managed:
                    raise ValueError(f"invalid follower symbol ownership: {follower_id}")
                followers[follower_id] = FollowerOwnership(
                    protected,
                    managed,
                    observed,
                    suspended,
                )
        except ConfigurationError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"invalid ownership state file {self.path}: {exc}") from exc
        self.blocked_leader_symbols = blocked
        self.followers = followers
        self._loaded_version = version

    def _save(self) -> None:
        if not self.persist_changes:
            return
        payload = {
            "version": STATE_VERSION,
            "identity": self.identity,
            "blocked_leader_symbols": sorted(self.blocked_leader_symbols),
            "followers": {
                follower_id: {
                    "protected_symbols": sorted(ownership.protected_symbols),
                    "managed_symbols": sorted(ownership.managed_symbols),
                    "observed_managed_symbols": sorted(ownership.observed_managed_symbols),
                    "suspended_symbols": sorted(ownership.suspended_symbols),
                }
                for follower_id, ownership in sorted(self.followers.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _validate_follower_ids(self, follower_symbols: dict[str, set[str]]) -> None:
        configured = set(follower_symbols)
        persisted = set(self.followers)
        if configured != persisted:
            raise ConfigurationError(
                "ownership state followers do not match current configuration: "
                f"configured={sorted(configured)} persisted={sorted(persisted)}"
            )

    def _follower(self, follower_id: str) -> FollowerOwnership:
        try:
            return self.followers[follower_id]
        except KeyError as exc:
            raise ConfigurationError(f"follower missing from ownership state: {follower_id}") from exc

    def _require_initialized(self) -> None:
        if not self.initialized:
            raise RuntimeError("position ownership is not initialized")

    @staticmethod
    def _string_set(value: Any, name: str) -> set[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{name} must be a string array")
        return set(value)
