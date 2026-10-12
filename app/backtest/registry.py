"""Registro de hipóteses e execuções do laboratório (Fase 4b).

Por que existe: quanto mais hipóteses testamos, maior a chance de uma passar
por sorte. Com 20 testes independentes a 5%, espera-se um "aprovado" falso.
Então o laboratório precisa saber quantas hipóteses já foram declaradas para
corrigir o p-valor (Bonferroni: p × N, conservador).

Dois registros:
- **Hipóteses declaradas** (`docs/research/hypotheses.json`, versionado): um
  item por veredito oficial, com a configuração principal declarada antes de
  rodar. É o N da correção. Varreduras e testes exploratórios não entram.
- **Execuções** (`artifacts/lab/registry.jsonl`, local): toda execução, com o
  commit do código, a configuração e o resultado — para reproduzir depois.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

DECLARED_PATH = Path("docs/research/hypotheses.json")
RUNS_PATH = Path("artifacts/lab/registry.jsonl")


class DeclaredHypothesis(BaseModel):
    id: str            # ex.: "L3b-trend-breakout"
    date: str          # AAAA-MM-DD do veredito
    strategy: str
    params: dict[str, float] = {}
    universe: str      # ativos, timeframes e período, em texto
    verdict: str       # APROVADA / REPROVADA
    net_pct: float | None = None       # líquido médio por trade (ou período)
    ci95_low: float | None = None
    timing_p: float | None = None      # p do timing aleatório (sem correção)
    report: str | None = None          # relatório em artifacts/lab
    notes: str = ""


def load_declared(path: Path = DECLARED_PATH) -> list[DeclaredHypothesis]:
    if not path.exists():
        return []
    return [DeclaredHypothesis(**x) for x in json.loads(path.read_text(encoding="utf-8"))]


def append_declared(entry: DeclaredHypothesis, path: Path = DECLARED_PATH) -> int:
    """Acrescenta um veredito declarado; recusa id repetido. Retorna o novo N."""
    entries = load_declared(path)
    if any(e.id == entry.id for e in entries):
        raise ValueError(f"hipótese {entry.id!r} já registrada")
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([e.model_dump() for e in entries], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",  # arquivo versionado: LF também no Windows
    )
    return len(entries)


def bonferroni(p_value: float, tests: int) -> float:
    return min(1.0, p_value * max(1, tests))


def git_commit() -> str:
    """Commit atual (com `+dirty` se houver mudanças locais); `desconhecido` fora do git."""
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "desconhecido"
    return f"{head}+dirty" if dirty else head


def record_run(kind: str, summary: dict[str, Any], path: Path = RUNS_PATH) -> dict[str, Any]:
    """Acrescenta uma linha ao registro local de execuções."""
    entry = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "kind": kind,
        **summary,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry
