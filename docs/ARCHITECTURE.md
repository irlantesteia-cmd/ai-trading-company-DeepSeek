# Arquitetura e manual de operação

Como a AI Trading Company funciona, como ela se protege e o que fazer quando
algo dá errado. Para comandos de instalação e desenvolvimento, veja o
[README](../README.md).

> O bot opera na **Binance Demo Trading** (Futures USDⓈ-M). Nenhuma estratégia
> passou ainda pelo gate do laboratório (seção 5): uma execução no demo hoje é
> um **teste operacional**, não um teste de lucro.

---

## 1. Visão geral

O sistema imita uma "empresa de trading": cada agente tem um papel e eles
conversam por eventos dentro do mesmo processo (`app/events/bus.py`).

```
 Binance (REST + WebSocket)
        │
        ▼
 CandleStreamService ──CandleClosed──▶ AssetAgent (1 por ativo, com a estratégia do ativo)
                                              │
                                        SignalGenerated
                                              ▼
                                       TradingManager ──(SIGNAL_AUTO_EXECUTION_ENABLED=false: só registra)
                                              │
                    ┌─────────────────────────┼──────────────────────────┐
                    ▼                         ▼                          ▼
              RiskAgent (regras)     PortfolioAgent (tamanho)    ExecutionAgent (ordem + SL/TP)
                                                                         │
                                                                    OrderFilled
                                                                         ▼
                       TradeRecorder · RoundTripAgent · SignalRecorder · Auditor · QA
                                                (gravam no Postgres / verificam invariantes)

 Em paralelo: Reconciler + ReconcileMonitor (5 min) · TimeExitMonitor (1 min) ·
              User Data Stream (fills de SL/TP) · health checks · heartbeat
```

| Agente | Papel |
|---|---|
| `AssetAgent` | Um por ativo. A cada candle fechado roda a estratégia configurada em `STRATEGY_PER_SYMBOL` e publica o sinal. |
| `TradingManager` | Recebe o sinal e conduz o pipeline risco → tamanho → execução. Conta erros seguidos da corretora (R3). |
| `RiskAgent` | Aplica as regras de risco (seção 2) e cria as travas de perda. |
| `PortfolioAgent` | Calcula a quantidade a partir do risco por trade e da distância do stop. |
| `ExecutionAgent` | Envia a ordem, espera o fill e cria SL/TP ancorados no preço executado. |
| `TradeRecorder`, `RoundTripAgent`, `SignalRecorder` | Gravam fills, operações completas (entrada + saída) e sinais com a decisão. |
| `AuditorAgent`, `QAAgent` | Trilha de auditoria no log e checagem de invariantes (fill com preço e quantidade válidos). |
| `EngineeringAgent`, `ResearchAgent`, `MLAgent` | Saúde/alertas, hipóteses e o pipeline de ML (encerrado; seção 5). |

Dois detalhes que explicam comportamentos do sistema:

- O event bus roda os handlers **um de cada vez, na ordem de registro**. Por
  isso o `TradingManager` pode decidir sobre um sinal antes de o
  `SignalRecorder` gravá-lo; o gravador guarda essas decisões e as aplica
  quando o sinal chega.
- Um handler que falha é isolado (logado) e não derruba os outros.

---

## 2. Camadas de proteção

Da mais próxima da ordem para a mais ampla. Toda configuração fica no `.env`
(modelo em `.env.example`).

| # | Camada | O que faz | Configuração |
|---|---|---|---|
| 1 | **Regras de risco por sinal** | Rejeita o sinal, na ordem: trava ativa → confiança mínima → perda diária → perdas seguidas → exposição total → máx. posições → máx. na mesma direção → já há posição no ativo → grupo correlacionado. | `RISK_*` |
| 2 | **Tamanho da posição** | Arrisca uma fração do capital até o stop, limitada por um notional máximo por ativo. | `SIZING_RISK_PER_TRADE_PCT`, `RISK_MAX_POSITION_NOTIONAL` |
| 3 | **SL/TP na corretora** | Criados logo após o fill, **ancorados no preço executado** (não no preço do sinal). | `STOP_LOSS_ENABLED`, `TAKE_PROFIT_ENABLED` |
| 4 | **Stop que falha** | Se o stop-loss não puder ser criado, a posição é fechada a mercado na hora. | `CLOSE_ON_STOP_LOSS_FAILURE` |
| 5 | **Saída por tempo** | Fecha posições abertas há mais de N minutos. | `POSITION_MAX_HOLDING_MINUTES` |
| 6 | **Candles atrasados** | Candle que chega atrasado (volta de suspensão, boot) não gera sinal. | `CANDLE_MAX_EMIT_LAG_S` |
| 7 | **Travas por perda** (R2) | Perda diária ou perdas seguidas criam trava global. | `RISK_MAX_DAILY_LOSS(_PCT)`, `RISK_MAX_CONSECUTIVE_LOSSES` |
| 8 | **Travas operacionais** (R3) | Falhas de reconciliação, de API ou de proteção criam trava. | `HALT_*` |
| 9 | **Limpeza no boot** | Opcionalmente fecha posições e cancela SL/TP órfãos ao iniciar. | `CLOSE_POSITIONS_ON_BOOT`, `CANCEL_PROTECTIVE_ORDERS_ON_CLOSE` |

