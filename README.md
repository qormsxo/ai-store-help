# AI Store Help

소규모 사업자의 고객 문의를 AI가 응대하고, AI가 답할 수 없는 문의는 사장에게 전달하는 서비스.
사장의 답변은 지식 DB에 쌓여서 이후 유사한 문의에 AI가 자동 응답한다.

> 현재 상태: **기반 설정 + DB 모델 단계**. Health Check, 라우팅, 공통 레이아웃, DB 모델/마이그레이션까지 있고 Agent와 업무 기능은 아직 없다.

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| Backend | Python 3.14, FastAPI, SQLAlchemy 2.x (async, asyncpg), Pydantic v2 |
| Database | PostgreSQL 16 |
| Frontend | React 19, Vite, TypeScript, React Router |
| 실행 환경 | Docker Compose |

Python 버전은 `backend/Dockerfile`의 `FROM python:3.14-slim` 한 줄로 바꾼다.

## 빠른 시작 (Docker Compose)

```bash
cp .env.example .env
docker compose up --build
```

| 서비스 | 주소 |
| --- | --- |
| Frontend | http://localhost:5173 |
| Backend API 문서 | http://localhost:8000/docs |
| Health Check | http://localhost:8000/api/health |
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

테이블 4개: `conversations`, `messages`, `support_tickets`, `knowledge_entries` (모델: `backend/app/models.py`).
Knowledge DB는 비어 있는 상태로 시작한다. Seed 데이터는 없다.

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
      errors.py                 # 공통 예외와 에러 응답 형식
      schemas.py                # Pydantic 스키마
      api/                      # 라우터 (요청/응답 처리만)
      services/                 # 비즈니스 로직
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
