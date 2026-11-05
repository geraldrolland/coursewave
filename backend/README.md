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
- `EMAIL` and `GOOGLE` authentication methods with nullable password hashes.
- Asynchronous notification processing through Celery, RabbitMQ quorum queues,
  and Redis locks.
- Jinja-rendered email templates for OTP and email verification messages.
- SendGrid email, Kafka push, and Termii SMS delivery through a provider
  registry.
- Authenticated WebSocket notifications at `/notification`.
- A four-minute Redis cache that skips already-processed notifications.
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
│       ├── notification/
│       │   ├── exception/
│       │   ├── model/
│       │   ├── provider/
│       │   │   ├── __init__.py
│       │   │   ├── base.py
│       │   │   ├── cache_mixin.py
│       │   │   ├── email_provider.py
│       │   │   ├── provider_registery.py
│       │   │   ├── push_provider.py
│       │   │   └── sms_provider.py
│       │   ├── pub_sub/
│       │   │   ├── __init__.py
│       │   │   └── kafka/
│       │   │       ├── __init__.py
│       │   │       ├── kafka_consumer.py
│       │   │       └── kafka_producer.py
│       │   ├── task/
│       │   │   └── process_notification.py
│       │   ├── template_manager/
│       │   │   └── __init__.py
│       │   └── ws/
│       │       ├── __init__.py
│       │       ├── authentication.py
│       │       └── websocket_notif_manager.py
│       └── student/
│           ├── exception/
│           ├── model/
│           ├── router/
│           └── schema/
├── celery_app.py
├── templates/
│   └── email/
│       ├── email_verification/
│       │   └── v1.html
│       └── otp/
│           └── v1.html
├── tests/
├── .env.example
├── requirements.txt
└── requirements-dev.txt
```

## Getting Started

### Prerequisites

- Python 3.12+
- PostgreSQL, Redis, and RabbitMQ when running outside Docker Compose

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
| `APP_HOST` | Public application host | `localhost:3000` |
| `REDIS_HOST` | Redis host | `localhost` |
| `REDIS_PORT` | Redis port | `6379` |
| `REDIS_PASSWORD` | Redis password; empty disables authentication | empty |
| `REDIS_DB` | Redis database number | `0` |
| `AUTO_CREATE_TABLES` | Create missing SQLModel tables at startup | `false` |
| `DATABASE_URL` | Optional SQLAlchemy database URL override | `postgresql+asyncpg://postgres:user123@localhost:5432/coursewave_db` |
| `RABBITMQ_URL` | Broker URL used by the Celery worker | `amqp://guest:guest@localhost:5672/` |
| `SENDGRID_API_KEY` | SendGrid API key used by the email provider | `SENDGRID_API_KEY_PLACEHOLDER` |
| `SENDGRID_FROM_EMAIL` | Verified sender address used by the email provider | `no-reply@coursewave.example` |
| `SENDGRID_FROM_NAME` | Sender display name used by the email provider | `CourseWave` |
| `NOTIFICATION_TOPIC` | Kafka topic for push notifications | `notification` |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker address for push delivery | `localhost:9092` |
| `KAFKA_CONSUMER_ENABLED` | Start the Kafka-to-WebSocket consumer | `false` |
| `KAFKA_CONSUMER_GROUP_ID` | Kafka consumer group for WebSocket delivery | `coursewave-notification-websockets` |
| `TERMII_API_KEY` | Termii API key used by the SMS provider | `TERMII_API_KEY_PLACEHOLDER` |
| `TERMII_SENDER_ID` | Registered Termii sender ID | `TERMII_SENDER_ID_PLACEHOLDER` |
| `TERMII_CHANNEL` | Termii channel | `generic` |
| `TERMII_MESSAGE_TYPE` | Termii message type | `plain` |

### Run

```powershell
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Start the notification worker in a second terminal after PostgreSQL, Redis, and
RabbitMQ are available:

```powershell
.venv\Scripts\celery.exe -A celery_app worker --loglevel=INFO --queues=task
```

### Docker Compose

```powershell
cd ..
Copy-Item .env.example .env
docker compose up --build
```

Compose starts five services:

- `coursewave` - this API, published on `http://localhost:3000`
- `worker` - the Celery worker consuming the `task` queue
- `postgres` - PostgreSQL with the `coursewave_db` database
- `redis` - sessions, cache, rate-limit counters, verification tokens, and
  notification locks
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
3. Inserts the `Student` with `auth_method=EMAIL` and the hashed password.

