from enum import StrEnum


class Exchange(StrEnum):
    BINANCE = "binance"
    OKX = "okx"
    FAKE = "fake"


class CopyMode(StrEnum):
    FIXED = "fixed"
    RATIO = "ratio"


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class MarginMode(StrEnum):
    CROSS = "cross"
    ISOLATED = "isolated"


class PositionMode(StrEnum):
    ONE_WAY = "one_way"
    HEDGE = "hedge"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    DRY_RUN = "dry_run"


class ReconcileAction(StrEnum):
    OPEN = "open"
    ADD = "add"
    REDUCE = "reduce"
    CLOSE = "close"
    FLIP = "flip"
    NOOP = "noop"
    RISK_BLOCKED = "risk_blocked"
    ORDER_FAILED = "order_failed"
