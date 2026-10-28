# CourseWave Backend

FastAPI backend for the CourseWave platform. The student service accepts
requests forwarded by the CourseWave API Gateway and enforces the gateway's
HMAC request signature before allowing access to protected routes.

## Features

- HMAC-SHA256 verification of `x-proxy-signature`.
- Millisecond timestamp validation with a 30-second tolerance.
- Replay protection by rejecting stale and future-dated signatures.
- `X-REQUEST-ID` propagation for distributed tracing.
- Structured JSON request logs with method, path, status code, message, and
  request ID.
- `/health` endpoint exempt from proxy-signature checks.
- FastAPI-standard error responses: `{"detail": "..."}`.
- SQLModel-based PostgreSQL access through an async request-scoped session
  dependency.
- Validated student registration with Argon2 password hashing.
- Redis-backed email verification tokens with a 120-second TTL.
- Transactional outbox events for asynchronous email delivery.

## Project Structure

```text
backend/
├── app/
│   ├── main.py
│   ├── base_config.py
│   ├── logging_config.py
│   ├── security.py
│   ├── base_dependencies/
│   │   └── __init__.py
│   ├── base_utils/
│   │   └── __init__.py
│   ├── db_config/
│   │   └── __init__.py
│   ├── middleware/
│   │   ├── check_proxy_signature.py
│   │   └── request_logger.py
│   ├── redis_config/
│   │   └── __init__.py
│   └── services/
│       ├── __init__.py
│       └── student/
│           ├── __init__.py
│           ├── exception/
│           │   └── __init__.py
│           ├── model/
│           │   ├── __init__.py
│           │   ├── base.py
│           │   ├── outbox.py
│           │   └── student.py
│           ├── router/
│           │   ├── __init__.py
│           │   └── student.py
│           └── schema/
│               └── __init__.py
├── tests/
├── .env.example
├── requirements.txt
└── requirements-dev.txt
```

## Getting Started

### Prerequisites

- Python 3.12+
- The same `PROXY_SECRET` configured in the API gateway
- PostgreSQL and Redis when running the service outside Docker Compose

### Installation

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

### Configuration

```powershell
Copy-Item .env.example .env
```

| Variable | Description | Default |
|----------|-------------|---------|
| `PROXY_SECRET` | Shared secret used by the gateway to sign requests | `change-me-in-production` |
| `PROXY_SIGNATURE_TTL_SECONDS` | Allowed timestamp tolerance | `30` |
| `LOG_LEVEL` | Application log level | `INFO` |
| `APP_HOST` | Public host used to build email verification links | `localhost:3000` |
| `REDIS_HOST` | Redis host | `localhost` |
| `REDIS_PORT` | Redis port | `6379` |
| `REDIS_PASSWORD` | Redis password; empty disables authentication | empty |
| `REDIS_DB` | Redis database number | `0` |
| `AUTO_CREATE_TABLES` | Create missing SQLModel tables at startup | `false` |
| `DATABASE_URL` | Optional SQLAlchemy database URL override | `postgresql+asyncpg://postgres:user123@localhost:5432/coursewave_db` |

### Run

```powershell
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

### Docker Compose

The full stack is defined in the repository root:

```powershell
cd ..
docker compose up --build
```

It starts the API gateway, backend service, Redis, PostgreSQL, and Kafka. The
backend service uses `DATABASE_URL` to reach the `postgres` container and runs
with `AUTO_CREATE_TABLES=true`, so the `student` and `outbox` tables are created
on startup if they do not exist.

## Database

The service uses SQLModel models with SQLAlchemy's async PostgreSQL engine.
`app/db_config/__init__.py` provides the cached engine factory and currently
connects to:

| Setting | Value |
|---------|-------|
| Driver | `postgresql+asyncpg` |
| Host | `localhost` |
| Port | `5432` |
| Database | `coursewave_db` |
| Username | `postgres` |
| Password | `user123` |

The connection settings default to the values above. Set `DATABASE_URL` to
override them without changing code:

```text
DATABASE_URL=postgresql+asyncpg://postgres:user123@postgres:5432/coursewave_db
```

The root `docker-compose.yml` uses this override so the service connects to the
PostgreSQL container. Move all credentials into environment variables or a
secrets manager before deployment.

Use the request-scoped session dependency in routes:

```python
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_dependencies import get_session


async def endpoint(session: AsyncSession = Depends(get_session)):
    ...
