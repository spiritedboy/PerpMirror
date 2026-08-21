from perpmirror.okx_check import mask_key, parse_permissions


def test_parse_okx_permissions() -> None:
    assert parse_permissions("read_only,trade") == frozenset({"read_only", "trade"})
    assert parse_permissions(["read_only", "trade"]) == frozenset({"read_only", "trade"})


def test_mask_key_does_not_disclose_full_value() -> None:
    value = "12345678-1234-1234-1234-123456789012"
    masked = mask_key(value)
    assert masked == "1234...9012"
    assert value not in masked
