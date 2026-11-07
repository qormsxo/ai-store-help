"""데모 매장 seed 스크립트. 실행: python -m app.seed

만드는 것: 가상의 미국 매장 1건 + 요일별 영업시간 7건.
만들지 않는 것: KnowledgeEntry, Conversation, Message, SupportTicket (지식 DB는 빈 상태로 시작한다).

여러 번 실행해도 안전하다. 같은 slug의 매장이 이미 있으면 아무것도 바꾸지 않는다.
"""

import asyncio
import logging
from datetime import time

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionFactory, engine
from app.models import BusinessHour, Store

logger = logging.getLogger(__name__)

# 전화번호는 가상 번호 대역(555-01xx), 이메일과 웹사이트는 예약 도메인(.example)을 써서 실존 업체와 겹치지 않게 한다.
DEMO_STORE_NAME = "Oak & Pine Coffee Roasters"
DEMO_STORE_DESCRIPTION = (
    "Neighborhood coffee shop and bakery serving small-batch roasted coffee, fresh pastries, and light lunches."
)
DEMO_STORE_ADDRESS = "1427 Oak Street, Portland, OR 97205"
DEMO_STORE_PHONE = "+1 (503) 555-0142"
DEMO_STORE_EMAIL = "hello@oakpine.example"
DEMO_STORE_WEBSITE = "https://www.oakpine.example"
DEMO_STORE_TIMEZONE = "America/Los_Angeles"

# (요일 0=월요일, 오픈, 마감). 시간이 None이면 휴무.
DEMO_BUSINESS_HOURS: tuple[tuple[int, time | None, time | None], ...] = (
    (0, time(7, 0), time(18, 0)),
    (1, time(7, 0), time(18, 0)),
    (2, time(7, 0), time(18, 0)),
    (3, time(7, 0), time(18, 0)),
    (4, time(7, 0), time(18, 0)),
    (5, time(8, 0), time(17, 0)),
    (6, None, None),
)


async def seed_store(session: AsyncSession, slug: str) -> bool:
    """slug 매장이 없으면 영업시간과 함께 만들고 True, 이미 있으면 건드리지 않고 False를 반환한다.

    ON CONFLICT DO NOTHING이라 동시에 두 번 실행해도 중복이 생기지 않는다.
    매장과 영업시간은 호출자의 같은 트랜잭션에서 만들어진다.
    """
    result = await session.execute(
        insert(Store)
        .values(
            slug=slug,
            name=DEMO_STORE_NAME,
            description=DEMO_STORE_DESCRIPTION,
            address=DEMO_STORE_ADDRESS,
            phone=DEMO_STORE_PHONE,
            email=DEMO_STORE_EMAIL,
            website=DEMO_STORE_WEBSITE,
            timezone=DEMO_STORE_TIMEZONE,
        )
        .on_conflict_do_nothing(index_elements=[Store.slug])
        .returning(Store.id)
    )
    store_id = result.scalar_one_or_none()
    if store_id is None:
        return False

    session.add_all(
        BusinessHour(store_id=store_id, day_of_week=day, open_time=opens, close_time=closes)
        for day, opens, closes in DEMO_BUSINESS_HOURS
    )
    await session.flush()
    return True


async def main() -> None:
    slug = get_settings().default_store_slug
    try:
        async with SessionFactory() as session:
            created = await seed_store(session, slug)
            await session.commit()
    finally:
        await engine.dispose()

    if created:
        logger.info("Created store %r with %d business hours.", slug, len(DEMO_BUSINESS_HOURS))
    else:
        logger.info("Store %r already exists. Nothing was changed.", slug)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