`password_hash` is nullable at the model level, allowing future
`auth_method=GOOGLE` accounts to have no local password.

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

## Email Templates

`app.services.notification.template_manager` renders the email bodies used by
notification providers:

```python
from app.services.notification.template_manager import TemplateManager

rendered = TemplateManager(
    "email_verification",
    {"link": "https://app.example.com/verify?token=abc"},
).get_template()

rendered.body
rendered.version_number
rendered.template_path
```

`TemplateManager` accepts either the string values `otp` and
`email_verification` or their `TemplateType` enum members. `get_template()`
returns a frozen `template` dataclass with `body`, `version_number`, and
`template_path` fields.

Templates are registered in `template_registery`:

```python
template_registery = {
    TemplateType.EMAIL_VERIFICATION: {
        "template_dir": "templates/email/email_verification",
        "template": "v1.html",
    },
}
```

`template_dir` contains the versioned HTML files, and `template` pins the version
used by `get_template()`. `version_number` is derived from the pinned filename,
so `v1.html` returns `1`. Template paths are resolved relative to `backend/`.

The email verification template renders `{{ link }}` as the `href` value.
The OTP template renders `{{ otp }}` in its message body. `template_build()` is
the static Jinja rendering helper used by `get_template()`.

## Notification Providers

`app.services.notification.provider` resolves the delivery implementation for a
notification type:

```python
from app.services.notification.provider import ProviderManager

provider = ProviderManager().get_provider("email")
provider.process(notification)
```

`ProviderManager.get_provider()` accepts `email`, `push`, and `sms` strings or
their `ProviderType` enum members. Looking up an unknown type raises
`ProviderNotFoundError`.

Providers implement `BaseProvider.process(notification)`. The built-in
`EmailProvider` uses SendGrid and expects this payload shape:

```json
{
  "template_type": "email_verification",
  "content": {
    "link": "https://example.com/verify"
  },
  "to_email": "student@example.com"
}
```

`to_email` is the preferred recipient key; `email` is also accepted. `content`
may be a JSON object or a JSON string.

`EMAIL_SUBJECT_REGISTRY` maps template types to email subjects. SendGrid uses
`SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`, and `SENDGRID_FROM_NAME`. Errors while
processing the payload, rendering, or building the message raise `ProviderError`.
SendGrid client and non-2xx response errors raise
`ProviderServiceNotAvailableError` (also exported with the
`ProviderServiceNotAvailbleError` spelling). Both are subclasses of
`NotificationError` so the existing Celery retry flow handles them.

`PushProvider` publishes `notification.payload` to the Kafka topic configured by
`NOTIFICATION_TOPIC` through the singleton producer in
`app.services.notification.pub_sub.kafka.kafka_producer`. Kafka uses
`KAFKA_BOOTSTRAP_SERVERS`. Producer or broker errors raise
`ProviderServiceNotAvailableError`.

`SMSProvider` sends `notification.payload` through Termii using
`termii-client`. Missing sender and routing fields are filled from
`TERMII_SENDER_ID`, `TERMII_MESSAGE_TYPE`, and `TERMII_CHANNEL`. Termii settings
are placeholders until real credentials are supplied.

`EmailProvider`, `PushProvider`, and `SMSProvider` inherit
`NotificationCacheMixin`. Before processing, they check Redis for:

```text
notification:processed:<notification-type>:<notification-id>
```

A cached key causes the provider to return without sending again. After a
successful send, the key is stored with the value `processed` and a 240-second
TTL. Redis read and write failures fail open so delivery is not blocked.

## WebSocket Notifications

Clients connect to:

```text
ws://localhost:8000/notification?token=<access-token>
```

The `authenticate_user` dependency decodes the access token, requires
`type == "access"`, loads `session:<sessionId>` from Redis, and stores the
session in `websocket.state.user`.

- Invalid or missing tokens and missing sessions close with code `1008`.
- Redis failures and malformed stored sessions close with code `1011`.

