from fastapi import APIRouter

from app.api.deps import CurrentStoreDep
from app.schemas import StoreResponse

router = APIRouter(tags=["store"])


@router.get("/store", response_model=StoreResponse)
async def get_store(store: CurrentStoreDep) -> StoreResponse:
    return StoreResponse.model_validate(store)
