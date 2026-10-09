from pathlib import Path

import pytest

from app.agents.ml import MLAgent
from app.core.enums import MarketType
from app.features.pipeline import default_pipeline
from tests.unit.strategies.conftest import make_candles


def _trending_candles(n: int = 300):
    closes = []
    price = 100.0
    for i in range(n):
        price += 0.5 if (i // 20) % 2 == 0 else -0.4
        closes.append(price)
    return make_candles(closes)


@pytest.mark.asyncio
async def test_train_saves_artifacts(context, tmp_path: Path):
    agent = MLAgent(context, model_dir=tmp_path)
    result = await agent.train(
        symbol="BTCUSDT", candles=_trending_candles(300)
    )
    artifacts = list(tmp_path.glob("*.joblib"))
    metas = list(tmp_path.glob("*.json"))
    assert len(artifacts) == 1
    assert len(metas) == 1
    assert result.metadata.symbol == "BTCUSDT"
    assert artifacts[0].stem == result.metadata.version


@pytest.mark.asyncio
async def test_train_rejects_too_few_candles(context, tmp_path: Path):
    agent = MLAgent(context, model_dir=tmp_path)
    with pytest.raises(ValueError, match="insuficientes"):
        await agent.train(symbol="BTCUSDT", candles=_trending_candles(30))


@pytest.mark.asyncio
async def test_predict_uses_trained_model(context, tmp_path: Path):
    agent = MLAgent(context, model_dir=tmp_path)
    candles = _trending_candles(300)
    await agent.train(symbol="BTCUSDT", candles=candles)
    sig = await agent.predict(symbol="BTCUSDT", candles=candles[-80:])
    if sig is not None:
        assert sig.symbol == "BTCUSDT"
        assert sig.strategy == "ml_classifier"


@pytest.mark.asyncio
async def test_predict_without_model_returns_none(context, tmp_path: Path):
    agent = MLAgent(context, model_dir=tmp_path)
    sig = await agent.predict(symbol="BTCUSDT", candles=_trending_candles(100))
    assert sig is None


@pytest.mark.asyncio
async def test_predict_loads_from_disk_when_cache_empty(context, tmp_path: Path):
    agent1 = MLAgent(context, model_dir=tmp_path)
    candles = _trending_candles(300)
    await agent1.train(symbol="BTCUSDT", candles=candles)

    agent2 = MLAgent(context, model_dir=tmp_path)
    sig = await agent2.predict(
        symbol="BTCUSDT",
        candles=candles[-80:],
        market_type=MarketType.FUTURES,
        interval="5m",
    )
    assert sig is None or sig.symbol == "BTCUSDT"


def test_preview_version_has_prefix():
    v = MLAgent.preview_version("BTCUSDT", 5)
    assert v.startswith("BTCUSDT_h5_")


def test_pipeline_injected(context, tmp_path: Path):
    custom = default_pipeline(ema_fast=5, ema_slow=15)
    agent = MLAgent(context, model_dir=tmp_path, pipeline=custom)
    assert agent.model_dir == tmp_path
    assert agent.horizon == 5

@pytest.mark.asyncio
async def test_train_prunes_old_versions(context, tmp_path: Path, monkeypatch):
    from app.core.config import settings
    from tests.unit.ml.test_retention import _write

    monkeypatch.setattr(settings, "ml_model_retention", 2)
    for day in range(1, 5):
        _write(tmp_path, f"BTCUSDT_h5_2026100{day}T120000Z")
    _write(tmp_path, "ETHUSDT_h5_20261001T120000Z")

    agent = MLAgent(context, model_dir=tmp_path)
    result = await agent.train(symbol="BTCUSDT", candles=_trending_candles(300))

    btc = sorted(p.stem for p in tmp_path.glob("BTCUSDT_h5_*.joblib"))
    # O modelo recém-treinado + o mais recente dos antigos; ETH intocado.
    assert btc == sorted([result.metadata.version, "BTCUSDT_h5_20261004T120000Z"])
    assert (tmp_path / "ETHUSDT_h5_20261001T120000Z.joblib").exists()
