# AI Store Help

소규모 사업자의 고객 문의를 AI가 응대하고, AI가 답할 수 없는 문의는 사장에게 전달하는 서비스.
사장의 답변은 지식 DB에 쌓여서 이후 유사한 문의에 AI가 자동 응답한다.

> 현재 상태: **AI Agent 단계**. Health Check, DB 모델/마이그레이션, 매장 정보 API(`GET /api/store`)와 seed, Gemini Function Calling Agent(`app/agent/`)까지 있다. Agent를 부르는 채팅 REST API와 SSE, 사장 화면은 아직 없다.

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| Backend | Python 3.14, FastAPI, SQLAlchemy 2.x (async, asyncpg), Pydantic v2 |
| Database | PostgreSQL 16 (Knowledge 검색은 English Full Text Search) |
| AI | Google Gemini (`google-genai` SDK, async client, 수동 Function Calling) |
| Frontend | React 19, Vite, TypeScript, React Router |
| 실행 환경 | Docker Compose |

Python 버전은 `backend/Dockerfile`의 `FROM python:3.14-slim` 한 줄로 바꾼다.

## 빠른 시작 (Docker Compose)

```bash
cp .env.example .env
docker compose up --build
docker compose exec backend python -m app.seed    # 데모 매장 생성 (처음 한 번)
```

seed 전에는 매장이 없어서 `GET /api/store`가 503(`STORE_NOT_CONFIGURED`)을 반환하고, 화면 상단에 "매장 정보를 불러오지 못했습니다" 안내가 뜬다. seed를 실행하면 해소된다.

| 서비스 | 주소 |
| --- | --- |
| Frontend | http://localhost:5173 |
| Backend API 문서 | http://localhost:8000/docs |
| Health Check | http://localhost:8000/api/health |
| 매장 정보 | http://localhost:8000/api/store |
| PostgreSQL | localhost:5432 (`.env`의 `POSTGRES_PORT`) |

Health Check 정상 응답:

```json
{"status": "ok", "database": "ok"}
```

DB에 연결할 수 없으면 `503`과 함께 `{"error": {"code": "SERVICE_UNAVAILABLE", "message": "..."}}`를 반환한다.

> **포트 충돌**: 로컬에 이미 PostgreSQL이 5432를 쓰고 있으면 `.env`에서 `POSTGRES_PORT=5433`으로 바꾸고, 로컬 실행용 `DATABASE_URL`의 포트도 같이 바꾼다. 컨테이너 내부 통신은 항상 5432를 쓰므로 영향 없다.

소스는 컨테이너에 볼륨으로 마운트되어 있어서 코드를 고치면 backend(`--reload`)와 frontend(HMR)에 바로 반영된다.

## 로컬 실행 (Docker 없이 개발)

DB만 Docker로 띄우고 backend와 frontend는 직접 실행한다.

```bash
docker compose up -d db

# Backend (저장소 루트의 .env를 읽는다)
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload

# Frontend (새 터미널)
cd frontend
npm install
npm run dev
```

로컬 실행에서는 `DATABASE_URL`이 `localhost`를 가리켜야 한다 (`.env.example` 기본값).

## 데이터베이스와 마이그레이션

테이블 6개 (모델: `backend/app/models.py`):

- `stores`, `business_hours`: 매장 정보와 요일별 영업시간
- `conversations`, `messages`: 고객(visitor_id 기반 익명) 대화
- `support_tickets`, `knowledge_entries`: 사장에게 넘긴 문의와 축적된 지식

`conversations`, `support_tickets`, `knowledge_entries`는 `store_id`를 가진다. `messages`의 매장은 `conversation.store_id`로 얻는다.
사장 계정과 User 테이블은 없다.

### 매장 고정 정책

- 한 실행 환경에서는 매장 하나만 쓴다. 환경변수 `DEFAULT_STORE_SLUG`(기본값 `oak-pine-demo`)가 그 매장을 정한다.
- 고객 요청과 LLM은 `store_id`를 지정할 수 없다. 백엔드가 slug로 매장을 조회해 내부적으로만 넘긴다.
- 해당 slug의 매장이 DB에 없으면 `503 STORE_NOT_CONFIGURED`를 반환한다.
- 영업시간은 요일을 0=월요일 … 6=일요일로 저장하고, 시간이 NULL이면 휴무다. 시각은 매장 `timezone` 기준 현지 시각이다.

### Seed

```bash
docker compose exec backend python -m app.seed    # 로컬 실행이면 backend/에서 python -m app.seed
```

