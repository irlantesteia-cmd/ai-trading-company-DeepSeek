from app.github.policies import AutonomyPolicy, default_policy
from app.github.types import FileChange


def _policy() -> AutonomyPolicy:
    return default_policy()


def test_allows_simple_strategy_file():
    result = _policy().check_change(
        [FileChange(path="app/strategies/momentum.py", content="x = 1\n")]
    )
    assert result.allowed is True


def test_rejects_empty_change():
    result = _policy().check_change([])
    assert result.allowed is False
    assert result.violations[0].rule == "empty_change"


def test_rejects_path_outside_allow_list():
    result = _policy().check_change(
        [FileChange(path="app/agents/risk.py", content="x")]
    )
    assert result.allowed is False
    assert any(v.rule == "not_allowed" for v in result.violations)


def test_rejects_env_file():
    result = _policy().check_change([FileChange(path=".env", content="A=1")])
    assert result.allowed is False


def test_rejects_strategy_subdirectory():
    # `*` NÃO cruza `/`: app/strategies/*.py não casa com subdiretórios.
    result = _policy().check_change(
        [FileChange(path="app/strategies/sub/file.py", content="x")]
    )
    assert result.allowed is False
    assert any(v.rule == "not_allowed" for v in result.violations)


def test_single_star_does_not_cross_slash():
    # Regressão direta: `*` não pode casar com `/` em glob path-aware.
    policy = AutonomyPolicy(
        allowed_globs=["app/strategies/*.py"],
        forbidden_globs=[],
        forbidden_content_patterns=[],
    )
    assert policy.check_change(
        [FileChange(path="app/strategies/a.py", content="")]
    ).allowed
    assert not policy.check_change(
        [FileChange(path="app/strategies/a/b.py", content="")]
    ).allowed
    assert not policy.check_change(
        [FileChange(path="app/strategies/a/b/c.py", content="")]
    ).allowed


def test_double_star_crosses_slash():
    policy = AutonomyPolicy(
        allowed_globs=["app/strategies/**/*.py"],
        forbidden_globs=[],
        forbidden_content_patterns=[],
    )
    assert policy.check_change(
        [FileChange(path="app/strategies/deep/nested/file.py", content="")]
    ).allowed


def test_double_star_slash_matches_zero_dirs():
    # `**/` casa com zero ou mais diretórios: `.env` no root e em subdir.
    policy = AutonomyPolicy(
        allowed_globs=["**/*.py"],
        forbidden_globs=[],
        forbidden_content_patterns=[],
    )
    assert policy.check_change(
        [FileChange(path="top.py", content="")]
    ).allowed
    assert policy.check_change(
        [FileChange(path="a/b/c.py", content="")]
    ).allowed


def test_rejects_secret_content():
    result = _policy().check_change(
        [
            FileChange(
                path="app/strategies/x.py",
                content="API_SECRET=abcdef\n",
            )
        ]
    )
    assert result.allowed is False
    assert any(v.rule == "forbidden_content" for v in result.violations)


def test_rejects_pem_content():
    result = _policy().check_change(
        [
            FileChange(
                path="app/strategies/x.py",
                content="-----BEGIN PRIVATE KEY-----\n",
            )
        ]
    )
    assert result.allowed is False


def test_rejects_too_many_files():
    files = [
        FileChange(path=f"app/strategies/s{i}.py", content="x") for i in range(10)
    ]
    result = _policy().check_change(files)
    assert result.allowed is False
    assert any(v.rule == "max_files_per_pr" for v in result.violations)


def test_rejects_file_too_long():
    huge = "x = 1\n" * 600
    result = _policy().check_change(
        [FileChange(path="app/strategies/x.py", content=huge)]
    )
    assert result.allowed is False
    assert any(v.rule == "max_lines_per_file" for v in result.violations)


def test_custom_policy_allows_other_paths():
    policy = AutonomyPolicy(
        allowed_globs=["docs/*.md"],
        forbidden_globs=[".env"],
        forbidden_content_patterns=[],
        max_files_per_pr=3,
        max_lines_per_file=1000,
    )
    assert policy.check_change(
        [FileChange(path="docs/arch.md", content="# arch\n")]
    ).allowed
    assert not policy.check_change(
        [FileChange(path="app/strategies/x.py", content="x")]
    ).allowed


def test_default_policy_block_list_is_strict():
    policy = default_policy()
    for path in (
        "app/core/config.py",
        "app/database/base.py",
        "app/exchanges/binance/client.py",
        "migrations/versions/0001.py",
        "tests/unit/foo.py",
        ".github/workflows/ci.yml",
        "pyproject.toml",
        "scripts/run_bot.py",
    ):
        result = policy.check_change([FileChange(path=path, content="x")])
        assert not result.allowed, f"deveria bloquear {path}"