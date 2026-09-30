class AITradingError(Exception):
    """Base de todas as exceções do sistema."""


class ConfigurationError(AITradingError):
    ...


class ValidationError(AITradingError):
    ...


class DomainError(AITradingError):
    ...


class ExchangeError(AITradingError):
    ...


class ExchangeConnectionError(ExchangeError):
    ...


class ExchangeAuthError(ExchangeError):
    ...


class ExchangeRateLimitError(ExchangeError):
    ...


class ExchangeOrderRejectedError(ExchangeError):
    ...


class ExchangeInsufficientFundsError(ExchangeError):
    ...


class RiskLimitExceededError(AITradingError):
    ...


class CircuitBreakerOpenError(AITradingError):
    ...


class EventBusError(AITradingError):
    ...