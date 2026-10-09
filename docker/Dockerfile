# Imagem do bot (scripts/run_bot.py). Segredos NÃO entram na imagem: o
# compose injeta o .env em tempo de execução (env_file). Ver .dockerignore.
FROM python:3.12.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependências primeiro (camada reaproveitada enquanto o pyproject não mudar).
COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN pip install .

COPY app/ app/
COPY scripts/ scripts/
COPY migrations/ migrations/
COPY alembic.ini ./
RUN pip install --no-deps . \
    && useradd --create-home --uid 1000 bot \
    && mkdir -p /app/models \
    && chown -R bot:bot /app/models

USER bot

CMD ["python", "scripts/run_bot.py"]
