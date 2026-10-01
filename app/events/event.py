from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.order import Order
from app.domain.models.signal import Signal


class Event(BaseModel):
    """Evento base — todos os eventos do sistema derivam deste."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def name(self) -> str:
        return self.__class__.__name__


# --- Eventos de Mercado ----------------------------------------------------
class TickerUpdated(Event):
    symbol: str
    market_type: str
    last: float


class CandleClosed(Event):
    symbol: str
    interval: str
    close: float


class OrderBookUpdated(Event):
    symbol: str
    market_type: str


# --- Eventos de Ordem / Execução -------------------------------------------
class OrderSubmitted(Event):
    client_order_id: str
    symbol: str


class OrderFilled(Event):
    """Ordem preenchida (total ou parcialmente).

    Carrega o `Order` completo — com a lista de `fills` — para permitir que
    ouvintes persistam cada fill como um `TradeORM`.
    """

    exchange_order_id: str
    symbol: str
    filled_quantity: float
    average_price: float
    order: Order | None = None


class OrderRejected(Event):
    client_order_id: str
    reason: str


# --- Eventos de Sinal / Decisão --------------------------------------------
class SignalGenerated(Event):
    """Sinal gerado por um `AssetAgent` (via estratégia).

    `signal` é opcional para backward compatibility — payloads antigos
    continuam válidos. Quando presente, o `TradingManager` pode processá-lo
    (auto-execução, sujeita a `signal_auto_execution_enabled`).
    """

    signal_id: str
    symbol: str
    agent: str
    direction: str
    confidence: float
    signal: Signal | None = None


class SignalApproved(Event):
    signal_id: str
    approved_quantity: float


class SignalRejected(Event):
    signal_id: str
    reason: str


class OrderIntentCreated(Event):
    intent_id: str
    signal_id: str
    symbol: str
    side: str
    quantity: float


# --- Eventos de Risco / Portfólio ------------------------------------------
class RiskLimitBreached(Event):
    rule: str
    detail: str


class PositionOpened(Event):
    symbol: str
    quantity: float


class PositionClosed(Event):
    symbol: str
    pnl: float


# --- Eventos de Agente -----------------------------------------------------
class AgentStarted(Event):
    agent_name: str
    role: str


class AgentStopped(Event):
    agent_name: str
    role: str


# --- Eventos Sistêmicos ----------------------------------------------------
class SystemStarted(Event):
    version: str


class SystemStopped(Event):
    reason: str


class HealthCheckFailed(Event):
    component: str
    detail: str