**Como a trava funciona:** fica gravada na tabela `trading_halts` e vale na
hora, sem reiniciar o bot. Ela bloqueia **só novas entradas**: posições
abertas seguem com SL, TP e saída por tempo. O bot **nunca** fecha posições ou
envia ordens por causa de uma trava. Travas automáticas só são liberadas por
uma pessoa (`resume`). Todas passam por um único ponto
(`app/runtime/halt_guard.py`), que evita duplicatas e dispara um único alerta
`TRADING HALTED` no log.

---

## 3. Manual de operação

### Ligar, acompanhar e desligar

```powershell
docker compose up -d postgres redis grafana
.\.venv\Scripts\alembic upgrade head
docker compose --profile bot up -d --build app      # liga o bot (opera na conta demo)
docker compose logs -f app                          # acompanha
docker compose --profile bot stop app               # desliga
```

- Grafana: http://127.0.0.1:3000, dashboard **Trading — visão geral**.
- O container reinicia sozinho até 5 vezes se o processo cair, mas **não**
  volta depois de reiniciar o PC ou o Docker: suba de novo com o comando acima.
- Para zerar todas as posições na hora: `.\.venv\Scripts\python scripts/close_all_positions.py`.

### Travas

```powershell
.\.venv\Scripts\python scripts/trading_control.py status
.\.venv\Scripts\python scripts/trading_control.py pause --reason "manutenção"
.\.venv\Scripts\python scripts/trading_control.py pause --symbol XRPUSDT --reason "spread alto"
.\.venv\Scripts\python scripts/trading_control.py resume --note "revisado: <o que foi conferido>"
.\.venv\Scripts\python scripts/trading_control.py resume --symbol SOLUSDT --note "..."
```

O `resume` libera **todas** as travas do escopo (global ou do ativo). Escreva
na nota o que foi conferido: ela fica no histórico do Grafana.

### O que fazer em cada trava

Em todos os casos, comece por `trading_control.py status` (mostra a regra e o
motivo) e pelo painel **Trava de trading** no Grafana.

| Regra | O que provavelmente aconteceu | O que conferir | Quando liberar |
|---|---|---|---|
| `manual` | Alguém pausou. | A nota da pausa. | Quando o motivo da pausa acabar. |
| `daily_loss` | O P&L do dia (realizado desde 00:00 UTC + não realizado) passou do limite. | Painel de resultado e os últimos round trips: perdas normais da estratégia ou algo anormal (slippage, ordem duplicada)? | Depois de 00:00 UTC. Antes disso o P&L do dia continua abaixo do limite e o próximo sinal cria a trava de novo. |
| `consecutive_losses` | N operações seguidas fecharam no prejuízo. | Se as perdas vieram de stops normais ou de algum erro; se o mercado mudou de regime. | Depois de revisar. O `resume` zera a contagem: só contam as perdas fechadas depois da última liberação. Se as próximas N operações também perderem, trava de novo. |
| `reconciliation` | Ordens no banco e na corretora não batem há 2 rodadas (≈ 10 min). | Tabela "Reconciliações com divergência não resolvida" no Grafana; as ordens abertas no site da Binance Demo. | Quando a divergência for explicada (ordem criada à mão, ordem antiga) e a reconciliação seguinte vier equilibrada. |
| `reconciliation_error` | A reconciliação não conseguiu rodar 3 vezes seguidas. | Log do bot (`reconcile.failed`): internet, Binance fora, Postgres parado (`docker compose ps`). | Quando a causa for resolvida e o painel "Minutos desde a última reconciliação" voltar a zerar. |
| `api_auth` | A Binance rejeitou a chave da API. | Rode `scripts/diagnose_binance_auth.py` (não mostra a chave). A chave pode ter sido revogada, expirada ou perdido permissão. | Depois de corrigir a chave no `.env` e reiniciar o bot. |
| `api_errors` | 3 erros seguidos da corretora ao executar sinais. | Log (`trading_manager.signal_processing_skipped`) para ver o código do erro: margem insuficiente, quantidade mínima, instabilidade da Binance. | Quando a causa for entendida e corrigida. Um erro de margem pede revisão do tamanho das posições. |
| `protection` (do ativo) | O stop-loss falhou e a posição foi zerada. | Log (`execution.flattening_after_stop_loss_failure`) e o código do erro da Binance. | Depois de entender o erro; trava só aquele ativo. |
| `protection_failure` | O stop-loss falhou **e** o fechamento também. **Pode haver posição sem proteção.** | **Na hora:** a posição no site da Binance. Zere com `close_all_positions.py` ou à mão. Depois, o log. | Só depois de confirmar que não há posição desprotegida. |
| `unprotected_position` | Há posição aberta sem stop-loss ativo há 2 verificações (≈ 10 min). | **Na hora:** a posição e as ordens condicionais na Binance. Coloque o stop à mão ou zere a posição. | Depois que toda posição aberta tiver stop, ou não houver posição. |

