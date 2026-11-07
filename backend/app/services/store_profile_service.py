import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import StoreNotConfiguredError
from app.models import BusinessHour, Store
from app.repositories import store_repository
from app.schemas import BusinessHourItem, StoreInfoItem, StoreProfile

logger = logging.getLogger(__name__)

_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


async def get_store_profile(session: AsyncSession, store_id: uuid.UUID) -> StoreProfile:
    """매장 기본 정보와 영업시간을 출처(source_type, source_id)와 함께 반환한다.

    store_id는 호출하는 백엔드가 현재 매장(`store_service.get_default_store`)에서 넘긴다.
    DB에 값이 있는 항목만 content에 넣고, 행이 없는 요일은 만들어 내지 않는다.
    """
    store = await store_repository.find_store_by_id(session, store_id)
    if store is None:
        logger.error("Store %s does not exist.", store_id)
        raise StoreNotConfiguredError("매장 정보를 찾을 수 없습니다.")
    return StoreProfile(
        store=StoreInfoItem(source_id=store.id, content=_store_content(store)),
        business_hours=[
            BusinessHourItem(source_id=hour.id, content=_business_hour_content(hour))
            for hour in store.business_hours
        ],
    )


def _store_content(store: Store) -> str:
    fields = (
        ("Name", store.name),
        ("Description", store.description),
        ("Address", store.address),
        ("Phone", store.phone),
        ("Email", store.email),
        ("Website", store.website),
        ("Time zone", store.timezone),
    )
    return "\n".join(f"{label}: {value}" for label, value in fields if value)


def _business_hour_content(hour: BusinessHour) -> str:
    day = _DAY_NAMES[hour.day_of_week]
    # 시간이 둘 다 NULL이면 휴무다 (DB 제약이 한쪽만 NULL인 상태를 막는다).
    if hour.open_time is None or hour.close_time is None:
        return f"{day}: Closed"
    return f"{day}: {hour.open_time:%H:%M}-{hour.close_time:%H:%M}"
