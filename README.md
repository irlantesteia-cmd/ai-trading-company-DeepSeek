# AI Trading Company

Sistema de Day Trade autônomo, multi-IA, multi-ativo, operando na Binance.

## Princípios

- Desenvolvimento incremental e auditável.
- Nenhuma dependência de projetos anteriores.
- Arquitetura modular e testável.
- Autonomia controlada por limites, permissões e auditoria.

## Tecnologias

Python 3.12+, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, Redis, WebSockets,
httpx, pytest, Alembic, Docker, GitHub, NumPy, scikit-learn, joblib.

## Arquitetura

A plataforma é organizada como uma "empresa de trading com IA":

```
CEO / ORCHESTRATOR
│
├── TRADING MANAGER
├── RESEARCH AGENT
├── RISK OFFICER
├── PORTFOLIO MANAGER
├── EXECUTION AGENT
├── AUDITOR
├── QA AGENT
├── ENGINEERING AGENT
├── ML / DATA AGENT
└── ASSET AGENTS (BTC, ETH, SOL, XRP, ...)
```

## Como rodar

### Requisitos

- Python 3.12+
- Docker Desktop (para Postgres + Redis locais)

### Setup inicial

```powershell
# 1) Copiar e preencher variáveis de ambiente
Copy-Item .env.example .env
# Edite .env com BINANCE_API_KEY, BINANCE_API_SECRET, etc.

# 2) Subir Postgres + Redis
docker compose up -d postgres redis

# 3) Instalar dependências
python -m pip install -e ".[dev]"

# 4) Aplicar migrações
alembic upgrade head

# 5) Rodar o bot
python scripts/run_bot.py
```

### Rodar o bot em container

O serviço `app` fica no profile `bot`: um `docker compose up` comum **não** o
sobe, porque com `SIGNAL_AUTO_EXECUTION_ENABLED=true` o bot opera na conta.
O `.env` não entra na imagem; o compose o injeta em tempo de execução e
aponta `DATABASE_URL`/`REDIS_URL` para os serviços internos.

```powershell
docker compose up -d postgres redis
alembic upgrade head
docker compose --profile bot up -d --build app
docker compose logs -f app
docker compose --profile bot stop app
```

### Grafana

```powershell
docker compose up -d grafana
# http://127.0.0.1:3000 — dashboard "Trading — visão geral" (pasta Trading)
```

### Scripts auxiliares

- `scripts/diagnose_binance_auth.py` — valida formato das credenciais sem vazar segredos
- `scripts/test_binance_auth.py` — testa contra SPOT e FUTURES testnet; descobre qual aceita a chave

## Estratégia de branches

```
main          ← sempre estável, só recebe merge de develop quando testado
develop       ← integração; PRs vêm de feature/* e fix/*
feature/*     ← novas funcionalidades
fix/*         ← correções
experiment/*  ← experimentos descartáveis
refactor/*    ← refatorações sem mudança de comportamento
```

Regra: nunca commitar direto em main. Todo trabalho novo vai em
feature/* ou fix/* e abre PR para develop. Releases `develop` → `main` usam
**merge commit** (não squash), para o histórico dos dois branches não divergir.

## Convenções

### Commits

[Conventional Commits](https://www.conventionalcommits.org/), em inglês:

```
feat: add market data service
fix: handle websocket reconnect
test: add risk engine tests
refactor: isolate binance adapter
docs: update architecture
perf: optimize feature calculation
```

### Testes e qualidade

```powershell
pytest -q        # suíte completa
ruff check .     # lint
mypy             # tipos (configurado no pyproject para app/)
```

O CI (`.github/workflows/ci.yml`) roda os três em cada push e PR para `main` e `develop`.

## Estado atual

| Fase | Descrição | Status |
|---|---|---|
| 0 | Fundação (pyproject, Docker, CI, health) | ✅ |
| 1 | Domínio, contratos, event bus, ORM, Alembic | ✅ |
| 2 | Binance Adapter (REST + WebSocket + mappers) | ✅ |
| 3 | Agentes e Orquestração | ✅ |
| 4 | Risco e Portfólio | ✅ |
| 5 | Estratégias e Backtest | ✅ |
| 6 | ML e Features | ✅ |
| 7 | Operação 24/7 e Recuperação | ✅ |
| 8 | GitHub e Auto-Evolução Controlada | ✅ |

Depois disso:

- **Programa de ML encerrado** (2026-10-09). Nenhuma hipótese testada (OHLCV,
  cross-asset, taker buy, funding, premium) passou de AUC 0,55 no walk-forward.
  O código continua no repositório e os gates de deploy deixam o bot em
  standby.
- **Infra:** mypy no CI; persistência do ciclo de vida das ordens (`orders`);
  reconciliação de ordens condicionais (SL/TP); cancelamento de SL/TP órfãos
  no boot; retenção de modelos em `models/`; Grafana com dashboard de trading;
  imagem Docker do bot.
