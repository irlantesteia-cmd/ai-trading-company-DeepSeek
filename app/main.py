from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import setup_logging

setup_logging(settings.log_level)

app = FastAPI(title=settings.app_name)


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}