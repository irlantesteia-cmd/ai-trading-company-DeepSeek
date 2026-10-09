"""Cada módulo de `app/` precisa ser importável como ponto de entrada.

Um ciclo de import só aparece quando o módulo "errado" é importado primeiro
(ex.: `app.ml.inference` antes de `app.strategies`), então importar o pacote
inteiro uma vez não basta. Para cada módulo, um import a frio: os módulos
`app.*` são removidos de `sys.modules` antes. Roda num subprocesso para não
afetar o estado de import do pytest.
"""

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_SCRIPT = r"""
import importlib, pkgutil, sys, traceback
import app

names = sorted(m.name for m in pkgutil.walk_packages(app.__path__, "app."))
failed = []
for name in names:
    for loaded in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[loaded]
    try:
        importlib.import_module(name)
    except Exception:
        failed.append((name, traceback.format_exc(limit=1).strip().splitlines()[-1]))
print(f"checked={len(names)}")
for name, err in failed:
    print(f"FAIL {name}: {err}")
sys.exit(1 if failed else 0)
"""


def test_every_app_module_imports_cold():
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,  # o returncode é verificado abaixo, com a saída na mensagem
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "checked=" in proc.stdout
