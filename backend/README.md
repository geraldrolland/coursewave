# CourseWave Backend

FastAPI service for the CourseWave platform. It is the public API on port
`3000` and enforces authentication, CORS, response caching, and rate limiting
directly.

## Features

- Bearer JWT authentication with Redis-backed sessions.
- Refresh-token rotation through signed cookies.
- CORS with the same configuration names the former gateway used.
- Redis caching for public JSON `GET` responses with a 7-minute TTL.
- Redis fixed-window and token-bucket rate limiting selected by route strategy.
- `X-REQUEST-ID` propagation and structured JSON request logs.
- FastAPI-standard error responses: `{"detail": "..."}`.
- Validated student registration with Argon2 password hashing.
- Redis-backed email verification tokens with a 120-second TTL.
- Transactional outbox events for asynchronous email delivery.
- PostgreSQL access through an async request-scoped SQLModel session.

## Project Structure

```text
backend/
├── app/
│   ├── main.py
│   ├── base_config.py
│   ├── logging_config.py
│   ├── security.py
│   ├── base_dependencies/
│   ├── base_utils/
│   ├── db_config/
│   ├── middleware/
│   │   ├── auth.py
│   │   ├── cache.py
│   │   ├── cors.py
│   │   ├── ratelimit/
│   │   │   ├── __init__.py
│   │   │   ├── fixed_window.py
│   │   │   ├── ratelimiter_registry.py
│   │   │   ├── token_bucket.py
│   │   │   └── utils.py
│   │   ├── refresh_token.py
│   │   └── request_logger.py
│   ├── redis_config/
│   └── services/
│       └── student/
│           ├── exception/
│           ├── model/
│           ├── router/
│           └── schema/
├── tests/
├── .env.example
├── requirements.txt
└── requirements-dev.txt
```

## Getting Started

### Prerequisites

- Python 3.12+
- PostgreSQL and Redis when running outside Docker Compose
- RabbitMQ is part of the Compose stack for outbox delivery

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
| `JWT_SECRET` | HS256 secret used to sign access and refresh tokens | `change-me-in-production` |
| `COOKIE_SECRET` | Secret used to sign the refresh cookie | `change-me-in-production` |
| `ACCESS_TOKEN_EXPIRY` | Access-token lifetime (`ms`, `s`, `m`, `h`, `d`, `w`) | `7m` |
| `REFRESH_TOKEN_EXPIRY` | Refresh-token lifetime | `7d` |
| `REFRESH_TOKEN_MAX_AGE_SECONDS` | Redis session TTL extension and cookie `Max-Age` | `604800` |
| `AUTH_EXCLUDE_PATHS` | Comma-separated public paths | see `.env.example` |
| `CORS_ORIGIN` | `*`, one origin, or a comma-separated list | `*` |
| `CORS_METHODS` | Comma-separated allowed methods | `GET,POST,PUT,PATCH,DELETE,OPTIONS` |
| `CORS_HEADERS` | Comma-separated allowed request headers | `Content-Type,Authorization,X-Request-ID` |
| `CORS_CREDENTIALS` | Allow credentialed CORS requests | `false` |
| `CACHE_ENABLED` | Enable the public GET cache | `true` |
| `CACHE_TTL_SECONDS` | Cache entry lifetime | `420` |
| `CACHE_EXCLUDE_PATHS` | Paths that are never cached | `/health` |
| `RATE_LIMIT_ENABLED` | Enable rate limiting | `true` |
| `RATE_LIMIT_MAX` | Requests allowed per window for every strategy | `100` |
| `RATE_LIMIT_EXCLUDE_PATHS` | Paths that are never rate limited | `/health` |
| `RATELIMIT_ROUTES` | JSON list of `{prefix, window_ms, strategy}` routes | see `.env.example` |
| `LOG_LEVEL` | Application log level | `INFO` |
| `APP_HOST` | Public host used to build email verification links | `localhost:3000` |
| `REDIS_HOST` | Redis host | `localhost` |
| `REDIS_PORT` | Redis port | `6379` |
| `REDIS_PASSWORD` | Redis password; empty disables authentication | empty |
| `REDIS_DB` | Redis database number | `0` |
| `AUTO_CREATE_TABLES` | Create missing SQLModel tables at startup | `false` |
| `DATABASE_URL` | Optional SQLAlchemy database URL override | `postgresql+asyncpg://postgres:user123@localhost:5432/coursewave_db` |
| `RABBITMQ_URL` | Broker URL used by outbox consumers | `amqp://guest:guest@localhost:5672/` |

