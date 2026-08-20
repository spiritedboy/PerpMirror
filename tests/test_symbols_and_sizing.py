from decimal import Decimal

import pytest

from perpmirror.enums import Exchange
from perpmirror.exceptions import InstrumentNotFound
from perpmirror.models import InstrumentInfo
from perpmirror.symbols.mapper import SymbolMapper


def test_symbol_mapper_never_guesses(instrument: InstrumentInfo) -> None:
    mapper = SymbolMapper()
    mapper.add_instruments(Exchange.FAKE, {instrument.normalized_symbol: instrument})
    assert mapper.native_symbol(Exchange.FAKE, "BTC-USDT-PERP") == "BTCUSDT"
    with pytest.raises(InstrumentNotFound):
        mapper.native_symbol(Exchange.FAKE, "NOT-A-REAL-PERP")


def test_binance_market_quantity_floors_without_oversizing(instrument: InstrumentInfo) -> None:
    quantity = instrument.quantity_for_notional(Decimal("100"), Decimal("33"))
    assert quantity == Decimal("3.030")
    assert instrument.notional_for_quantity(quantity, Decimal("33")) <= Decimal("100")


def test_okx_contract_count_uses_ctval_and_ctmult() -> None:
    okx = InstrumentInfo(
        exchange=Exchange.OKX,
        symbol="BTC-USDT-SWAP",
        normalized_symbol="BTC-USDT-PERP",
        base_currency="BTC",
        quote_currency="USDT",
        settle_currency="USDT",
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        contract_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        contract_type="linear",
    )
    assert okx.quantity_for_notional(Decimal("600"), Decimal("60000")) == Decimal("1")
    assert okx.notional_for_quantity(Decimal("3"), Decimal("60000")) == Decimal("1800.00")
