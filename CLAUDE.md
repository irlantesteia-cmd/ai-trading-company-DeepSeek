# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Autonomous multi-agent crypto day-trading bot ("AI Trading Company") on **Binance Demo Trading**
(USDⓈ-M Futures at `demo-fapi.binance.com`; never `testnet.binancefuture.com`). Windows + PowerShell
host, Python 3.12 venv in `.venv` (`.venv-1` is legacy — don't use it).

## Commands

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
docker compose up -d postgres redis          # local DB/cache (grafana: docker compose up -d grafana)
.\.venv\Scripts\alembic upgrade head         # migrations live in migrations/versions (000N_*.py)

.\.venv\Scripts\pytest -q                                    # full suite (no DB/network needed)
.\.venv\Scripts\pytest tests/unit/backtest/test_lab.py -q    # one file
.\.venv\Scripts\pytest tests/unit/backtest/test_lab.py::test_name -q
.\.venv\Scripts\ruff check .
.\.venv\Scripts\mypy                         # checks app/ only (configured in pyproject)
```

CI (`.github/workflows/ci.yml`) runs `pytest`, `ruff check .` and `mypy` on every push/PR to
`main` and `develop`. All three must be clean; fix ruff findings instead of adding `noqa`.

Operational scripts (`scripts/`): `run_bot.py` (entry point), `trading_control.py status|pause|resume`
(trading halt), `close_all_positions.py`, `strategy_lab.py` (backtest lab), `diagnose_binance_auth.py`
(validates credentials without printing them). The bot in Docker runs behind the `bot` compose
profile (`docker compose --profile bot up -d --build app`) so a plain `docker compose up` never trades.

## Working rules (from the project owner)

- Explanations to the user in **Portuguese**; code, identifiers and commit messages in **English**;
  PR bodies in Portuguese. When giving the user shell instructions, use PowerShell.
- Present a 3–5 bullet plan before coding and wait for approval; small patches, one phase per PR.
- Branches: `feature/*`, `fix/*`, `refactor/*`, `experiment/*` → PR → **squash** into `develop`.
  Releases `develop` → `main` use a **merge commit** (never squash — squash made the branches
  look diverged once). Never commit directly to `main`. Conventional Commits (`feat(scope): ...`).
- Never read out or commit `.env`; edit it only by targeted line changes. Do not start the trading
  bot or place orders — the user does that.
- Hypothesis → minimal implementation → measurement → decision; accept robust negative results and
  never promise a strategy "will work". Any new ML measurement needs ≥10k candles (`n_train ≥ 5000`).
  The ML program is closed (no hypothesis beat walk-forward AUC 0.55); don't propose new ML
  features unless asked.

## Architecture

Hexagonal layout under `app/`: `domain/models` (frozen Pydantic contracts: Signal, Order,
OrderIntent, Position, …), `exchanges/base` (ports) + `exchanges/binance` (REST client, adapter,
mappers, WebSocket, user data stream), `database/` (async SQLAlchemy models + repositories),
`agents/`, `strategies/`, `risk/`, `portfolio/`, `runtime/`, `market/`, `backtest/`.
Money is always `Decimal` (construct from strings, never `Decimal(0.1)`); indicators/ML use `float`.
`app/core/indicators.py` holds shared indicators; `tests/unit/test_import_cycles.py` cold-imports
every module, so keep imports acyclic (core/domain must not import upward).

### Live pipeline (`scripts/run_bot.py`)

`run_bot.py` wires everything: `BinanceAdapter` (orders wrapped by `RecordingOrderProvider` so every
order is persisted), an `AgentContext` (event bus, exchange, settings, session factory, registry),
agents registered in an `AgentRegistry`, plus runtime services (`Reconciler`, `TimeExitMonitor`,
heartbeat, health checks, user-stream handler) managed by `ApplicationLifecycle`.

Flow: `CandleStreamService` publishes `CandleClosed` → `AssetAgent` (one per symbol, strategy chosen by
`STRATEGY_PER_SYMBOL` via `strategies/factory.make_strategy`) publishes `SignalGenerated` →
`TradingManager` (only if `SIGNAL_AUTO_EXECUTION_ENABLED`) loads `PortfolioState`
(`portfolio/state_loader.py`), then calls Risk → Portfolio (sizing) → Execution **directly through
the registry** (not via events), publishing `SignalApproved`/`SignalRejected`/`OrderIntentCreated`/
`OrderFilled` along the way. Recorder agents (`SignalRecorderAgent`, `TradeRecorderAgent`,
`RoundTripAgent`) and `AuditorAgent`/`QAAgent` consume those events.

`EventBus` (`app/events/bus.py`) is in-process, dispatches by exact event type, and runs handlers
**sequentially** in subscription order with isolated failures. Consequence: `TradingManager` can
decide on a signal before `SignalRecorderAgent` has inserted it — the recorder keeps a pending map
for decisions that arrive first. Preserve registration order in `run_bot.py`.

### Risk and safety invariants

- `RiskEngine` (`app/risk/engine.py`) evaluates coded rules in order; rule 0 is the trading halt
  (`trading_halts` table). `daily_loss` and `consecutive_losses` breaches create an automatic global
  halt that is released **only** manually (`trading_control.py resume`). Daily P&L = realized since
  00:00 UTC + unrealized.
- Every automatic halt goes through `app/runtime/halt_guard.py` (`HaltGuard.trip`, deduplicated per
  scope/symbol/rule). Operational rules (R3): `reconciliation`, `reconciliation_error`,
  `unprotected_position` (from `ReconcileMonitor`), `api_errors`/`api_auth` (`TradingManager`),
  `protection`/`protection_failure` (`ExecutionAgent`). Thresholds are `HALT_*` settings; halts never
  touch the exchange, they only block new entries.
- Execution anchors SL/TP to the actual fill price; if the stop-loss cannot be placed, the position
  is flattened (`CLOSE_ON_STOP_LOSS_FAILURE`). Stale candles (`CANDLE_MAX_EMIT_LAG_S`) are skipped.
- Binance specifics: conditional orders (STOP/TAKE_PROFIT_MARKET) go through `/fapi/v1/algoOrder`
  (statuses NEW/CANCELED/EXPIRED/FINISHED; FINISHED = triggered), not `/fapi/v1/order`; never send
  `reduceOnly` with `positionSide`; never `stopPrice` on MARKET; HMAC signs the exact urlencoded
  query, so don't let httpx re-serialize signed params. Time sync re-runs on `-1021`/`-1022`.
- `Reconciler` (`app/runtime/recovery.py`) syncs DB orders with the exchange; boot cleanup cancels
  orphan SL/TP on symbols without a position; `TimeExitMonitor` closes positions older than
  `POSITION_MAX_HOLDING_MINUTES` (client order id prefix `tx-`).

The operator-facing runbook (Portuguese) is `docs/ARCHITECTURE.md`; keep its halt table in sync when
adding or changing a halt rule. All behaviour toggles live in `app/core/config.py` (`Settings`, pydantic-settings, loaded from `.env`;
see `.env.example`).

### Strategy lab (`app/backtest/`)

Research tool, separate from live trading. `data.py` loads public Binance klines/funding
(`fapi.binance.com`) with a gzip JSON cache in `data/klines/` (the in-progress candle is never
cached). `engine.py` (`BacktestEngine`) replays the live rules bar by bar: entries fill on the next
bar's open with SL/TP anchored to the fill, stop beats target on the same bar, optional time exit,
trailing stop, real funding (long pays / short receives), notional cap, and ruin below 1% equity.
`regime.py` labels bars causally; `stats.py` does bootstrap CIs and walk-forward folds;
`gate.py` is the promotion gate (≥300 trades, CI95 lower bound > 0, ≥4/5 positive folds, PF > 1.1,
max DD ≤ 20%); `lab.py` + `scripts/strategy_lab.py` run strategy × symbols × intervals and write
reports to `artifacts/lab/`. A strategy reaches the live bot only after passing the gate;
`trend_breakout` is lab-only for now. Results so far: `momentum` rejected (≈0 gross edge),
`trend_breakout` rejected (gross edge concentrated in one year).

Portfolio strategies (`xs_momentum`, cross-sectional long/short on daily bars) run through
`portfolio.py` + `portfolio_lab.py` instead of `BacktestEngine`: the statistical unit is the
rebalance period (positions of one period are correlated), and the gate's sample criterion counts
positions plus a minimum number of periods.

```powershell
.\.venv\Scripts\python scripts/strategy_lab.py --strategy momentum --intervals 5m,15m,1h --days 180 --hold-bars 6
.\.venv\Scripts\python scripts/strategy_lab.py --strategy trend_breakout --intervals 4h,1d --days 1750 --trailing-stop --funding
```

### Market data collector

`scripts/run_collector.py` (compose service `collector`, no `.env`, `restart: unless-stopped`) runs
`app/market/collector.py` independently of the bot: every 5 min it stores public derivatives data
(open interest, long/short ratios, taker volume, premium/funding, top-20 book) for
`COLLECTOR_SYMBOLS` into `market_metrics` (one row per symbol and 5-min block; sources merge
via a non-null-wins upsert) and backfills up to ~30 days on start, since Binance keeps no longer
history. It is the data source for research on non-price signals.

### Data and tests

Postgres tables include `orders`, `order_fills`, `trades`, `round_trips`, `signals`, `candles`,
`reconciliation_runs`, `trading_halts`; Grafana reads them through the read-only role `grafana_ro`
(dashboard JSON in `docker/grafana/`). Unit tests never touch a real DB or the network: repository
tests compile SQL with the Postgres dialect, exchange calls use `AsyncMock`/`httpx.MockTransport`/
`respx`, and `tests/unit/conftest.py` provides `event_bus`, `fake_exchange` and an `AgentContext`.
pytest runs with `asyncio_mode = "auto"` (plain `async def test_...`).
