from __future__ import annotations

from pydantic import Field

from app.domain.models.base import DomainModel


class FileChange(DomainModel):
    """Substituição integral de um arquivo (conteúdo final, não diff)."""

    path: str
    content: str


class ProposedChange(DomainModel):
    title: str
    body: str
    branch_name: str
    base_branch: str = "main"
    files: list[FileChange] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    draft: bool = False


class PolicyViolation(DomainModel):
    path: str
    rule: str
    detail: str


class PolicyResult(DomainModel):
    allowed: bool
    violations: list[PolicyViolation] = Field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(f"{v.rule}: {v.detail}" for v in self.violations)


class PullRequestResult(DomainModel):
    number: int
    url: str
    title: str
    head_branch: str
    draft: bool = False