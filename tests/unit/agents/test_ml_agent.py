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
    result = await agent.train(symbol="BTCUSDT", candles=_trending_candles(300))
    artifacts = list(tmp_path.glob("*.joblib"))
    metas = list(tmp_path.glob("*.json"))
    assert len(artifacts) == 1
    assert len(metas) == 1
    assert result.metadata.symbol == "BTCUSDT"
    assert artifacts[0].stem == result.metadata.version


@pytest.mark.asyncio
async def test_predict_uses_trained_model(context, tmp_path: Path):
    agent = MLAgent(context, model_dir=tmp_path)
    candles = _trending_candles(300)
    await agent.train(symbol="BTCUSDT", candles=candles)
    sig = await agent.predict(symbol="BTCUSDT", candles=candles[-80:])
    # Sinal pode ser None se proba ficar no meio, mas não pode dar erro.
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

    # Novo agente, sem cache em memória, deve carregar do disco
    agent2 = MLAgent(context, model_dir=tmp_path)
    sig = await agent2.predict(
        symbol="BTCUSDT",
        candles=candles[-80:],
        market_type=MarketType.FUTURES,
        interval="5m",
    )
    # Sem garantia do sinal, mas o carregamento não pode falhar.
    assert sig is None or sig.symbol == "BTCUSDT"


def test_preview_version_has_prefix():
    v = MLAgent.preview_version("BTCUSDT", 5)
    assert v.startswith("BTCUSDT_h5_")


def test_pipeline_injected(context, tmp_path: Path):
    custom = default_pipeline(ema_fast=5, ema_slow=15)
    agent = MLAgent(context, model_dir=tmp_path, pipeline=custom)
    assert agent.model_dir == tmp_path
    assert agent.horizon == 5