```

`get_session` yields one session per request, rolls back on exceptions, and
closes the session automatically. The application lifespan disposes of the
engine's connection pool when the service shuts down.

### Models

`Student` stores the account profile and authentication data:

- `user_id` (UUID primary key, generated automatically)
- `first_name`, optional `middle_name`, `last_name`
- unique, indexed `email`
- Argon2 `password_hash`
- `is_email_verified` (default `false`)
- `created_at`, `updated_at`
- optional `profile_pic_url`

`Outbox` stores events that must be delivered reliably:

- `id` (UUID primary key)
- `status` (`pending`, `processed`, or `failed`)
- optional `retry_after`
- `num_of_retries` (default `0`)
- `topic`
- JSON `payload`
- `created_at`, `updated_at`

## Student Registration

`POST /student/api/v1/register` creates a student account and queues the email
verification event. The route requires a valid gateway signature.

Request body:

```json
{
  "first_name": "Ada",
  "middle_name": "Augusta",
  "last_name": "Lovelace",
  "email": "ada@example.com",
  "password": "StrongPass1!",
  "confirm_password": "StrongPass1!"
}
```

Validation rules:

- `first_name` and `last_name` are required and cannot be blank.
- `middle_name` is optional; blank values are stored as `null`.
- `email` must be valid and is normalized to lowercase.
- Passwords must be 8-128 characters and contain uppercase, lowercase, numeric,
  and special characters.
- `password` and `confirm_password` must match.

The endpoint performs the following work:

1. Hashes the password with Argon2.
2. Generates a URL-safe verification token and stores it in Redis with the
   student UUID as its value and a 120-second TTL.
3. Builds `http://{APP_HOST}/email-verification?token={token}`. If `APP_HOST`
   already includes an `http://` or `https://` scheme, that scheme is preserved.
4. Inserts the `Student` and an `Outbox` row in the same database transaction.

The outbox event uses topic `student.registered` and payload:

```json
{
  "recipient_email": "ada@example.com",
  "verification_link": "http://localhost:3000/email-verification?token=..."
}
```

Successful response (`201 Created`):

```json
{
  "user_id": "6f8c3e5e-3fbc-4f2e-8f19-3e0f6f8a9b21",
  "message": "Registration successful. Please verify your email."
}
```

Errors:

- `409 Conflict` when the email is already registered.
- `422 Unprocessable Entity` when request validation fails.
- `503 Service Unavailable` when Redis cannot store the verification token.

If the database transaction fails after the Redis token is written, the token is
deleted on a best-effort basis. The email verification endpoint itself is not
implemented yet; registration only creates and stores the verification link.

## Gateway Signature Verification

The API gateway signs each proxied request with:

```text
HMAC-SHA256(
  key = PROXY_SECRET,
  message = "{METHOD}:{URL}:{TIMESTAMP}"
).hexdigest()
```

The URL is the gateway's rewritten `req.url`, including the query string. The
timestamp is epoch milliseconds.

Required headers:

```http
x-proxy-signature: <hex-digest>
x-proxy-timestamp: <epoch-milliseconds>
```

The middleware rejects a request with `403` when:

- either signature header is missing,
- the timestamp is not a valid integer,
- the timestamp is 30 seconds or more away from the current time,
- the signature does not match the expected HMAC.

Example error:

```json
{
  "detail": "Invalid proxy signature"
}
```

`GET /health` bypasses signature verification for container and load-balancer
health checks.

## Request Logging

Every HTTP request produces a JSON log entry:

```json
{
  "timestamp": "2026-10-27T00:00:00.000000+00:00",
  "level": "INFO",
  "logger": "student_service.request",
  "method": "GET",
  "path": "/health",
  "status_code": 200,
  "message": "Request completed",
  "request_id": "4f3c2f0e-7f5f-4c6d-9f0a-2a6f7d1e8b21",
  "duration_ms": 1.42
}
```

The logger reads `X-REQUEST-ID` from the gateway and generates a UUID if the
header is absent. The response includes `X-REQUEST-ID`.

Requests rejected by the signature middleware are logged with status `403` and
the signature error as the message.

## API

| Method | Path | Description | Signature required |
|--------|------|-------------|--------------------|
| `GET` | `/health` | Health check | No |
| `POST` | `/student/api/v1/register` | Register a student | Yes |
| `GET` | `/docs` | FastAPI documentation | Yes |
| `GET` | `/openapi.json` | OpenAPI schema | Yes |

## Testing

```powershell
.venv\Scripts\python -m pytest
```

The tests cover valid signatures, missing headers, invalid timestamps, expired
and future timestamps, tampered signatures, query-string signing, request-ID
propagation, structured request logging, PostgreSQL engine configuration,
session rollback behavior, registration validation, Argon2 password hashing,
Redis token creation, duplicate-email handling, and transactional outbox writes.

## License

ISC
