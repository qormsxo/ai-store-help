from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import HealthResponse
from app.services import health_service

router = APIRouter(tags=["health"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/health", response_model=HealthResponse)
async def health(session: SessionDep) -> HealthResponse:
    await health_service.check_database(session)
    return HealthResponse(status="ok", database="ok")
