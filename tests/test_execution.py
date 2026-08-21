import re

from perpmirror.execution.executor import ExecutionEngine


def test_client_order_id_is_okx_and_binance_compatible() -> None:
    identifiers = {
        ExecutionEngine.client_order_id("okx_fixed", "NEIRO-USDT-PERP", "increase")
        for _ in range(100)
    }

    assert len(identifiers) == 100
    assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,31}", value) for value in identifiers)
