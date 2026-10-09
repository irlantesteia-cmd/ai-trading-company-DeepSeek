"""Geradores de mudança para o `EvolutionLoop`.

`HeuristicChangeGenerator` observa as métricas coletadas pelo
`MetricsCollector` e, quando detecta degradação, propõe um ajuste nos
thresholds de ML via PR que reescreve `config/evolution/thresholds.json`.

O ajuste é sempre na mesma direção: **tighten** (long += step,
short -= step). Isso reduz o número de sinais e busca qualidade. Se já
atingiu o teto/piso, retorna `None`.

Regras (qualquer uma basta, com N >= min_trades):
    - win_rate < win_rate_floor
    - |avg_loss| / avg_win > asymmetry_ceiling
    - total_pnl < pnl_floor

O JSON é a **única** configuração que o loop pode tocar — a
`AutonomyPolicy` bloqueia todo o resto.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from app.github.types import FileChange, ProposedChange

logger = logging.getLogger(__name__)


class HeuristicChangeGenerator:
    """Gera `ProposedChange` de tighten dos thresholds quando métricas degradam."""

    def __init__(
        self,
        *,
        config_path: Path,
        repo_path: str | None = None,
        step: float = 0.05,
        min_trades: int = 20,
        win_rate_floor: float = 0.40,
        asymmetry_ceiling: float = 2.0,
        pnl_floor: float = -50.0,
        max_long_threshold: float = 0.70,
        min_short_threshold: float = 0.30,
        base_branch: str = "main",
    ) -> None:
        if not (0.0 < step <= 0.5):
            raise ValueError("step deve estar em (0, 0.5]")
        if min_trades < 1:
            raise ValueError("min_trades deve ser >= 1")
        if max_long_threshold <= min_short_threshold:
            raise ValueError("max_long_threshold deve ser > min_short_threshold")
        self._path = config_path
        self._repo_path = repo_path or str(config_path).replace("\\", "/")
        self._step = step
        self._min_trades = min_trades
        self._win_rate_floor = win_rate_floor
        self._asymmetry_ceiling = asymmetry_ceiling
        self._pnl_floor = pnl_floor
        self._max_long = max_long_threshold
        self._min_short = min_short_threshold
        self._base_branch = base_branch

    @property
    def config_path(self) -> Path:
        return self._path

    @property
    def repo_path(self) -> str:
        return self._repo_path

    async def __call__(self, metrics: dict[str, float]) -> ProposedChange | None:
        num_trades = int(metrics.get("num_trades", 0))
        if num_trades < self._min_trades:
            logger.debug(
                "change_generator.not_enough_trades",
                extra={"num_trades": num_trades, "min_trades": self._min_trades},
            )
            return None

        win_rate = float(metrics.get("win_rate", 0.0))
        avg_win = float(metrics.get("avg_win", 0.0))
        avg_loss = float(metrics.get("avg_loss", 0.0))
        total_pnl = float(metrics.get("total_pnl", 0.0))

        triggers: list[str] = []
        if win_rate < self._win_rate_floor:
            triggers.append(f"win_rate={win_rate:.3f}<{self._win_rate_floor}")
        if avg_win > 0 and avg_loss < 0:
            ratio = abs(avg_loss) / avg_win
            if ratio > self._asymmetry_ceiling:
                triggers.append(
                    f"|avg_loss|/avg_win={ratio:.2f}>{self._asymmetry_ceiling}"
                )
        if total_pnl < self._pnl_floor:
            triggers.append(f"total_pnl={total_pnl:.2f}<{self._pnl_floor}")

        if not triggers:
            logger.debug("change_generator.healthy", extra={"metrics": metrics})
            return None

        current = self._load_current()
        if current is None:
            logger.warning(
                "change_generator.config_unavailable",
                extra={"path": str(self._path)},
            )
            return None

        new_long = min(
            self._max_long, round(current["long_threshold"] + self._step, 4)
        )
        new_short = max(
            self._min_short, round(current["short_threshold"] - self._step, 4)
        )

        if (
            new_long == current["long_threshold"]
            and new_short == current["short_threshold"]
        ):
            logger.info(
                "change_generator.at_limit",
                extra={
                    "long": new_long,
                    "short": new_short,
                    "triggers": triggers,
                },
            )
            return None

        ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        branch = f"evolution/thresholds-{ts}"

        logger.info(
            "change_generator.proposal_ready",
            extra={
                "long_from": current["long_threshold"],
                "long_to": new_long,
                "short_from": current["short_threshold"],
                "short_to": new_short,
                "triggers": triggers,
                "branch": branch,
                "repo_path": self._repo_path,
            },
        )

        return ProposedChange(
            title=(
                f"chore(evolution): tighten ML thresholds "
                f"({new_long:.2f}/{new_short:.2f})"
            ),
            body=self._build_pr_body(
                current=current,
                new_long=new_long,
                new_short=new_short,
                triggers=triggers,
                metrics=metrics,
            ),
            branch_name=branch,
            base_branch=self._base_branch,
            files=[
                FileChange(
                    path=self._repo_path,
                    content=self._render(
                        long_threshold=new_long, short_threshold=new_short
                    ),
                )
            ],
            labels=["evolution", "autonomous"],
            draft=False,
        )

    # ------------------------------------------------------------------ helpers
    def _load_current(self) -> dict[str, float] | None:
        if not self._path.exists():
            return None
        try:
            # utf-8-sig tolera BOM (arquivos editados no Windows).
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            logger.exception(
                "change_generator.config_read_failed",
                extra={"path": str(self._path)},
            )
            return None
        if not isinstance(raw, dict):
            return None
        try:
            return {
                "long_threshold": float(raw["long_threshold"]),
                "short_threshold": float(raw["short_threshold"]),
            }
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "change_generator.config_invalid",
                extra={"raw": raw},
            )
            return None

    @staticmethod
    def _render(*, long_threshold: float, short_threshold: float) -> str:
        payload = {
            "long_threshold": long_threshold,
            "short_threshold": short_threshold,
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"

    @staticmethod
    def _build_pr_body(
        *,
        current: dict[str, float],
        new_long: float,
        new_short: float,
        triggers: list[str],
        metrics: dict[str, float],
    ) -> str:
        lines = [
            "## Ajuste automático de thresholds ML",
            "",
            "Detectada degradação nas métricas operacionais. Este PR",
            "**apenas aperta** os thresholds (mais seletividade).",
            "Nenhum outro arquivo é tocado — o `EvolutionLoop` só pode",
            "editar `config/evolution/thresholds.json`.",
            "",
            "### Gatilhos",
            *[f"- `{t}`" for t in triggers],
            "",
            "### Mudança",
            "```diff",
            f"- long_threshold:  {current['long_threshold']}",
            f"+ long_threshold:  {new_long}",
            f"- short_threshold: {current['short_threshold']}",
            f"+ short_threshold: {new_short}",
            "```",
            "",
            "### Métricas (janela do `MetricsCollector`)",
            "```json",
            json.dumps(metrics, indent=2),
            "```",
            "",
            "### Ação humana",
            "1. Revisar se o aperto é coerente com o mercado.",
            "2. Merge (Squash) em `main` — o próximo boot aplica.",
            "3. Fechar sem mergear se não concordar; o cooldown evita reincidência.",
            "",
            (
                "_Gerado pelo `EvolutionLoop`. Nenhum arquivo fora de "
                "`config/evolution/` é editado._"
            ),
        ]
        return "\n".join(lines)