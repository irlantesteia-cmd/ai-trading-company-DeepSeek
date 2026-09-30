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

Scripts auxiliares
scripts/diagnose_binance_auth.py — valida formato das credenciais sem vazar segredos

scripts/test_binance_auth.py — testa contra SPOT e FUTURES testnet; descobre qual aceita a chave

Estratégia de branches
main          ← sempre estável, só recebe merge de develop quando testado
develop       ← integração; PRs vêm de feature/* e fix/*
feature/*     ← novas funcionalidades
fix/*         ← correções
experiment/*  ← experimentos descartáveis
refactor/*    ← refatorações sem mudança de comportamento

Regra: nunca commitar direto em main. Todo trabalho novo vai em
feature/* ou fix/* e abre PR para develop.

Convenções
Commits
Conventional Commits:
feat: add market data service
fix: handle websocket reconnect
test: add risk engine tests
refactor: isolate binance adapter
docs: update architecture
perf: optimize feature calculation
Testes
pytest -q roda a suíte completa (233 testes)

ruff check . valida lint

CI (.github/workflows/ci.yml) roda ambos em cada push/PR para main e develop

Estado atual
Fase	Descrição	Status
0	Fundação (pyproject, Docker, CI, health)	✅
1	Domínio, contratos, event bus, ORM, Alembic	✅
2	Binance Adapter (REST + WebSocket + mappers)	✅
3	Agentes e Orquestração	✅
4	Risco e Portfólio	✅
5	Estratégias e Backtest	✅
6	ML e Features	✅
7	Operação 24/7 e Recuperação	✅
8	GitHub e Auto-Evolução Controlada	✅
Total: 233 testes · lint limpo · 13 agentes · CI verde em Windows e Linux.

text

---

## Rodar no terminal PowerShell

```powershell
git add README.md
git commit -m "docs: add architecture, setup and branch strategy"
git push origin develop
Abra https://github.com/irlantesteia-cmd/ai-trading-company-DeepSeek/actions — deve rodar o CI de novo em ~40 s, verde.

Próximo passo — escolher feature
Agora o ciclo é:

text
1. git checkout develop
2. git checkout -b feature/<nome>
3. implementa + ruff + pytest local
4. git push -u origin feature/<nome>
5. Abre PR: feature/<nome> → develop
6. CI verde → merge
7. Quando develop estiver estável → PR develop → main
Escolha uma feature:

#	Feature	Branch sugerida	Tamanho
1	Ligar EvolutionLoop no run_bot.py	feature/evolution-wire	Pequeno
2	Persistir OrderFilled no DB	feature/persist-fills	Médio
3	User Data Stream (listenKey)	feature/listen-key	Grande
4	Nova estratégia (breakout)	feature/breakout-strategy	Médio
5	Backfill de candles históricos	feature/backfill-candles	Médio
6	Dashboard web (FastAPI + HTMX)	feature/dashboard	Grande
Recomendação: começar pela #1 (EvolutionLoop) — é a menor, valida o ciclo completo de feature-branch → PR → merge, e liga a Fase 8 de verdade (o sistema passa a propor melhorias via PR automaticamente). Depois que essa passar, atacamos uma das médias.