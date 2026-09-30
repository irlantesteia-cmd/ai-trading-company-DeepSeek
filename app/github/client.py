from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from app.core.exceptions import AITradingError

logger = logging.getLogger(__name__)


class GitHubError(AITradingError):
    ...


class GitHubAuthError(GitHubError):
    ...


class GitHubNotFound(GitHubError):
    ...


class GitHubClient:
    """Cliente mínimo para GitHub REST v3."""

    def __init__(
        self,
        *,
        token: str,
        repo: str,
        base_url: str = "https://api.github.com",
        timeout: float = 15.0,
    ) -> None:
        if not token:
            raise GitHubAuthError("github_token não configurado")
        if "/" not in repo:
            raise GitHubError(f"github_repo inválido: {repo!r} (esperado 'owner/name')")
        self._token = token
        self._repo = repo
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-trading-company/0.1",
            },
        )

    @property
    def repo(self) -> str:
        return self._repo

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ HTTP
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ) -> Any:
        response = await self._http.request(method, path, json=json)
        if response.status_code == 401:
            raise GitHubAuthError("token inválido ou sem permissão")
        if response.status_code == 404:
            raise GitHubNotFound(f"não encontrado: {method} {path}")
        if response.status_code >= 400:
            raise GitHubError(
                f"{response.status_code} em {method} {path}: {response.text[:200]}"
            )
        return response.json() if response.content else None

    # ------------------------------------------------------------- refs/branch
    async def get_ref_sha(self, branch: str) -> str:
        data = await self._request(
            "GET", f"/repos/{self._repo}/git/ref/heads/{branch}"
        )
        return str(data["object"]["sha"])

    async def create_branch(self, name: str, from_sha: str) -> None:
        await self._request(
            "POST",
            f"/repos/{self._repo}/git/refs",
            json={"ref": f"refs/heads/{name}", "sha": from_sha},
        )

    async def delete_branch(self, name: str) -> None:
        try:
            await self._request(
                "DELETE", f"/repos/{self._repo}/git/refs/heads/{name}"
            )
        except GitHubNotFound:
            return

    # ------------------------------------------------------------------ files
    async def fetch_file(
        self, path: str, *, ref: str
    ) -> tuple[str, str] | None:
        """Retorna (content_decode, sha) ou None se o arquivo não existir."""
        url = f"/repos/{self._repo}/contents/{path}"
        response = await self._http.get(url, params={"ref": ref})
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GitHubError(
                f"{response.status_code} em GET {url}: {response.text[:200]}"
            )
        data = response.json()
        content_b64 = data["content"].replace("\n", "")
        decoded = base64.b64decode(content_b64).decode("utf-8")
        return decoded, str(data["sha"])

    async def put_file(
        self,
        *,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha is not None:
            payload["sha"] = sha
        return await self._request(
            "PUT", f"/repos/{self._repo}/contents/{path}", json=payload
        )

    # ------------------------------------------------------------------ pulls
    async def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = False,
    ) -> dict:
        return await self._request(
            "POST",
            f"/repos/{self._repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": draft,
            },
        )

    # ----------------------------------------------------------------- issues
    async def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return await self._request(
            "POST", f"/repos/{self._repo}/issues", json=payload
        )

    async def close_issue(self, number: int) -> None:
        await self._request(
            "PATCH",
            f"/repos/{self._repo}/issues/{number}",
            json={"state": "closed"},
        )