### Run

```powershell
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

### Docker Compose

```powershell
cd ..
Copy-Item .env.example .env
docker compose up --build
```

Compose starts four services:

- `coursewave` - this API, published on `http://localhost:3000`
- `postgres` - PostgreSQL with the `coursewave_db` database
- `redis` - sessions, cache, rate-limit counters, verification tokens
- `rabbitmq` - broker with the management UI on `http://localhost:15672`

## Middleware

Middleware is registered in this order, and Starlette runs the last one added
first:

```text
CORS -> RequestLogger -> Auth -> RateLimit -> RefreshToken -> Cache -> routes
```

All custom middleware uses Starlette's `BaseHTTPMiddleware`; CORS uses the
built-in `CORSMiddleware`.

- CORS handles browser preflight requests before authentication.
- Request logging covers rejected requests too.
- Auth validates access tokens and loads the session.
- Rate limiting can therefore key authenticated requests by user.
- Refresh runs after rate limiting but bypasses auth for its exact path.
- Cache runs closest to the routes and only stores public JSON GET responses.

## Authentication

Protected routes require:

```http
Authorization: Bearer <access-token>
```

The token must be an HS256 JWT signed with `JWT_SECRET` containing:

```json
{
  "sessionId": "the-session-id",
  "type": "access",
  "iat": 1760000000,
  "exp": 1760000420
}
```

The middleware then loads `session:<sessionId>` from Redis. The value is a JSON
document such as:

```json
{
  "userId": "6f8c3e5e-3fbc-4f2e-8f19-3e0f6f8a9b21",
  "role": "student",
  "email": "ada@example.com"
}
```

The parsed document is stored in the request state as `user`. Redis errors
return `500` and missing or invalid sessions return `401`. Public paths are
configured with `AUTH_EXCLUDE_PATHS`.

## Token Refresh

`POST /auth/api/v1/token-refresh` is handled by `BaseHTTPMiddleware`, not a
route. It reads the `refresh` cookie, which uses Express-compatible signing:

```text
s:<JWT>.<base64(HMAC-SHA256(COOKIE_SECRET, JWT)) without padding>
```

The refresh JWT must be signed with `JWT_SECRET`, have `type` equal to
`"refresh"`, and carry a `sessionId` that still exists in Redis. A successful
refresh:

1. Extends `session:<sessionId>` to `REFRESH_TOKEN_MAX_AGE_SECONDS`.
2. Issues a new access token and a new refresh token for the same session.
3. Sets the rotated cookie with `HttpOnly; Secure; SameSite=Strict;
   Path=/auth/api/v1/token-refresh; Max-Age=604800`.
4. Returns `{"accessToken": "..."}`.

Refresh errors use `{"detail": "..."}` with `401` or `500` status codes.

## Response Cache

Only `GET` responses are cached, and only when the request has no
`Authorization` header, no `Cookie` header, and no authenticated session. This
prevents one user's data from being served to another user.

Caching is also limited to the prefixes in `CACHE_ROUTES` in
`backend/app/middleware/cache.py`:

```python
CACHE_ROUTES = (
    "/student/api/v1",
    "/instructor/api/v1",
)
```

Paths outside those prefixes bypass Redis cache reads and writes and do not
receive an `X-Cache` header. Prefixes match complete path segments, so
`/student/api/v10` does not match `/student/api/v1`.

- Cache key: `cache:<path>` plus the raw query string when present.
- TTL: `CACHE_TTL_SECONDS` (420 seconds by default).
- Only responses whose media type is exactly `application/json` are cached.
  Parameters such as `application/json; charset=utf-8` are allowed, but
  `application/problem+json` and other non-JSON media types are not cached.
- Non-JSON responses under a cache route are streamed through without buffering
  and still receive `X-Cache: MISS`.
- Responses are cached only when they are `200` and have no `Set-Cookie`,
  `Cache-Control: no-store`/`private`, or `Vary: *`.
