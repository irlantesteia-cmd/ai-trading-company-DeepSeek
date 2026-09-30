from enum import StrEnum


class MarketType(StrEnum):
    SPOT = "SPOT"
    FUTURES = "FUTURES"  # USDⓈ-M


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT_LIMIT"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"


class OrderStatus(StrEnum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"  # post-only


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"  # one-way mode


class SignalDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class MarketRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    QUIET = "QUIET"
    UNKNOWN = "UNKNOWN"


class MarginType(StrEnum):
    ISOLATED = "ISOLATED"
    CROSSED = "CROSSED"


class AgentRole(StrEnum):
    ORCHESTRATOR = "ORCHESTRATOR"
    TRADING_MANAGER = "TRADING_MANAGER"
    RESEARCH = "RESEARCH"
    RISK = "RISK"
    PORTFOLIO = "PORTFOLIO"
    EXECUTION = "EXECUTION"
    AUDITOR = "AUDITOR"
    QA = "QA"
    ENGINEERING = "ENGINEERING"
    ML = "ML"
    ASSET = "ASSET"


class RiskAction(StrEnum):
    APPROVE = "APPROVE"
    RESIZE = "RESIZE"
    REJECT = "REJECT"