# AI Store Help

소규모 매장의 고객 문의를 AI가 응대하고, 답할 수 없는 문의는 사장에게 넘긴다. 사장의 답변은 지식으로 쌓여 다음 문의에 다시 쓰인다.

Backend는 Python 3.14, FastAPI, SQLAlchemy 2.x, PostgreSQL 16. AI는 Gemini Function Calling. Frontend는 React, Vite, TypeScript.

둘 다 저장소 루트에서 `.env.example`을 `.env`로 복사한 뒤 `GEMINI_API_KEY`를 실제 키로 바꾼다. `your_api_key` 그대로면 서버는 뜨고 손님 답만 503이다. `.env`의 `LLM_PROVIDER=gemini`이면 로컬과 도커 모두 실제 Gemini를 호출한다. `mock`은 이 값이 비어 있을 때만 도커의 기본값이다.

`alembic upgrade head`가 스키마와 데모 매장 2곳, 매장마다 다른 주차 지식을 넣는다. 그 전에 `GET /api/store`는 503이다. `python -m app.seed`는 같은 데이터를 다시 넣으며, 같은 slug나 같은 질문은 덮어쓰지 않는다.

- 화면: http://localhost:5173
- API 문서: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

`/`는 `DEFAULT_STORE_SLUG`(`oak-pine-demo`)로 간다. 다른 매장은 http://localhost:5173/seongsu-flower/chat 이다.

## 전부 도커로 올리기

컨테이너 안 DB 주소는 compose가 `db:5432`로 덮어쓴다. `.env`의 `DATABASE_URL`은 로컬용이라 도커 백엔드는 쓰지 않는다. 호스트로 여는 DB 포트만 `POSTGRES_PORT`다. 컨테이너 안은 항상 5432다.

도커 백엔드는 켜질 때 `alembic upgrade head`를 해서 스키마와 데모 매장을 만든다. 소스는 볼륨 마운트라 backend는 `--reload`, frontend는 HMR로 반영된다.

```bash
cp .env.example .env
docker compose up --build
```

## 로컬로 실행하기

DB만 Docker로 띄운다. 백엔드와 프론트는 각자 실행한다. `DATABASE_URL`은 `localhost`를 가리켜야 한다. PC에 5432가 이미 있으면 `.env`의 `POSTGRES_PORT`와 `DATABASE_URL` 포트를 같이 바꾼다.

로컬 `uvicorn`은 마이그레이션을 실행하지 않는다. `backend/`에서 가상환경을 켠 뒤 `alembic upgrade head`를 먼저 한다. 모델을 바꾸면 마이그레이션을 같이 만들고, 새 PostgreSQL enum은 `downgrade`에서 `DROP TYPE`까지 넣는다. 수동 생성은 `alembic revision --autogenerate -m "설명"`이다.

```bash
cp .env.example .env
docker compose up -d db

cd backend
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

다른 터미널에서 프론트를 띄운다. 브라우저는 `/api`로 호출하고, Vite가 `http://localhost:8000`으로 프록시한다.

```bash
cd frontend
npm install
npm run dev
```

## 데모 주소

`http://localhost:5173/` 는 기본 매장 `oak-pine-demo` 채팅으로 보낸다. 매장을 고르는 화면은 없다. 다른 매장은 주소의 slug가 고른다.

**오크앤파인 커피** (`oak-pine-demo`), 서울특별시 마포구 연남로 12, 일요일 휴무

- 손님: http://localhost:5173/oak-pine-demo/chat
- 사장: http://localhost:5173/oak-pine-demo/owner
- 주차 질문의 답: 매장 전용 주차장은 없고, 연남로 공영주차장을 이용한다

**성수 꽃집** (`seongsu-flower`), 서울특별시 성동구 연무장길 45, 월요일 휴무

- 손님: http://localhost:5173/seongsu-flower/chat
- 사장: http://localhost:5173/seongsu-flower/owner
- 주차 질문의 답: 매장 뒤편에 무료 주차 2대

손님은 로그인 없이 화면에서 고른다. 같은 사람이 두 매장에 들어가도 대화는 섞이지 않는다.

- 손님 1, `demo-emma-walsh`
- 손님 2, `demo-noah-bennett`
- 손님 3, `demo-priya-shah`

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
| `DEFAULT_STORE_SLUG` | 주소에 slug가 없을 때 여는 매장 | `oak-pine-demo` |
| `LLM_PROVIDER` | `gemini`는 실제 호출. `mock`은 호출하지 않음 | `.env`는 `gemini`. 값이 없으면 도커만 `mock` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | `gemini`일 때 필수. 없으면 Agent만 503 | 없음 |
| `GEMINI_MAX_OUTPUT_TOKENS` / `GEMINI_TIMEOUT` | 응답 최대 토큰, 다음 조각까지 제한(초) | `2048` / `60` |
| `APP_ENV` / `LOG_LEVEL` | 실행 환경, 로그 레벨 | `development` / `INFO` |
| `BACKEND_PORT` / `FRONTEND_PORT` | 호스트 포트 | `8000` / `5173` |

에러 응답은 `{"error": {"code", "message"}}` 한 형식이다.
