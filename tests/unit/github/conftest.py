import pytest

from app.github.client import GitHubClient

BASE = "https://api.github.com"


@pytest.fixture
def client() -> GitHubClient:
    return GitHubClient(
        token="ghp_testtoken",
        repo="acme/ai-trading",
        base_url=BASE,
    )


@pytest.fixture
def base_url() -> str:
    return BASE