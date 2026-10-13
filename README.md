# AI Trading Company

Sistema de Day Trade autônomo, multi-IA, multi-ativo, operando na Binance.

Como o sistema funciona, as camadas de proteção e o que fazer em cada trava:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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

Se o processo do bot cair com erro, o Docker o reinicia (até 5 vezes). Ele
**não** é religado ao iniciar o Docker ou depois de um reboot do PC: suba de
novo com o comando acima. Os logs do container giram em 5 arquivos de 20 MB.

### Grafana

```powershell
docker compose up -d grafana
# http://127.0.0.1:3000 — dashboard "Trading — visão geral" (pasta Trading)
```

Alertas por Telegram (trava ativa, bot sem reconciliar, perda do dia perto do
limite): crie um bot no @BotFather, mande uma mensagem a ele e pegue o chat id
em `https://api.telegram.org/bot<TOKEN>/getUpdates`. Depois:

```powershell
Copy-Item docker/grafana/provisioning/alerting/telegram.yml.example docker/grafana/provisioning/alerting/telegram.yml
# edite telegram.yml: token e chat id (o arquivo fica fora do git)
docker compose restart grafana
```

### Coletor de métricas de mercado

Grava a cada 5 min, para 12 ativos, dados de derivativos que a Binance só
guarda por ~30 dias (ou nem guarda): open interest, razões long/short, volume
taker, funding, preço de marcação e o livro de ofertas. Roda separado do bot,
só lê dados públicos (sem chave, sem ordens) e completa os últimos 30 dias ao
iniciar. Painéis na seção **Mercado (coletor)** do Grafana.

```powershell
alembic upgrade head                      # cria a tabela market_metrics
docker compose up -d --build collector
docker compose logs -f collector
```

### Trava de trading (intervenção humana)

Bloqueia **novas entradas** na hora, sem reiniciar o bot; posições abertas
seguem com SL, TP e saída por tempo (para zerar, use `close_all_positions.py`).
Perda diária ou perdas seguidas acima do limite criam uma trava global
automática, liberada **só** pelo `resume`. Falhas operacionais também travam:
reconciliações seguidas com divergência ou com erro, erros seguidos da
corretora ao executar sinais, chave rejeitada, stop-loss que falha e posição
aberta sem stop-loss (limites `HALT_*` no `.env`; ver `.env.example`).

```powershell
python scripts/trading_control.py status
python scripts/trading_control.py pause --reason "manutenção"
python scripts/trading_control.py pause --symbol XRPUSDT --reason "spread alto"
python scripts/trading_control.py resume --note "revisado"
```

### Laboratório de estratégias

Backtest com as regras do bot ao vivo e custos reais, walk-forward, intervalo
de confiança (bootstrap), resultado por regime de mercado e veredito do gate
de promoção (≥ 300 trades, limite inferior do IC 95% > 0, ≥ 4/5 blocos
positivos, profit factor > 1,1, drawdown ≤ 20%). Dados da API pública da
Binance com cache em `data/klines/`; relatório em `artifacts/lab/`.

```powershell
python scripts/strategy_lab.py --strategy momentum --intervals 5m,15m,1h --days 180 --hold-bars 6
python scripts/strategy_lab.py --strategy momentum --fee maker --param stop_atr_mult=2
```

Todo veredito traz **benchmarks** (não operar, comprar e segurar e timing
aleatório com o mesmo lado e duração) e o gate exige vencer o acaso, com o
p-valor corrigido pelo número de hipóteses já declaradas
(`docs/research/hypotheses.json`). Sensibilidade a parâmetros e registro de
uma hipótese oficial:

```powershell
python scripts/strategy_lab.py --strategy trend_breakout --intervals 4h,1d --days 1750 --trailing-stop --funding --sweep channel_period=15,20,30 --sweep stop_atr_mult=2.5,3,3.5
python scripts/strategy_lab.py --strategy trend_breakout --intervals 4h,1d --days 1750 --trailing-stop --funding --declared L6-minha-hipotese
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
