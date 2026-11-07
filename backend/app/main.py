import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, store
from app.config import get_settings
from app.db import engine
from app.errors import register_exception_handlers

settings = get_settings()
logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


app = FastAPI(title="AI Store Help", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(health.router, prefix="/api")
app.include_router(store.router, prefix="/api")
