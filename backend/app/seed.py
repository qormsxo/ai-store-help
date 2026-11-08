"""데모 매장 seed 스크립트. 실행: python -m app.seed

마이그레이션 0001이 같은 매장과 주차 지식을 이미 넣는다. 이 스크립트는 그 행이 없을 때만 다시 만든다.
만드는 것: 가상의 한국 매장 2건, 각 영업시간 7건, 매장마다 다른 주차 지식 1건.
만들지 않는 것: Conversation, Message, SupportTicket.

여러 번 실행해도 안전하다. 같은 slug의 매장이나 같은 질문은 덮어쓰지 않는다.
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import time

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionFactory, engine
from app.models import BusinessHour, KnowledgeEntry, Store
from app.repositories.knowledge_repository import find_active_entry_by_question
from app.repositories.store_repository import find_store_by_slug

logger = logging.getLogger(__name__)

# 전화번호는 가상 번호 대역(555-01xx), 이메일과 웹사이트는 예약 도메인(.example)을 써서 실존 업체와 겹치지 않게 한다.
DEMO_STORE_NAME = "오크앤파인 커피"
DEMO_STORE_DESCRIPTION = "동네 커피집입니다. 소량으로 볶은 원두, 갓 구운 빵, 간단한 점심을 팝니다."
DEMO_STORE_ADDRESS = "서울특별시 마포구 연남로 12"
DEMO_STORE_PHONE = "02-555-0142"
DEMO_STORE_EMAIL = "hello@oakpine.example"
DEMO_STORE_WEBSITE = "https://www.oakpine.example"
DEMO_STORE_TIMEZONE = "Asia/Seoul"

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

Hours = tuple[tuple[int, time | None, time | None], ...]
_FLOWER_HOURS: Hours = (
    (0, None, None),
    (1, time(11, 0), time(20, 0)),
    (2, time(11, 0), time(20, 0)),
    (3, time(11, 0), time(20, 0)),
    (4, time(11, 0), time(20, 0)),
    (5, time(11, 0), time(20, 0)),
    (6, time(11, 0), time(20, 0)),
)
PARKING_QUESTION = "주차할 곳이 있나요?"


@dataclass(frozen=True)
class DemoProfile:
    slug: str
    name: str
    description: str
    address: str
    phone: str
    email: str
    website: str
    timezone: str
    hours: Hours
    parking_answer: str


OAK_PINE = DemoProfile(
    slug="oak-pine-demo",
    name=DEMO_STORE_NAME,
    description=DEMO_STORE_DESCRIPTION,
    address=DEMO_STORE_ADDRESS,
    phone=DEMO_STORE_PHONE,
    email=DEMO_STORE_EMAIL,
    website=DEMO_STORE_WEBSITE,
    timezone=DEMO_STORE_TIMEZONE,
    hours=DEMO_BUSINESS_HOURS,
    parking_answer="매장 전용 주차장은 없습니다. 연남로 공영주차장을 이용해 주세요.",
)
SEONGSU_FLOWER = DemoProfile(
    slug="seongsu-flower",
    name="성수 꽃집",
    description="성수동 골목의 꽃집입니다. 다발, 화분, 간단한 식물을 팝니다.",
    address="서울특별시 성동구 연무장길 45",
    phone="02-555-0198",
    email="hello@seongsu-flower.example",
    website="https://www.seongsu-flower.example",
    timezone="Asia/Seoul",
    hours=_FLOWER_HOURS,
    parking_answer="매장 뒤편에 무료 주차 2대가 있습니다.",
)
DEMO_PROFILES = (OAK_PINE, SEONGSU_FLOWER)


async def seed_profile(session: AsyncSession, profile: DemoProfile) -> bool:
    """프로필의 매장이 없으면 영업시간과 함께 만들고 True, 이미 있으면 건드리지 않고 False를 반환한다."""
    result = await session.execute(
        insert(Store)
        .values(
            slug=profile.slug,
            name=profile.name,
            description=profile.description,
            address=profile.address,
            phone=profile.phone,
            email=profile.email,
            website=profile.website,
            timezone=profile.timezone,
        )
        .on_conflict_do_nothing(index_elements=[Store.slug])
        .returning(Store.id)
    )
    store_id = result.scalar_one_or_none()
    if store_id is None:
        return False

    session.add_all(
        BusinessHour(store_id=store_id, day_of_week=day, open_time=opens, close_time=closes)
        for day, opens, closes in profile.hours
    )
    await session.flush()
    return True


async def seed_store(session: AsyncSession, slug: str) -> bool:
    """오크앤파인 프로필을 주어진 slug로 만든다. 이미 있으면 건드리지 않는다.

    ON CONFLICT DO NOTHING이라 동시에 두 번 실행해도 중복이 생기지 않는다.
    매장과 영업시간은 호출자의 같은 트랜잭션에서 만들어진다.
    """
    return await seed_profile(session, replace(OAK_PINE, slug=slug))


async def seed_parking_answer(session: AsyncSession, profile: DemoProfile) -> bool:
    """같은 주차 질문이 없으면 그 매장 답을 넣고 True, 매장이나 질문이 이미 있으면 False."""
    store = await find_store_by_slug(session, profile.slug)
    if store is None:
        return False
    existing = await find_active_entry_by_question(session, store.id, PARKING_QUESTION)
    if existing is not None:
        return False
    session.add(
        KnowledgeEntry(store_id=store.id, question=PARKING_QUESTION, answer=profile.parking_answer, is_active=True)
    )
    await session.flush()
    return True


async def seed_all(session: AsyncSession) -> None:
    """데모 매장 두 곳과, 매장마다 다른 주차 답을 만든다. 있는 행은 유지한다."""
    for profile in DEMO_PROFILES:
        await seed_profile(session, profile)
    for profile in DEMO_PROFILES:
        await seed_parking_answer(session, profile)


async def main() -> None:
    try:
        async with SessionFactory() as session:
            await seed_all(session)
            await session.commit()
    finally:
        await engine.dispose()
    logger.info("Demo stores are ready: %s", ", ".join(profile.slug for profile in DEMO_PROFILES))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
