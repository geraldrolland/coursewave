# CourseWave

CourseWave is a student platform built around a FastAPI backend, PostgreSQL,
Redis, RabbitMQ, MinIO, and Celery workers for asynchronous notification
delivery. Kafka is used for push notification fan-out and authenticated
WebSocket delivery through an external broker.

## Services

| Service | Description | Port |
|---------|-------------|------|
| `coursewave` | FastAPI backend | `3000` |
| `worker` | Celery worker for notification and outbox tasks | - |
| `beat` | Celery Beat scheduler for the outbox dispatcher | - |
| `dlq-consumer` | Retries dead-lettered tasks and marks exhausted notifications failed | - |
| `postgres` | PostgreSQL 17 with `coursewave_db` | `5432` |
| `redis` | Sessions, cache, rate limits, verification tokens, notification locks | `6379` |
| `rabbitmq` | Task broker with quorum queues and a dead-letter queue | `5672`, `15672` |
| `minio` | Profile-picture object storage and console | `9000`, `9001` |
| `minio-init` | Creates the public-read `profilepics` bucket, then exits | - |

## Quick Start

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The API is available at `http://localhost:3000`. Interactive documentation is
at `http://localhost:3000/docs` and requires a Bearer access token.

## Backend

See [`backend/README.md`](backend/README.md) for configuration, middleware
behavior, student and instructor registration, login, Google authentication,
current-session lookup, logout, profile lookup and updates, MinIO-backed profile
pictures, email verification, password reset, the authentication and
refresh-token contracts, the notification task flow and queue topology, email
template rendering, notification providers, authenticated WebSocket
notifications, and testing instructions.

## Tests

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```