- 가상의 미국 매장 1건과 영업시간 7건(일요일 휴무)을 만든다.
- 여러 번 실행해도 중복이 생기지 않는다. 같은 slug의 매장이 이미 있으면 **아무것도 바꾸지 않는다**(수정한 매장 정보를 덮어쓰지 않는다).
- Knowledge, Conversation, Message, SupportTicket은 만들지 않는다. **Knowledge DB는 항상 빈 상태로 시작한다.**
- 역할 구분: 마이그레이션은 스키마를, seed는 데모 데이터를 담당한다. 기존 데이터가 있는 DB에 `0002`를 적용하면 마이그레이션이 placeholder 매장(`Migrated Store`) 1건을 만들어 기존 행을 연결한다. 이 경우 seed는 같은 slug를 건너뛰므로 매장 정보를 직접 수정한다.

Docker Compose의 backend는 기동할 때 `alembic upgrade head`를 자동 실행한다. 수동 실행은 다음과 같다.

```bash
cd backend                       # 컨테이너에서는 docker compose exec backend <명령>
alembic upgrade head             # 최신 스키마 적용
alembic downgrade base           # 전부 되돌리기
alembic revision --autogenerate -m "설명"   # 모델 변경 후 마이그레이션 생성 (생성된 파일은 꼭 검토)
alembic check                    # 모델과 DB 스키마가 어긋났는지 확인
```

주의: `downgrade`는 테이블뿐 아니라 PostgreSQL enum 타입도 지워야 다시 `upgrade`할 수 있다. 새 enum을 추가하면 마이그레이션의 `downgrade`에 `DROP TYPE`을 직접 넣는다.

## 테스트

```bash
cd backend
pytest
# 또는 컨테이너 안에서
docker compose exec backend pytest
```

- DB 테스트는 개발 DB와 같은 서버의 `<DB이름>_test` 데이터베이스를 쓴다 (없으면 자동 생성). 개발 데이터는 건드리지 않는다.
- 테스트 세션 시작 때 `alembic downgrade base` → `upgrade head`를 실행하므로 마이그레이션도 함께 검증된다.
- 테스트마다 트랜잭션을 롤백해서 서로 영향을 주지 않는다.
- `tests/test_migrations.py`는 모델과 마이그레이션이 어긋나면 실패한다. 모델을 고치면 마이그레이션을 같이 만든다.
- Agent 테스트는 실제 Gemini를 부르지 않는다. `tests/mock_llm.py`의 `MockGeminiProvider`(가짜)가 미리 정한 응답을 돌려주고, 실제 `AgentService`와 `ToolDispatcher`가 DB와 함께 동작한다.
- 실제 Gemini를 부르는 테스트는 `@pytest.mark.live`로 표시되어 기본 실행에서 빠진다. `GEMINI_API_KEY`와 `GEMINI_MODEL`을 설정한 뒤 `pytest -m live`로 실행한다 (키가 없으면 skip). API 사용량이 든다.

## AI Agent

고객 메시지 하나를 `AgentService.reply()`가 처리한다. Tool 선택은 LLM이 하고, 코드는 실행과 반복만 한다.

1. 대화(현재 매장 소속인지 확인)와 최근 메시지 20개를 불러오고, 고객 메시지를 저장한다.
2. System Instruction, 대화 기록, Tool 선언을 Gemini에 보낸다 (Automatic Function Calling은 끔).
3. 응답에 Function Call이 있으면 `ToolDispatcher`가 실행하고, 결과(Observation)를 Function Response로 붙여 다시 보낸다.
4. Function Call 없이 텍스트가 오면 최종 답변으로 저장하고 끝낸다.

| Tool | 인자 | 하는 일 |
| --- | --- | --- |
| `get_store_profile` | 없음 | 매장 기본 정보와 요일별 영업시간 |
| `search_knowledge` | `query` | 사장이 쌓은 지식 Full Text Search (없으면 빈 배열) |
| `create_support_ticket` | `question`, `escalation_reason` | 사장에게 넘길 티켓 생성 |

