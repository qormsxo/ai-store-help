# AI Store Help

소규모 매장의 고객 문의를 AI가 응대하고, 답할 수 없는 문의는 사장에게 넘긴다. 사장의 답변은 지식으로 쌓여 다음 문의에 다시 쓰인다.

Backend는 Python 3.14, FastAPI, SQLAlchemy 2.x, PostgreSQL 16. AI는 Gemini Function Calling. Frontend는 React, Vite, TypeScript.

## 실행

```bash
cp .env.example .env
docker compose up --build
docker compose exec backend python -m app.seed
```

seed 전에는 매장이 없어 `GET /api/store`가 503이다. seed는 데모 매장 1곳만 만들고, 같은 slug가 있으면 덮어쓰지 않는다.

- Frontend: http://localhost:5173
- API 문서: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

로컬 5432를 이미 쓰면 `.env`의 `POSTGRES_PORT`와 `DATABASE_URL` 포트를 같이 바꾼다. 컨테이너 안은 항상 5432다. 소스는 볼륨 마운트라 backend는 `--reload`, frontend는 HMR로 반영된다.

## 로컬 개발

DB만 Docker로 띄운다. `DATABASE_URL`은 `localhost`를 가리켜야 한다.

```bash
docker compose up -d db

cd backend
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload

cd frontend
npm install
npm run dev
```

백엔드는 기동 시 `alembic upgrade head`를 실행한다. 수동으로는 `backend/`에서 `alembic upgrade head`, `alembic revision --autogenerate -m "설명"`. 모델을 바꾸면 마이그레이션을 같이 만들고, 새 PostgreSQL enum은 `downgrade`에서 `DROP TYPE`까지 넣는다.

## 테스트

```bash
cd backend
pytest
```

DB 테스트는 `<DB이름>_test`를 쓰고 개발 데이터는 건드리지 않는다. 실제 Gemini 호출은 `@pytest.mark.live`라 기본 실행에서 빠진다. `pytest -m live`로만 돈다.

## 환경변수

`.env.example`을 복사해 `.env`를 만든다. `.env`는 커밋하지 않는다.

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | DB 계정과 이름 | `app` |
| `POSTGRES_PORT` | 호스트에 노출할 DB 포트 | `5432` |
| `DATABASE_URL` | 로컬 실행용 접속 문자열. Docker에서는 compose가 덮어씀 | `postgresql+asyncpg://app:app@localhost:5432/app` |
| `DEFAULT_STORE_SLUG` | 이 환경의 매장 slug | `oak-pine-demo` |
| `LLM_PROVIDER` | `mock`은 Gemini를 호출하지 않음. 실제 모델은 `gemini` | compose는 `mock` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | `gemini`일 때 필수. 없으면 Agent만 503 | 없음 |
| `GEMINI_MAX_OUTPUT_TOKENS` / `GEMINI_TIMEOUT` | 응답 최대 토큰, 다음 조각까지 제한(초) | `2048` / `60` |
| `APP_ENV` / `LOG_LEVEL` | 실행 환경, 로그 레벨 | `development` / `INFO` |
| `BACKEND_PORT` / `FRONTEND_PORT` | 호스트 포트 | `8000` / `5173` |

에러 응답은 `{"error": {"code", "message"}}` 한 형식이다.
