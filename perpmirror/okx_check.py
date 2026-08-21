from __future__ import annotations


def parse_permissions(raw: object) -> frozenset[str]:
    if isinstance(raw, str):
        return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())
    if isinstance(raw, list):
        return frozenset(str(item).strip().lower() for item in raw if str(item).strip())
    return frozenset()


def mask_key(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