`WebsocketNotificationManager` tracks multiple sockets per `userId` and sends
messages only to sockets whose client state is connected. Failed sockets are
removed automatically.

When `KAFKA_CONSUMER_ENABLED=true`, the API lifespan starts `KafkaConsumer`.
It consumes `NOTIFICATION_TOPIC`, reads this payload shape, sends the message
to the active sockets for `recipient_id`, and commits the offset:

```json
{
  "recipient_id": "user-123",
  "message": {
    "title": "Notification title"
  }
}
```

The consumer uses `KAFKA_CONSUMER_GROUP_ID` and runs in the API process because
it must share the in-memory WebSocket connection registry.

## Notifications

Notifications are persisted in the `notification` table and delivered by the
`notification.process_notification` Celery task.

| Field | Type | Notes |
|-------|------|-------|
| `notification_id` | UUID | Primary key |
| `type` | `email`, `push`, `sms` | `NotificationType` |
| `payload` | JSON object | Channel-specific data |
| `belongs_to` | `student`, `instructor`, `admin` | `NotificationAudience` |
| `user_id` | UUID | Recipient identifier; indexed |
| `status` | `pending`, `processed`, `failed` | Defaults to `pending` |
| `created_at`, `updated_at` | timezone-aware datetimes | Audit timestamps |

The task resolves the provider using
`ProviderManager.get_provider(notification.type)` and calls
`provider.process(notification)`.

### Task flow

`process_notification(notification_id)`:

1. Acquires the Redis lock `notification:process:<id>` with a 120-second TTL.
2. If the lock is held, retries with the same 120-second lock TTL as its
   countdown.
3. Loads the notification.
4. Returns if the status is already `processed`.
5. Resolves the notification provider and calls `provider.process(notification)`.
6. On success, marks the notification `processed`.
7. On `NotificationError`, retries with 10 seconds of exponential backoff and
   jitter (`max_retries=5`). After the final attempt it marks the notification
   `failed` and rejects the message without requeue so RabbitMQ routes it to
   `task.dlq`.

The process lock is released in a `finally` block, including when a retry or
reject is raised.

### Queue topology

- `task` - main quorum queue with `x-delivery-limit=5`; dead-letters to
  `task.dlx` with routing key `task.dlq`.
- `task.dlq` - quorum dead-letter queue with no delivery limit. The worker does
  not consume it.
- Both exchanges are topic exchanges, so Celery can use RabbitMQ native delayed
  delivery for retry countdowns instead of holding messages in a worker.
- `task_acks_late=True`, `task_reject_on_worker_lost=True`, and
  `task_acks_on_failure_or_timeout=False` make worker crashes requeue messages
  so the delivery limit eventually moves poison messages to the DLQ.
- The worker declares both queues before consuming and only consumes `task`:

  ```powershell
  .venv\Scripts\celery.exe -A celery_app worker --loglevel=INFO --queues=task
  ```

There is no enqueue endpoint or scheduler yet. Another component must insert a
`Notification` row and submit `notification.process_notification` with its ID.

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
duplicate-email handling, student auth-method defaults, notification model
defaults, Celery queue topology, lock handling, retry behavior, final failure
handling, provider dispatch, email template rendering, SendGrid error handling,
Kafka producer publishing, Kafka consumer dispatch and commits, Termii SMS
delivery, WebSocket authentication and connection management, and
processed-notification caching.

## Known Limitations

- There is no login or token-issuance endpoint in this repository. Access and
  refresh tokens must be issued by another service that creates the matching
  Redis session.
- There is no notification enqueue endpoint or scheduler. Notification rows
  must be created and submitted by another component.
- Kafka is not included in the Compose stack. Set `KAFKA_BOOTSTRAP_SERVERS` to
  an existing broker for push delivery and set `KAFKA_CONSUMER_ENABLED=true`
  to deliver Kafka messages to WebSocket clients.
- SendGrid, Termii, and Kafka configuration defaults are placeholders and must
  be replaced before production use.
- There is no migration framework. Existing databases need the `notification`
  table created manually because `AUTO_CREATE_TABLES` only creates missing
  tables on a fresh database. If an earlier schema exists, drop the unused
  `release_after` column manually.

## License

ISC
