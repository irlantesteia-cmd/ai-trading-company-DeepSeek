from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass
class _Beat:
    last_seen: datetime
    count: int = 0


class HeartbeatMonitor:
    """Rastreia batimentos por agente.

    Considera parado (`is_stale`) quando `now - last_seen >= max_age_seconds`.
    O limite é inclusivo: exatamente `max_age_seconds` já conta como stale.
    Agentes desconhecidos são tratados como parados.
    """

    def __init__(self, *, max_age_seconds: float = 60.0) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds deve ser > 0")
        self.max_age_seconds = max_age_seconds
        self._beats: dict[str, _Beat] = {}

    def beat(self, name: str, *, now: datetime | None = None) -> None:
        ts = now or datetime.now(UTC)
        b = self._beats.get(name)
        if b is None:
            self._beats[name] = _Beat(last_seen=ts, count=1)
        else:
            b.last_seen = ts
            b.count += 1

    def last_seen(self, name: str) -> datetime | None:
        b = self._beats.get(name)
        return b.last_seen if b else None

    def beat_count(self, name: str) -> int:
        b = self._beats.get(name)
        return b.count if b else 0

    def is_stale(self, name: str, *, now: datetime | None = None) -> bool:
        last = self.last_seen(name)
        if last is None:
            return True
        now = now or datetime.now(UTC)
        return (now - last).total_seconds() >= self.max_age_seconds

    def stale_agents(self, *, now: datetime | None = None) -> list[str]:
        return [name for name in self._beats if self.is_stale(name, now=now)]

    def known(self) -> list[str]:
        return sorted(self._beats.keys())

    def reset(self, name: str | None = None) -> None:
        if name is None:
            self._beats.clear()
        else:
            self._beats.pop(name, None)