r"""Relatório do modo sombra pela regra de parada pré-registrada (Fase 6c).

Refaz a simulação a partir dos dados públicos (não precisa do banco) e mostra
o estágio: coletando (< 100 trades), olhada intermediária (só informativa) ou
final (300 trades ou 12 meses: veredito oficial). Grava o relatório em
`artifacts/shadow/`. O veredito final entra no registro de hipóteses por PR.

Uso (PowerShell):
    .\.venv\Scripts\python scripts/shadow_report.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.backtest.lab import to_markdown
from app.backtest.registry import load_declared
from app.backtest.shadow import spec_from_registry
from app.backtest.shadow_report import evaluate_shadow, header_markdown
from app.core.config import settings

OUT_DIR = Path("artifacts/shadow")


async def main() -> int:
    hypothesis = settings.shadow_hypothesis
    spec = spec_from_registry(hypothesis, load_declared(Path(settings.shadow_registry_path)))
    if spec is None:
        print(f"{hypothesis}: hipótese sem início registrado (shadow_start).")
        return 1
    evaluation = await evaluate_shadow(spec)
    text = header_markdown(evaluation)
    if evaluation.report is not None:
        text += "\n" + to_markdown(evaluation.report)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = evaluation.generated_at.strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"{stamp}_{hypothesis}.md"
    path.write_text(text, encoding="utf-8")
    print(header_markdown(evaluation))
    if evaluation.report is not None:
        print("Gate:", "APROVADA" if evaluation.report.verdict.approved else "REPROVADA")
    print(f"Relatório: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
