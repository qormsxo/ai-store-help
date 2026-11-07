import httpx
from sqlalchemy.exc import OperationalError

from app.db import get_session
from app.main import app


class _OkSession:
    async def execute(self, *_: object, **__: object) -> None:
        return None


class _BrokenSession:
    async def execute(self, *_: object, **__: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


async def test_health_ok(client: httpx.AsyncClient) -> None:
    app.dependency_overrides[get_session] = lambda: _OkSession()

    res = await client.get("/api/health")

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "ok"}


async def test_health_returns_503_when_db_down(client: httpx.AsyncClient) -> None:
    app.dependency_overrides[get_session] = lambda: _BrokenSession()

    res = await client.get("/api/health")

    assert res.status_code == 503
    assert res.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


async def test_unknown_route_uses_common_error_format(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/nope")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
