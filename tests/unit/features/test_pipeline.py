from decimal import Decimal

from app.features.pipeline import default_pipeline
from tests.unit.strategies.conftest import make_candles


def test_pipeline_shape_and_names():
    pipeline = default_pipeline()
    candles = make_candles([100.0 + i * 0.5 for i in range(60)])
    fm = pipeline.transform(candles)
    assert fm.values.shape[1] == len(fm.names)
    assert fm.values.shape[0] == len(fm.indices) == len(fm.times)
    assert fm.values.shape[0] > 0
    assert "return_1" in fm.names
    assert "rsi_14" in fm.names


def test_pipeline_empty_candles():
    pipeline = default_pipeline()
    fm = pipeline.transform([])
    assert fm.values.shape[0] == 0
    assert len(fm.names) > 0


def test_pipeline_no_lookahead():
    """Features no índice i não podem mudar se candles após i mudarem."""
    pipeline = default_pipeline()
    closes_a = [100.0 + (i % 3) for i in range(50)]
    closes_b = closes_a + [200.0, 50.0, 999.0]  # dados futuros diferentes

    candles_a = make_candles(closes_a)
    candles_b = make_candles(closes_b)

    fm_a = pipeline.transform(candles_a)
    fm_b = pipeline.transform(candles_b)

    # Linhas com mesmo índice (candles 0..49) devem ser idênticas
    a_by_index = {idx: row for idx, row in zip(fm_a.indices, fm_a.values)}
    for idx_b, row_b in zip(fm_b.indices, fm_b.values):
        if idx_b >= len(closes_a):
            break
        row_a = a_by_index[idx_b]
        assert (row_a == row_b).all(), f"leak detectado no índice {idx_b}"


def test_pipeline_taker_buy_is_opt_in():
    candles = make_candles([100.0 + i * 0.5 for i in range(60)])
    base = default_pipeline()
    micro = default_pipeline(include_taker_buy=True)
    assert "taker_buy_ratio" not in base.names
    assert base.names == micro.names[:-1]
    assert micro.names[-1] == "taker_buy_ratio"
    fm = micro.transform(candles)
    assert fm.values.shape[1] == 9
    assert fm.values.shape[0] > 0


def test_pipeline_taker_buy_drops_rows_without_flow():
    candles = make_candles([100.0 + i * 0.5 for i in range(60)])
    candles_missing = [
        c.model_copy(update={"taker_buy_base_volume": None}) for c in candles
    ]
    fm = default_pipeline(include_taker_buy=True).transform(candles_missing)
    assert fm.values.shape[0] == 0


def test_pipeline_taker_buy_uses_volume_ratio():
    candles = make_candles([100.0 + i * 0.5 for i in range(60)])
    candles = [
        c.model_copy(
            update={
                "volume": Decimal(10),
                "taker_buy_base_volume": Decimal(2),
            }
        )
        for c in candles
    ]
    fm = default_pipeline(include_taker_buy=True).transform(candles)
    idx = fm.names.index("taker_buy_ratio")
    assert fm.values[-1, idx] == 0.2


def test_pipeline_indices_are_sorted_and_unique():
    pipeline = default_pipeline()
    candles = make_candles([100.0 + i for i in range(80)])
    fm = pipeline.transform(candles)
    assert fm.indices == sorted(fm.indices)
    assert len(set(fm.indices)) == len(fm.indices)