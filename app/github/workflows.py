from __future__ import annotations

import logging

from app.core.exceptions import ConfigurationError
from app.github.client import GitHubClient
from app.github.policies import AutonomyPolicy
from app.github.types import ProposedChange, PullRequestResult

logger = logging.getLogger(__name__)


class GitHubWorkflows:
    """Fluxos de alto nível: valida política → cria branch → commita → abre PR."""

    def __init__(
        self,
        *,
        client: GitHubClient,
        policy: AutonomyPolicy,
        enabled: bool,
    ) -> None:
        self._client = client
        self._policy = policy
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def policy(self) -> AutonomyPolicy:
        return self._policy

    # ------------------------------------------------------------------ propose
    async def propose_change(self, change: ProposedChange) -> PullRequestResult:
        if not self._enabled:
            raise ConfigurationError(
                "autonomia do GitHub desligada (GITHUB_AUTONOMY_ENABLED=false)"
            )

        check = self._policy.check_change(change.files)
        if not check.allowed:
            raise ConfigurationError(
                f"mudança rejeitada pela política: {check.reason}"
            )

        base_sha = await self._client.get_ref_sha(change.base_branch)
        await self._client.create_branch(change.branch_name, base_sha)

        try:
            for f in change.files:
                existing = await self._client.fetch_file(
                    f.path, ref=change.base_branch
                )
                current_sha = existing[1] if existing else None
                await self._client.put_file(
                    path=f.path,
                    content=f.content,
                    message=f"auto: update {f.path}",
                    branch=change.branch_name,
                    sha=current_sha,
                )

            pr = await self._client.create_pull_request(
                title=change.title,
                body=change.body,
                head=change.branch_name,
                base=change.base_branch,
                draft=change.draft,
            )
        except Exception:
            # Rollback best-effort: apaga a branch para não deixar lixo
            await self._client.delete_branch(change.branch_name)
            raise

        result = PullRequestResult(
            number=int(pr["number"]),
            url=str(pr["html_url"]),
            title=str(pr["title"]),
            head_branch=change.branch_name,
            draft=bool(pr.get("draft", False)),
        )
        logger.info(
            "github.pr_created",
            extra={
                "number": result.number,
                "url": result.url,
                "branch": result.head_branch,
            },
        )
        return result

    # -------------------------------------------------------------------- issue
    async def open_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> int:
        data = await self._client.create_issue(
            title=title, body=body, labels=labels
        )
        number = int(data["number"])
        logger.info("github.issue_created", extra={"number": number})
        return number