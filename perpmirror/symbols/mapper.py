from __future__ import annotations

from perpmirror.enums import Exchange
from perpmirror.exceptions import InstrumentNotFound
from perpmirror.models import InstrumentInfo


class SymbolMapper:
    """Metadata-backed symbol map. It never guesses symbols with string replacement."""

    def __init__(self) -> None:
        self._by_exchange: dict[Exchange, dict[str, InstrumentInfo]] = {}
        self._native: dict[tuple[Exchange, str], str] = {}

    def add_instruments(self, exchange: Exchange, instruments: dict[str, InstrumentInfo]) -> None:
        valid = {name: item for name, item in instruments.items() if item.active}
        self._by_exchange[exchange] = valid
        for normalized, item in valid.items():
            self._native[(exchange, item.symbol)] = normalized

    def instrument(self, exchange: Exchange, normalized_symbol: str) -> InstrumentInfo:
        try:
            return self._by_exchange[exchange][normalized_symbol]
        except KeyError as exc:
            raise InstrumentNotFound(f"{normalized_symbol} is not available on {exchange.value}") from exc

    def native_symbol(self, exchange: Exchange, normalized_symbol: str) -> str:
        return self.instrument(exchange, normalized_symbol).symbol

    def normalized_symbol(self, exchange: Exchange, native_symbol: str) -> str:
        try:
            return self._native[(exchange, native_symbol)]
        except KeyError as exc:
            raise InstrumentNotFound(f"native symbol {native_symbol} is unknown on {exchange.value}") from exc

    def supported(self, exchange: Exchange, normalized_symbol: str) -> bool:
        return normalized_symbol in self._by_exchange.get(exchange, {})
