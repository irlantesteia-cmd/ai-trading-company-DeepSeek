"""Retenção de modelos em `models/`: o autotrain grava um modelo novo a cada
boot e nada era apagado (centenas de arquivos em uma semana).

Regras de `prune_models`:
- só considera arquivos na raiz de `model_dir` cujo nome é exatamente
  `<SYMBOL>_h<H>_<YYYYMMDDTHHMMSSZ>.joblib` do símbolo/horizonte pedido
  (subpastas e outros nomes nunca são tocados);
- mantém os `keep` mais recentes e, além deles, sempre o modelo
  `deployable` mais recente (mesmo que seja mais antigo);
- apaga o par `.joblib` + `.json` dos demais;
- `keep <= 0` desliga a retenção.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.ml.model import ForwardReturnClassifier

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^(?P<symbol>[A-Z0-9]+)_h(?P<horizon>\d+)_(?P<ts>\d{8}T\d{6}Z)$")


def _versions(model_dir: Path, symbol: str, horizon: int) -> list[Path]:
    """`.joblib` do símbolo/horizonte, do mais recente para o mais antigo."""
    found: list[tuple[str, Path]] = []
    for path in model_dir.glob("*.joblib"):
        if not path.is_file():
            continue
        m = _VERSION_RE.match(path.stem)
        if m and m["symbol"] == symbol and int(m["horizon"]) == horizon:
            found.append((m["ts"], path))
    return [p for _, p in sorted(found, reverse=True)]


def prune_models(model_dir: Path, *, symbol: str, horizon: int, keep: int) -> list[Path]:
    """Apaga versões antigas. Retorna os `.joblib` removidos. Nunca levanta."""
    if keep <= 0 or not model_dir.is_dir():
        return []

    versions = _versions(model_dir, symbol, horizon)
    protected = set(versions[:keep])
    for path in versions:
        metadata = ForwardReturnClassifier.load_metadata(path)
        if metadata is not None and metadata.deployable:
            protected.add(path)
            break

    removed: list[Path] = []
    for path in versions:
        if path in protected:
            continue
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".json").unlink(missing_ok=True)
            removed.append(path)
        except OSError:
            logger.exception("ml.retention.delete_failed", extra={"path": str(path)})

    if removed:
        logger.info(
            "ml.retention.pruned",
            extra={
                "symbol": symbol,
                "horizon": horizon,
                "removed": len(removed),
                "kept": len(versions) - len(removed),
            },
        )
    return removed
