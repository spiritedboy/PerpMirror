class PerpMirrorError(Exception):
    """Base exception safe for the application boundary."""


class ConfigurationError(PerpMirrorError):
    pass


class ExchangeError(PerpMirrorError):
    pass


class AuthenticationError(ExchangeError):
    pass


class RetryableExchangeError(ExchangeError):
    pass


class NonRetryableExchangeError(ExchangeError):
    pass


class UnknownOrderState(ExchangeError):
    def __init__(self, client_order_id: str, message: str = "order state is unknown") -> None:
        super().__init__(message)
        self.client_order_id = client_order_id


class UnsafeOperation(PerpMirrorError):
    pass


class InstrumentNotFound(PerpMirrorError):
    pass