---

## 4. Dados e monitoramento

Banco Postgres (migrações em `migrations/versions/`):

| Tabela | Conteúdo |
|---|---|
| `signals` | Cada sinal gerado, com a decisão (aprovado / rejeitado / ignorado), o motivo e a ordem gerada. |
| `orders`, `order_fills` | Ciclo de vida de cada ordem, incluindo SL/TP (condicionais), e seus fills. |
| `trades` | Cada fill gravado a partir dos eventos de execução. |
| `round_trips` | Operação completa (entrada até saída), com P&L, taxas e motivo de fechamento (stop, alvo, tempo, manual). |
| `reconciliation_runs` | Cada reconciliação: ordens abertas dos dois lados, divergências e se ficou equilibrada. |
| `trading_halts` | Travas ativas e o histórico de quem liberou e com qual nota. |
| `market_metrics` | Coletor (fora do bot): open interest, long/short, volume taker, funding e livro de ofertas por ativo, a cada 5 min. Base para pesquisar estratégias com esses dados. |
| `candles`, `trading_pairs`, `futures_position_snapshots` | Dados de mercado, cadastro de pares e fotos de posições. |

O Grafana lê o banco com um usuário só de leitura (`grafana_ro`). Seções do
dashboard:

- **Trava de trading** (no topo): status atual e travas ativas/recentes.
- **Resultado**: P&L, win rate, taxas, P&L por ativo, motivo de fechamento e últimos round trips.
- **Ordens**: ordens por status, SL/TP abertos e ordens rejeitadas.
- **Dados de mercado**: último candle por ativo (se parou de atualizar, o stream caiu).
- **Sinais e reconciliação**: decisões por hora, motivos de rejeição, minutos desde a última reconciliação e divergências.
- **Mercado (coletor)**: por ativo, open interest e sua variação em 1 h/24 h, long/short, agressão taker, funding, desequilíbrio do livro e spread.

**Alertas** (`docker/grafana/provisioning/alerting/`), avaliados a cada
minuto e enviados ao Telegram quando `telegram.yml` está configurado (veja o
README):

| Alerta | Dispara quando |
|---|---|
| Trava de trading ativa | Qualquer trava ativa; uma mensagem por regra e escopo. `protection_failure` e `unprotected_position` saem como **URGENTE**. |
| Bot sem reconciliar | Nenhuma reconciliação há mais de 15 min: o bot caiu, travou ou perdeu acesso à Binance ou ao banco. |
| Perda do dia perto do limite | Prejuízo realizado desde 00:00 UTC abaixo de -60 USDT (a trava sai em cerca de -92). |

Cada alerta avisa de novo a cada 4 h enquanto continuar, e manda "RESOLVIDO"
quando acaba. Desligar o bot de propósito dispara "Bot sem reconciliar":
silencie no Grafana (Alerting → Silences).

Logs: JSON estruturado no stdout do container (`docker compose logs app`), em
5 arquivos de 20 MB.

---

## 5. Decisões e resultados

**Nenhuma estratégia vai ao bot ao vivo sem passar pelo gate do laboratório**
(`app/backtest/gate.py`): ≥ 300 trades, limite inferior do IC 95% do
resultado líquido > 0, ≥ 4 de 5 blocos de tempo positivos, profit factor > 1,1
e drawdown ≤ 20%. A configuração principal é declarada **antes** de rodar,
e parâmetros não são ajustados depois de ver o resultado.

| Data | Hipótese | Resultado |
|---|---|---|
| até 2026-10-09 | **ML** (OHLCV, cross-asset, taker buy, funding, premium) | Nenhuma passou de AUC 0,55 no walk-forward. **Programa encerrado.** |
| 2026-10-10 | **Momentum intradiário** (5m, 15m, 1h) | Reprovada: ganho bruto ≈ 0; o custo (≈ 0,14% por trade) vira prejuízo. |
| 2026-10-11 | **Rompimento de tendência** (Donchian, 4h e 1d, 12 ativos, desde 2021) | Reprovada: bruto +0,33% por trade, mas concentrado em um ano (nov/2024 a out/2025); drawdown 27%. |
| 2026-10-11 | **Momentum entre ativos** (compra os 3 mais fortes, vende os 3 mais fracos, semanal) | Reprovada: o lado vendido perde, o último ano é negativo e o drawdown chega a 46%. |

Os relatórios completos ficam em `artifacts/lab/` (fora do git). Os testes com
12 ativos usam moedas listadas hoje (viés de sobrevivência), o que tende a
**melhorar** o resultado; mesmo assim, todas foram reprovadas.

Consequência prática: o `.env` ainda pode apontar para `momentum`, que foi
reprovada. Rodar o bot serve para validar a **operação** (estabilidade,
travas, reconciliação, registro), não para medir lucro.
