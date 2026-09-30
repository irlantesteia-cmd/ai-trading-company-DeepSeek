# AI Trading Company

Sistema de Day Trade autônomo, multi-IA, multi-ativo, operando na Binance.

## Princípios
- Desenvolvimento incremental e auditável.
- Nenhuma dependência de projetos anteriores.
- Arquitetura modular e testável.
- Autonomia controlada por limites, permissões e auditoria.

## Tecnologias
Python 3.12+, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, Redis, WebSockets, httpx, pytest, Alembic, Docker, GitHub.

## Estrutura
Ver diretórios em `app/` e `tests/`.

## Como rodar
1. Copie `.env.example` para `.env` e preencha.
2. `docker-compose up -d`
3. `pip install -e ".[dev]"`
4. `uvicorn app.main:app --reload`