- `store_id`, `conversation_id`, `source_message_id`는 Backend가 실행 Context에서 넣는다. Tool 인자 모델은 `extra="forbid"`라서 LLM이 이 값을 보내면 `INVALID_ARGUMENTS`가 된다.
- Tool 실패(잘못된 인자, 없는 Tool, DB 오류)는 예외 대신 오류 Observation으로 LLM에 돌려준다. Tool마다 savepoint를 써서 DB 오류 뒤에도 세션을 계속 쓴다.
- 한 요청에서 LLM 호출은 최대 6번, Tool 호출은 최대 6번이다. 넘으면 `502 AGENT_LIMIT_EXCEEDED`.
- Gemini 429/5xx와 연결 오류는 최대 3번(1초, 2초 대기) 시도한다. 타임아웃과 그 밖의 4xx는 재시도하지 않는다.
- 커밋은 호출하는 쪽이 한다. 실패하면 롤백되어 고객 메시지와 티켓이 반쯤 저장되지 않는다.

| 오류 코드 | HTTP | 원인 |
| --- | --- | --- |
| `LLM_NOT_CONFIGURED` | 503 | 키/모델 미설정, 잘못된 키, 없는 모델 |
| `LLM_TIMEOUT` | 504 | `GEMINI_TIMEOUT` 초과 |
| `LLM_RATE_LIMITED` | 429 | 재시도 후에도 Gemini 요청 한도 초과 |
| `LLM_UNAVAILABLE` | 503 | 재시도 후에도 Gemini 서버/네트워크 오류 |
| `AGENT_LIMIT_EXCEEDED` / `AGENT_EMPTY_RESPONSE` | 502 | 반복 한도 초과 / 빈 응답 |

## 코드 품질 검사 (anti-slop)

`Any`/`object` 타입, `cast`, `type: ignore` 같은 우회 수단을 막는 린터다. 커밋 전에 실행한다.

```bash
cd backend
python tools/anti_slop
```

설정은 `backend/pyproject.toml`의 `[tool.anti-slop]`에 있다.

## 환경변수

`.env.example`을 복사해서 `.env`를 만든다. `.env`는 커밋하지 않는다.

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | DB 계정과 이름 | `app` |
| `POSTGRES_PORT` | 호스트에 노출할 DB 포트 | `5432` |
| `DATABASE_URL` | 로컬 실행용 DB 접속 문자열 (Docker에서는 compose가 덮어씀) | `postgresql+asyncpg://app:app@localhost:5432/app` |
| `DEFAULT_STORE_SLUG` | 이 환경이 응대하는 매장의 slug (소문자, 숫자, 하이픈) | `oak-pine-demo` |
| `GEMINI_API_KEY` | Gemini API 키. 없어도 서버는 뜨고 Agent만 503 | 없음 |
| `GEMINI_MODEL` | Gemini 모델 이름. 바꾸면 다른 모델을 쓴다 | 없음 (예: `gemini-3.8-flash`) |
| `GEMINI_MAX_OUTPUT_TOKENS` | 응답 최대 토큰 | `2048` |
| `GEMINI_TIMEOUT` | 요청 한 번의 제한 시간(초) | `30` |
| `APP_ENV` | 실행 환경 | `development` |
| `LOG_LEVEL` | 로그 레벨 | `INFO` |
| `BACKEND_PORT` / `FRONTEND_PORT` | 호스트에 노출할 포트 | `8000` / `5173` |

## 디렉터리 구조

```
ai-store-help/
  docker-compose.yml
  .env.example
  .cursor/rules/project.mdc     # 프로젝트 개발 규칙
  backend/
    app/
      main.py                   # FastAPI 앱, 라우터 등록
      config.py                 # 환경변수 설정 (pydantic-settings)
      db.py                     # async 엔진, 세션
      models.py                 # SQLAlchemy 모델과 enum
      seed.py                   # 데모 매장 seed (python -m app.seed)
      errors.py                 # 공통 예외와 에러 응답 형식
      schemas.py                # Pydantic 스키마
      api/                      # 라우터 (요청/응답 처리만)
      services/                 # 비즈니스 로직
      repositories/             # DB 조회 함수
      llm/                      # LLMProvider 인터페이스, GeminiProvider, 공통 대화 타입
      agent/                    # AgentService(실행 Loop), Tool, ToolDispatcher, System Instruction
    alembic/                    # 마이그레이션 (env.py는 async)
    tests/
    tools/anti_slop/            # 코드 품질 린터 (vendored)
  frontend/
    src/
      App.tsx                   # 라우팅 (/chat, /owner)
      components/Layout.tsx     # 공통 레이아웃
      pages/
      api/client.ts             # 백엔드 호출 (/api 프록시)
```

## 에러 응답 형식

모든 에러는 같은 형식이다.

```json
{"error": {"code": "NOT_FOUND", "message": "Not Found"}}
```
