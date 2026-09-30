import base64

import pytest
import respx
from httpx import Response

from app.github.client import GitHubAuthError, GitHubClient, GitHubNotFound
from tests.unit.github.conftest import BASE


@pytest.mark.asyncio
async def test_init_requires_token():
    with pytest.raises(GitHubAuthError):
        GitHubClient(token="", repo="a/b")


@pytest.mark.asyncio
async def test_init_rejects_bad_repo():
    from app.github.client import GitHubError

    with pytest.raises(GitHubError):
        GitHubClient(token="t", repo="noslash")


@pytest.mark.asyncio
async def test_get_ref_sha(client: GitHubClient):
    try:
        with respx.mock(base_url=BASE) as mock:
            mock.get("/repos/acme/ai-trading/git/ref/heads/main").mock(
                return_value=Response(200, json={"object": {"sha": "abc123"}})
            )
            sha = await client.get_ref_sha("main")
            assert sha == "abc123"
            request = mock.calls[0].request
            assert request.headers["Authorization"] == "Bearer ghp_testtoken"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_create_branch(client: GitHubClient):
    try:
        with respx.mock(base_url=BASE) as mock:
            route = mock.post("/repos/acme/ai-trading/git/refs").mock(
                return_value=Response(201, json={})
            )
            await client.create_branch("auto/feature/x", "deadbeef")
            sent = route.calls[0].request
            assert b"refs/heads/auto/feature/x" in sent.content
            assert b"deadbeef" in sent.content
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fetch_file_decodes_base64(client: GitHubClient):
    payload = "line1\nline2\n"
    encoded = base64.b64encode(payload.encode()).decode()
    try:
        with respx.mock(base_url=BASE) as mock:
            mock.get("/repos/acme/ai-trading/contents/app/strategies/x.py").mock(
                return_value=Response(
                    200, json={"content": encoded, "sha": "filesha"}
                )
            )
            result = await client.fetch_file("app/strategies/x.py", ref="main")
            assert result == (payload, "filesha")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fetch_file_returns_none_for_missing(client: GitHubClient):
    try:
        with respx.mock(base_url=BASE) as mock:
            mock.get(
                "/repos/acme/ai-trading/contents/app/strategies/new.py"
            ).mock(return_value=Response(404, json={"message": "Not Found"}))
            assert await client.fetch_file("app/strategies/new.py", ref="main") is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_put_file_includes_sha_when_provided(client: GitHubClient):
    try:
        with respx.mock(base_url=BASE) as mock:
            route = mock.put(
                "/repos/acme/ai-trading/contents/app/strategies/x.py"
            ).mock(return_value=Response(200, json={"commit": {"sha": "c"}}))
            await client.put_file(
                path="app/strategies/x.py",
                content="hello",
                message="auto: update",
                branch="auto/x",
                sha="prevsha",
            )
            sent = route.calls[0].request
            assert b"prevsha" in sent.content
            assert base64.b64encode(b"hello") in sent.content
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_put_file_omits_sha_when_new(client: GitHubClient):
    try:
        with respx.mock(base_url=BASE) as mock:
            route = mock.put(
                "/repos/acme/ai-trading/contents/app/strategies/new.py"
            ).mock(return_value=Response(201, json={}))
            await client.put_file(
                path="app/strategies/new.py",
                content="new",
                message="auto: add",
                branch="auto/x",
            )
            sent = route.calls[0].request
            assert b'"sha"' not in sent.content
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_create_pull_request(client: GitHubClient):
    try:
        with respx.mock(base_url=BASE) as mock:
            route = mock.post("/repos/acme/ai-trading/pulls").mock(
                return_value=Response(
                    201,
                    json={
                        "number": 42,
                        "html_url": "https://github.com/acme/ai-trading/pull/42",
                        "title": "auto: foo",
                        "draft": True,
                    },
                )
            )
            pr = await client.create_pull_request(
                title="auto: foo",
                body="body",
                head="auto/x",
                base="main",
                draft=True,
            )
            assert pr["number"] == 42
            sent = route.calls[0].request
            assert b'"draft": true' in sent.content or b'"draft":true' in sent.content
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_create_issue(client: GitHubClient):
    try:
        with respx.mock(base_url=BASE) as mock:
            route = mock.post("/repos/acme/ai-trading/issues").mock(
                return_value=Response(201, json={"number": 7})
            )
            data = await client.create_issue(
                title="Hypothesis", body="body", labels=["research"]
            )
            assert data["number"] == 7
            sent = route.calls[0].request
            assert b"research" in sent.content
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_not_found_raises(client: GitHubClient):
    try:
        with respx.mock(base_url=BASE) as mock:
            mock.get(
                "/repos/acme/ai-trading/git/ref/heads/nope"
            ).mock(return_value=Response(404, json={}))
            with pytest.raises(GitHubNotFound):
                await client.get_ref_sha("nope")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delete_branch_swallows_404(client: GitHubClient):
    try:
        with respx.mock(base_url=BASE) as mock:
            mock.delete(
                "/repos/acme/ai-trading/git/refs/heads/auto/gone"
            ).mock(return_value=Response(404, json={}))
            # Não deve explodir
            await client.delete_branch("auto/gone")
    finally:
        await client.close()