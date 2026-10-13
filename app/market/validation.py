"""Validação de dados de mercado (Fase 5d): o bot não calcula sinal sobre
candles incoerentes nem sobre janelas com buracos grandes.

- Candle inválido: preço <= 0, máxima abaixo de abertura/fechamento/mínima,
  mínima acima de abertura/fechamento, volume negativo, fechamento antes da
  abertura.
- Janela inválida: algum candle inválido, tempo que não cresce (repetido ou
  fora de ordem) ou mais de `max_gap_bars` barras faltando seguidas. Buracos
  menores só geram aviso: mal distorcem indicadores de 200 barras.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.domain.models.market import Candle

logger = logging.getLogger(__name__)

_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def interval_ms(interval: str) -> int | None:
    try:
        return int(interval[:-1]) * _UNIT_MS[interval[-1]]
    except (KeyError, ValueError, IndexError):
        return None


def candle_problem(c: Candle) -> str | None:
    """Motivo pelo qual o candle é inválido, ou None se está coerente."""
    if min(c.open, c.high, c.low, c.close) <= 0:
        return "preço <= 0"
    if c.high < max(c.open, c.close, c.low):
        return "máxima abaixo de abertura/fechamento/mínima"
    if c.low > min(c.open, c.close):
        return "mínima acima de abertura/fechamento"
    if c.volume < 0:
        return "volume negativo"
    if c.close_time <= c.open_time:
        return "fechamento antes da abertura"
    return None


def window_problem(candles: Sequence[Candle], interval: str, *, max_gap_bars: int = 3) -> str | None:
    """Motivo pelo qual a janela não serve para calcular sinal, ou None."""
    step = interval_ms(interval)
    for i, c in enumerate(candles):
        problem = candle_problem(c)
        if problem is not None:
            return f"candle {c.open_time.isoformat()}: {problem}"
        if i == 0:
            continue
        prev = candles[i - 1]
        if c.open_time <= prev.open_time:
            return f"tempo não cresce em {c.open_time.isoformat()} (repetido ou fora de ordem)"
        if step:
            missing = round((c.open_time - prev.open_time).total_seconds() * 1000 / step) - 1
            if missing > max_gap_bars:
                return f"{missing} barras faltando antes de {c.open_time.isoformat()}"
            if missing > 0:
                logger.warning(
                    "market_data.small_gap",
                    extra={"symbol": c.symbol, "missing_bars": missing, "before": c.open_time.isoformat()},
                )
    return None
