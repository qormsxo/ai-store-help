from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas import HealthResponse
from app.services import health_service

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(session: SessionDep) -> HealthResponse:
    await health_service.check_database(session)
    return HealthResponse(status="ok", database="ok")