- Streaming responses and bodies over 1 MiB are not cached.
- Redis failures fail open and never break the request.
- The `X-Cache` header is `HIT` or `MISS` on cache-routed responses.

## Rate Limiting

Rate limits are keyed by the session `userId` for authenticated requests and by
the socket IP for anonymous requests.

Routes are configured with `RATELIMIT_ROUTES`, a JSON list of dictionaries:

```json
[
  {
    "prefix": "/student/api",
    "window_ms": 90000,
    "strategy": "fixed_window"
  },
  {
    "prefix": "*",
    "window_ms": 90000,
    "strategy": "fixed_window"
  }
]
```

- `prefix`: request path prefix. Routes are checked in configured order and the
  first matching prefix wins.
- `window_ms`: window length in milliseconds.
- `strategy`: registry key used to select the rate-limit handler.
- `{"prefix": "*"}` matches any path and is normally listed last.
- If no route matches, the middleware falls back to
  `{"window_ms": 90000, "strategy": "fixed_window"}`.
- `RATE_LIMIT_MAX` is the number of requests allowed per window for both
  strategies.
- `/health` is excluded by default.

Strategy handlers live in
`backend/app/middleware/ratelimit/ratelimiter_registry.py`:

```python
RATELIMITER_REGISTRY = {
    "fixed_window": fixed_window_limit,
    "token_bucket": token_bucket_limit,
}
```

Handlers receive the request and the route window and return a boolean:

```python
async def fixed_window_limit(request, window_ms) -> bool:
    ...
```

The middleware binds Redis and `RATE_LIMIT_MAX` to the handler before calling
it. Handlers also publish `limit`, `remaining`, `reset`, and `retry-after`
metadata in the request state so the middleware can set the response headers.

- `fixed_window` uses Redis `INCR`/`EXPIRE`.
- `token_bucket` uses a Redis Lua script. Its capacity is `RATE_LIMIT_MAX` and
  it refills `RATE_LIMIT_MAX` tokens over `window_ms`.
- Keys: `ratelimit:fixed_window:<prefix>:<identifier>` and
  `ratelimit:token_bucket:<prefix>:<identifier>`.
- Response headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
  `X-RateLimit-Reset`.
- `429` responses include `Retry-After` and
  `{"detail": "Too many requests, please try again later"}`.
- Redis failures fail open.
- Unknown strategies log a warning and fall back to `fixed_window`.

## CORS

CORS uses the former gateway environment names:

- `CORS_ORIGIN`: `*`, one origin, or a comma-separated list.
- `CORS_METHODS`: comma-separated methods.
- `CORS_HEADERS`: comma-separated allowed request headers.
- `CORS_CREDENTIALS`: `true` or `false`.

Exposed response headers include `X-Request-ID`, the rate-limit headers,
`Retry-After`, and `X-Cache`.

## Student Registration

`POST /student/api/v1/register` is public and unchanged. It creates a student
account and queues the email verification event.

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

The endpoint:

1. Hashes the password with Argon2.
2. Generates a URL-safe verification token and stores it in Redis with the
   student UUID as its value and a 120-second TTL.
3. Builds `http://{APP_HOST}/email-verification?token={token}`.
4. Inserts the `Student` and an `Outbox` row in the same database transaction.

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
deleted on a best-effort basis.

## API

| Method | Path | Description | Authentication |
|--------|------|-------------|----------------|
| `GET` | `/health` | Health check | Public |
| `POST` | `/student/api/v1/register` | Register a student | Public |
| `POST` | `/auth/api/v1/token-refresh` | Rotate tokens | Refresh cookie |
| `GET` | `/docs` | FastAPI documentation | Bearer token |
| `GET` | `/openapi.json` | OpenAPI schema | Bearer token |

## Testing

```powershell
.venv\Scripts\python -m pytest
```

The suite covers authentication, CORS, caching, rate limiting, refresh-token
rotation, request logging, PostgreSQL configuration, session cleanup,
registration validation, password hashing, Redis token creation,
duplicate-email handling, and transactional outbox writes.

## Known Limitations

- There is no login or token-issuance endpoint in this repository. Access and
  refresh tokens must be issued by another service that creates the matching
  Redis session.
- RabbitMQ is provisioned in Compose, but this repository does not yet contain
  an outbox publisher or consumer.

## License

ISC
