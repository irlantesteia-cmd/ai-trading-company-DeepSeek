from __future__ import annotations

import logging
import re

from app.github.types import FileChange, PolicyResult, PolicyViolation

logger = logging.getLogger(__name__)


def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Glob path-aware → regex.

    Regras (POSIX-style):
        *   casa com qualquer caractere exceto `/`
        **  casa com qualquer caractere (inclui `/`)
        **/ quando seguido de `/`, casa com zero ou mais diretórios
        ?   casa com um caractere (exceto `/`)
    """
    i, n = 0, len(pattern)
    parts: list[str] = ["^"]
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # "**/" → opcionalmente um ou mais diretórios
                if i + 2 < n and pattern[i + 2] == "/":
                    parts.append("(?:.*/)?")
                    i += 3
                    continue
                parts.append(".*")
                i += 2
                continue
            parts.append("[^/]*")
            i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(c))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


class AutonomyPolicy:
    """Decide se um conjunto de mudanças pode ser proposto autonomamente.

    Regras (na ordem):
        1. O PR precisa ter ao menos 1 arquivo.
        2. Número de arquivos <= max_files_per_pr.
        3. Cada arquivo precisa casar com ≥ 1 padrão de allowed_globs.
        4. Cada arquivo não pode casar com nenhum forbidden_globs.
        5. Cada arquivo não pode conter nenhum forbidden_content_patterns.
        6. Cada arquivo não pode exceder max_lines_per_file.
    """

    def __init__(
        self,
        *,
        allowed_globs: list[str],
        forbidden_globs: list[str],
        forbidden_content_patterns: list[str],
        max_files_per_pr: int = 5,
        max_lines_per_file: int = 500,
    ) -> None:
        if not allowed_globs:
            raise ValueError("allowed_globs não pode ser vazio")
        self.allowed_globs = list(allowed_globs)
        self.forbidden_globs = list(forbidden_globs)
        self.forbidden_content_patterns = list(forbidden_content_patterns)
        self.max_files_per_pr = max_files_per_pr
        self.max_lines_per_file = max_lines_per_file
        self._allowed_re = [_compile_glob(p) for p in self.allowed_globs]
        self._forbidden_re = [_compile_glob(p) for p in self.forbidden_globs]

    def check_change(self, files: list[FileChange]) -> PolicyResult:
        violations: list[PolicyViolation] = []

        if not files:
            return PolicyResult(
                allowed=False,
                violations=[
                    PolicyViolation(
                        path="", rule="empty_change", detail="nenhum arquivo proposto"
                    )
                ],
            )

        if len(files) > self.max_files_per_pr:
            violations.append(
                PolicyViolation(
                    path="",
                    rule="max_files_per_pr",
                    detail=f"{len(files)} arquivos > {self.max_files_per_pr}",
                )
            )

        for f in files:
            if not self._matches_any_re(f.path, self._allowed_re):
                violations.append(
                    PolicyViolation(
                        path=f.path,
                        rule="not_allowed",
                        detail="caminho fora da allow-list",
                    )
                )
                continue

            if self._matches_any_re(f.path, self._forbidden_re):
                violations.append(
                    PolicyViolation(
                        path=f.path,
                        rule="forbidden_path",
                        detail="caminho na block-list",
                    )
                )
                continue

            for pattern in self.forbidden_content_patterns:
                if pattern in f.content:
                    violations.append(
                        PolicyViolation(
                            path=f.path,
                            rule="forbidden_content",
                            detail=f"contém padrão proibido: {pattern!r}",
                        )
                    )
                    break

            line_count = f.content.count("\n") + 1
            if line_count > self.max_lines_per_file:
                violations.append(
                    PolicyViolation(
                        path=f.path,
                        rule="max_lines_per_file",
                        detail=f"{line_count} linhas > {self.max_lines_per_file}",
                    )
                )

        return PolicyResult(allowed=not violations, violations=violations)

    @staticmethod
    def _matches_any_re(path: str, patterns: list[re.Pattern[str]]) -> bool:
        return any(p.match(path) for p in patterns)


def default_policy() -> AutonomyPolicy:
    """Política conservadora: só `app/strategies/*.py` é editável autonomamente."""
    return AutonomyPolicy(
        allowed_globs=[
            "app/strategies/*.py",
        ],
        forbidden_globs=[
            ".env",
            ".env.*",
            "**/.env",
            "**/.env.*",
            "app/core/**",
            "app/database/**",
            "app/exchanges/**",
            "app/events/**",
            "app/orchestration/**",
            "app/runtime/**",
            "app/github/**",
            "app/agents/**",
            "app/risk/**",
            "app/portfolio/**",
            "app/backtest/**",
            "migrations/**",
            "tests/**",
            ".github/**",
            "pyproject.toml",
            "docker-compose.yml",
            "Dockerfile",
            "scripts/**",
        ],
        forbidden_content_patterns=[
            "BEGIN PRIVATE KEY",
            "BEGIN RSA PRIVATE KEY",
            "BEGIN OPENSSH PRIVATE KEY",
            "BINANCE_API_SECRET",
            "API_SECRET=",
            "GITHUB_TOKEN=",
        ],
        max_files_per_pr=5,
        max_lines_per_file=500,
    )