import base64

import pytest
import respx
from httpx import Response

from app.core.exceptions import ConfigurationError
from app.github.client import GitHubClient, GitHubError
from app.github.policies import default_policy
from app.github.types import FileChange, ProposedChange
from app.github.workflows import GitHubWorkflows

BASE = "https://api.github.com"


def _workflows(enabled: bool = True) -> GitHubWorkflows:
    return GitHubWorkflows(
        client=GitHubClient(
            token="ghp_test", repo="acme/ai-trading", base_url=BASE
        ),
        policy=default_policy(),
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_propose_change_happy_path():
    wf = _workflows()
    try:
        with respx.mock(base_url=BASE) as mock:
            mock.get("/repos/acme/ai-trading/git/ref/heads/main").mock(
                return_value=Response(200, json={"object": {"sha": "base-sha"}})
            )
            mock.post("/repos/acme/ai-trading/git/refs").mock(
                return_value=Response(201, json={})
            )
            mock.get(
                "/repos/acme/ai-trading/contents/app/strategies/x.py"
            ).mock(
                return_value=Response(
                    200,
                    json={
                        "content": base64.b64encode(b"old\n").decode(),
                        "sha": "file-sha",
                    },
                )
            )
            mock.put(
                "/repos/acme/ai-trading/contents/app/strategies/x.py"
            ).mock(return_value=Response(200, json={}))
            mock.post("/repos/acme/ai-trading/pulls").mock(
                return_value=Response(
                    201,
                    json={
                        "number": 99,
                        "html_url": "https://github.com/acme/ai-trading/pull/99",
                        "title": "auto: bump momentum",
                        "draft": True,
                    },
                )
            )

            result = await wf.propose_change(
                ProposedChange(
                    title="auto: bump momentum",
                    body="Motivo: ...",
                    branch_name="auto/strategy/bump-momentum",
                    files=[
                        FileChange(
                            path="app/strategies/x.py",
                            content="new\n",
                        )
                    ],
                    draft=True,
                )
            )
            assert result.number == 99
            assert result.head_branch == "auto/strategy/bump-momentum"
    finally:
        await wf._client.close()


@pytest.mark.asyncio
async def test_propose_change_rolls_back_branch_on_failure():
    wf = _workflows()
    try:
        with respx.mock(base_url=BASE) as mock:
            mock.get("/repos/acme/ai-trading/git/ref/heads/main").mock(
                return_value=Response(200, json={"object": {"sha": "base-sha"}})
            )
            mock.post("/repos/acme/ai-trading/git/refs").mock(
                return_value=Response(201, json={})
            )
            # fetch_file: arquivo inexistente (404 → None)
            mock.get(
                "/repos/acme/ai-trading/contents/app/strategies/x.py"
            ).mock(return_value=Response(404, json={}))
            # put_file: erro 500 → GitHubError
            mock.put(
                "/repos/acme/ai-trading/contents/app/strategies/x.py"
            ).mock(return_value=Response(500, json={}))
            # rollback
            mock.delete(
                "/repos/acme/ai-trading/git/refs/heads/auto/x"
            ).mock(return_value=Response(204))

            with pytest.raises(GitHubError):
                await wf.propose_change(
                    ProposedChange(
                        title="x",
                        body="y",
                        branch_name="auto/x",
                        files=[
                            FileChange(
                                path="app/strategies/x.py", content="new"
                            )
                        ],
                    )
                )
            # Última chamada foi o DELETE de rollback
            assert mock.calls[-1].request.method == "DELETE"
    finally:
        await wf._client.close()


@pytest.mark.asyncio
async def test_propose_change_disabled_raises():
    wf = _workflows(enabled=False)
    try:
        with pytest.raises(ConfigurationError, match="autonomia"):
            await wf.propose_change(
                ProposedChange(
                    title="x",
                    body="y",
                    branch_name="auto/x",
                    files=[FileChange(path="app/strategies/x.py", content="new")],
                )
            )
    finally:
        await wf._client.close()


@pytest.mark.asyncio
async def test_propose_change_rejected_by_policy():
    wf = _workflows()
    try:
        with pytest.raises(ConfigurationError, match="política"):
            await wf.propose_change(
                ProposedChange(
                    title="x",
                    body="y",
                    branch_name="auto/x",
                    files=[FileChange(path=".env", content="A=1")],
                )
            )
    finally:
        await wf._client.close()


@pytest.mark.asyncio
async def test_open_issue():
    wf = _workflows()
    try:
        with respx.mock(base_url=BASE) as mock:
            mock.post("/repos/acme/ai-trading/issues").mock(
                return_value=Response(201, json={"number": 12})
            )
            n = await wf.open_issue(title="x", body="y", labels=["research"])
            assert n == 12
    finally:
        await wf._client.close()