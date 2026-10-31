# CourseWave

CourseWave is a student platform built around a FastAPI backend, PostgreSQL,
Redis, and RabbitMQ.

## Services

| Service | Description | Port |
|---------|-------------|------|
| `coursewave` | FastAPI backend | `3000` |
| `postgres` | PostgreSQL 17 with `coursewave_db` | `5432` |
| `redis` | Sessions, cache, rate limits, verification tokens | `6379` |
| `rabbitmq` | Provisioned message broker | `5672`, `15672` |

## Quick Start

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The API is available at `http://localhost:3000`. Interactive documentation is
at `http://localhost:3000/docs` and requires a Bearer access token.

## Backend

See [`backend/README.md`](backend/README.md) for configuration, middleware
behavior, the authentication and refresh-token contracts, and testing
instructions.

## Tests

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```
