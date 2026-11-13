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
- A DLQ consumer that retries failed notifications through a TTL queue and
  marks them failed after the retry budget is exhausted.
- Jinja-rendered email templates for OTP and email verification messages.
- SendGrid email, Kafka push, and Termii SMS delivery through a provider
  registry.
- Authenticated WebSocket notifications at `/notification`.
- A four-minute Redis cache that skips already-processed notifications.
- PostgreSQL access through an async request-scoped SQLModel session.
- MinIO-backed profile-picture storage with multipart profile updates and
  public-read picture URLs.
- Instructor account, profile, education, experience, and payment-information
  models with email verification, optional 2FA, email/Google auth, JSON
  expertise, and degree enums, plus registration, login, OTP verification and
  resend, email verification, password reset, Google login, current-session, and
  logout routes.
- Non-null `role` fields on student and instructor accounts and a shared Redis
  session contract of `{"id": "...", "email": "...", "role": "..."}`.
- An instructor verification model with JSON identity/academic payloads and a
  pending/verified/rejected status.
- Student account suspension and verification flags, both defaulting to false.

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
│       ├── instructor/
│       │   ├── model/
│       │   │   ├── education.py
│       │   │   ├── experience.py
│       │   │   ├── instructor.py
│       │   │   ├── payment_info.py
│       │   │   └── profile.py
│       │   ├── router/
│       │   │   ├── __init__.py
│       │   │   ├── instructor.py
│       │   │   └── profile.py
│       │   ├── schema/
│       │   │   └── __init__.py
│       │   ├── google.py
│       │   └── utils/
│       │       └── __init__.py
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
│       ├── outbox/
│       │   ├── model/
│       │   │   └── outbox.py
│       │   └── task/
│       │       └── process_outbox.py
│       ├── storage/
│       │   └── minio.py
│       ├── student/
│       │   ├── exception/
│       │   ├── model/
│       │   ├── router/
│       │   ├── schema/
│       │   └── utils/
│       └── verification/
│           └── model/
│               ├── __init__.py
│               └── verification.py
├── celery_app/
│   ├── __init__.py
│   └── task_registry.py
├── dlq/
│   ├── __init__.py
│   ├── consumer.py
│   └── handlers.py
├── templates/
│   └── email/
│       ├── email_verification/
│       │   └── v1.html
│       └── otp/
│           └── v1.html
├── tests/
├── .env.example
├── pytest.ini
├── requirements.txt
└── requirements-dev.txt
```

## Getting Started

### Prerequisites

- Python 3.12+
- PostgreSQL, Redis, RabbitMQ, and MinIO when running outside Docker Compose

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
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | `GOOGLE_CLIENT_ID_PLACEHOLDER` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | `GOOGLE_CLIENT_SECRET_PLACEHOLDER` |
| `GOOGLE_REDIRECT_URI` | Registered Google OAuth callback URL | `GOOGLE_REDIRECT_URI_PLACEHOLDER` |
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
| `MINIO_ENDPOINT` | MinIO API address used by the backend | `localhost:9000` |
| `MINIO_PUBLIC_URL` | Public base URL embedded in profile-picture URLs | `http://localhost:9000` |
| `MINIO_ACCESS_KEY` | MinIO access key | `coursewave` |
| `MINIO_SECRET_KEY` | MinIO secret key | `coursewave-secret` |
| `MINIO_BUCKET` | Bucket for uploaded profile pictures | `profilepics` |
| `MINIO_SECURE` | Use HTTPS when connecting to MinIO | `false` |
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

Start the DLQ consumer in a third terminal after PostgreSQL and RabbitMQ are
available:

```powershell
.venv\Scripts\python -m dlq.consumer
```

### Docker Compose

```powershell
cd ..
Copy-Item .env.example .env
docker compose up --build
```

Compose starts nine services:

- `coursewave` - this API, published on `http://localhost:3000`
- `worker` - the Celery worker consuming the `task` queue
- `beat` - the Celery Beat scheduler for the outbox dispatcher
- `dlq-consumer` - the DLQ consumer that retries and finalizes failed tasks
- `postgres` - PostgreSQL with the `coursewave_db` database
- `redis` - sessions, cache, rate-limit counters, verification tokens, and
  notification locks
- `rabbitmq` - broker with the management UI on `http://localhost:15672`
- `minio` - profile-picture object storage with the API on
  `http://localhost:9000` and console on `http://localhost:9001`
- `minio-init` - one-shot setup that creates the `profilepics` bucket and marks
  it publicly readable, then exits

The backend waits for `minio-init` to complete successfully before starting.
Compose uses `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` for the MinIO root
credentials, defaulting to `coursewave` and `coursewave-secret`.

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
  "id": "6f8c3e5e-3fbc-4f2e-8f19-3e0f6f8a9b21",
  "email": "ada@example.com",
  "role": "student"
}
```

Sessions contain exactly these three fields. Both services write the same
shape: `role` is `student` or `instructor`, and `id` is the student or
instructor UUID. Legacy sessions that only contain `userId` are not recognized
and must be recreated by logging in again.

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
- Only the response body is stored in Redis; response headers are not cached.
  Cache hits return `application/json` with a regenerated `Content-Length` and
  `X-Cache: HIT`.
- Only responses whose media type is exactly `application/json` are cached.
  Parameters such as `application/json; charset=utf-8` are allowed, but
  `application/problem+json` and other non-JSON media types are not cached.
- Non-JSON responses under a cache route are streamed through without buffering
  and still receive `X-Cache: MISS`.
- Responses are cached only when they are `200` and have no `Set-Cookie`,
  `Cache-Control: no-store`/`private`, or `Vary: *`.
- JSON bodies over 1 MiB are not cached. Chunked JSON responses under that limit
  are fully buffered and cached.
- Cacheable JSON responses are fully buffered before being returned, including
  bodies over 1 MiB.
- Redis failures fail open and never break the request.
- The `X-Cache` header is `HIT` or `MISS` on cache-routed responses.

## Rate Limiting

Rate limits are keyed by the session `id` for authenticated requests and by the
socket IP for anonymous requests.

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

`POST /student/api/v1/register` is public. It creates a student account,
persists an email verification notification, and writes a transactional outbox
row so the notification is dispatched after the database commit.

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
3. Builds `{APP_HOST}/email-verification?token={token}`. A missing
   `http://`/`https://` scheme is added automatically.
4. Inserts the `Student` with `auth_method=EMAIL` and the hashed password.
5. Inserts an email `Notification` with a provider-compatible payload:
   `template_type=email_verification`, `content={"link": ...}`, and `to_email`.
6. Inserts an `Outbox` row with
   `payload={"argument": {"notification_id": "..."}, "task_name": "process_notification"}`,
   `table="notification"`, and `ref_id=<notification UUID>`.
7. Commits the student, notification, and outbox rows in one transaction.

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
deleted on a best-effort basis. No task is published directly from the request.

## Student Login

`POST /student/api/v1/login` is public.

Request body:

```json
{
  "email": "ada@example.com",
  "password": "StrongPass1!"
}
```

The endpoint queries the student by normalized email. Unknown emails, accounts
without a local password, and incorrect passwords all return the same
`401 {"detail": "Invalid email or password"}` response.

If the password is correct but `is_email_verified` is false, the endpoint:

1. Creates a new 120-second verification token in Redis.
2. Queues an `email_verification` notification and outbox row.
3. Returns a `403` explaining that a fresh verification email was sent.

If the email is verified, the endpoint:

1. Generates a UUID session ID.
2. Stores `session:<session_id>` in Redis as
   `{"id": "...", "email": "...", "role": "student"}` with a 604800-second TTL.
3. Issues access and refresh JWTs.
4. Sets the signed refresh cookie with `HttpOnly; Secure; SameSite=Strict`,
   `Path=/auth/api/v1/token-refresh`, and `Max-Age=604800`.
5. Returns `200 {"accessToken": "..."}`.

The refresh endpoint accepts that cookie and rotates both tokens using the same
session ID.

## Google Authentication

`GET /student/api/v1/google?code=...&state=...` is public and is intended to be
the browser-facing OAuth callback. `state` is required and identifies the
frontend path to use when authentication fails.

The endpoint uses the configured Google credentials to:

1. Exchange the authorization code at
   `https://oauth2.googleapis.com/token` for an access token.
2. Fetch the account details from
   `https://www.googleapis.com/oauth2/v2/userinfo`.
3. Look up the student by the returned email address.

If the student already exists, the email is marked verified when necessary.
Their `auth_method` is left unchanged. If no student exists, a new account is
created with `is_email_verified=true`, `auth_method=google`, and no password
hash. The student's first and last names come from `given_name`/`family_name`,
the full `name`, or the email local part as a fallback.

On success the endpoint creates the same Redis session and signed refresh cookie
as password login, then redirects with `302` to `{APP_HOST}/dashboard`. The
dashboard can call `/auth/api/v1/token-refresh` to obtain an access token.

Any failure, including a missing code, HTTP error, invalid userinfo response,
database error, or Redis error, redirects with `302` to
`{APP_HOST}/{state}?msg=google_authentication_failed`. A request without
`state` is rejected with `422`.

```env
GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID_PLACEHOLDER
GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET_PLACEHOLDER
GOOGLE_REDIRECT_URI=GOOGLE_REDIRECT_URI_PLACEHOLDER
```

The values are placeholders until real Google Cloud OAuth credentials are
configured. `GOOGLE_REDIRECT_URI` must exactly match the redirect URI registered
for the Google OAuth client.

## Current Session

`GET /student/api/v1/me` requires a Bearer access token.

The endpoint reads the authenticated Redis session that `AuthMiddleware` stores
on `request.state.user` and returns:

```json
{
  "id": "0f8fad5b-d9cb-469f-a165-70867728950e",
  "email": "ada@example.com"
}
```

The session's `id` and `email` values are returned directly; the `role` field is
not returned. No database lookup is performed. The endpoint is protected because
it is not listed in `AUTH_EXCLUDE_PATHS`, so missing, invalid, expired, or
session-less tokens return `401`. A session document without non-empty string
`id` and `email` values also returns
`401 {"detail": "Invalid authentication session"}`.

## Logout

`POST /student/api/v1/logout` requires a Bearer access token.

The endpoint decodes the access token, reads its `sessionId`, and deletes the
matching `session:<sessionId>` key from Redis. It then returns:

```json
{
  "message": "Logged out successfully"
}
```

The response also sets the refresh cookie to an expired value:

```text
refresh=; HttpOnly; Secure; SameSite=Strict; Path=/auth/api/v1/token-refresh; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT
```

The cookie is cleared because it uses the same name and path. Missing, invalid,
or expired access tokens are rejected by `AuthMiddleware` with `401`. A Redis
failure while deleting the session returns
`503 {"detail": "Logout service unavailable"}` and does not claim success.

## Get Profile

`GET /student/api/v1/profile` requires a Bearer access token and returns the
authenticated student's profile:

```json
{
  "first_name": "Ada",
  "last_name": "Lovelace",
  "email": "ada@example.com",
  "profile_pic_url": "http://localhost:9000/profilepics/6f0d...d4.png"
}
```

`profile_pic_url` is `null` until a picture has been uploaded. The student is
resolved from the session `id`, so this endpoint only returns the caller's own
profile. Missing or invalid sessions return `401`, and a session whose student
no longer exists returns `404 {"detail": "Student not found"}`.

## Profile Update

`PATCH /student/api/v1/profile` requires a Bearer access token and accepts
`multipart/form-data`.

| Field | Required | Description |
|-------|----------|-------------|
| `first_name` | No | New first name, trimmed, 1-100 characters |
| `middlename` | No | New middle name, trimmed, 1-100 characters; stored as `middle_name` |
| `profile_pic` | No | JPEG or PNG image smaller than 5 MB |

At least one of `first_name`, `middlename`, or `profile_pic` must be provided;
an empty request returns `422`. The form field is `middlename` without an
underscore, while the response uses `middle_name`.

```bash
curl -X PATCH http://localhost:3000/student/api/v1/profile \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "first_name=Grace" \
  -F "middlename=Brewster" \
  -F "profile_pic=@avatar.png"
```

Profile pictures must have a `.jpg`, `.jpeg`, or `.png` extension
(case-insensitive) and must be smaller than 5 MB. If the student has no existing
picture, or the existing URL cannot be parsed, the endpoint normalizes the
extension to lowercase, generates an object key as `<uuid>.<extension>`, and
uploads the file to `MINIO_BUCKET`. If `profile_pic_url` already points to an
object in the bucket, the endpoint extracts that object key and overwrites the
same object, so the URL stays unchanged. The key is reused verbatim even when
the new file has a different extension, and the upload's content type is stored
with it. Compose makes the `profilepics` bucket publicly readable so these URLs
do not expire.

On success, the endpoint returns:

```json
{
  "user_id": "5a4c5f54-03a5-4a71-9a86-0b25f4d9c1a2",
  "first_name": "Grace",
  "middle_name": "Brewster",
  "last_name": "Lovelace",
  "email": "ada@example.com",
  "profile_pic_url": "http://localhost:9000/profilepics/6f0d...d4.png"
}
```

The upload happens before the database commit. Replacing an existing picture
does not delete an old object because the existing key is overwritten. If the
upload fails, the endpoint returns
`503 {"detail": "Profile picture service unavailable"}` without changing the
student. If the commit fails, the transaction is rolled back; a newly generated
object is removed, while an overwritten existing object is left in place so the
stored URL remains usable. Uploads without a new picture leave
`profile_pic_url` unchanged.

## Instructor Models

The instructor service defines five SQLModel tables and public registration,
login, OTP, Google, email-verification, password-reset, session, and logout
routes.

- `instructor`: UUID `instructor_id`, required unique `email`, a non-null
  `role` defaulting to `instructor`, email-verification and 2FA flags defaulting
  to `false`, nullable `password_hash`, and `auth_method` defaulting to `email`
  with `google` as the other supported value.
- `instructor_profile`: UUID `profile_id`, unique foreign key `instructor_id`,
  required names and profile fields, a `male`/`female` gender enum, required
  JSON `expertise` such as `["tech", "science"]`, an integer `years_of_exp`,
  optional `company`, a 1000-character `bio`, and nullable JSON `social_links`
  containing entries such as `{"linkedin": "...", "x": "..."}`.
- `education`: UUID `education_id`, unique foreign key `instructor_id`,
  required `school` and `course`, a `doctorate`/`masters`/`bachelor`/
  `associate`/`diploma` degree enum, and a required `graduate_at` in `MM/YYYY`
  form.
- `experience`: UUID `experience_id`, indexed foreign key `instructor_id`,
  required `company_name`, `role`, and `started_at` in `MM/YYYY` form, a nullable
  `ended_at` in `MM/YYYY` form, and `till_present` defaulting to `false`.
- `payment_info`: UUID `payment_info_id`, unique foreign key `instructor_id`,
  required `full_name`, `account_number`, and `bank`, and `version_num`
  defaulting to `1`.

The `education`, `experience`, and `payment_info` tables include the standard
`created_at` and `updated_at` timestamps. `graduate_at`, `started_at`, and
`ended_at` are stored as `MM/YYYY` strings. Model-level validation is
intentionally omitted; callers are expected to validate input separately.

## Instructor Profile Creation

`POST /instructor/api/v1/profile` requires a Bearer access token and accepts
`multipart/form-data`. An instructor can create only one profile; an existing
profile returns `403 {"detail": "Instructor profile already exists"}`.

Required fields:

| Field | Description |
|-------|-------------|
| `first_name` | Required, trimmed, 1–100 characters |
| `last_name` | Required, trimmed, 1–100 characters |
| `gender` | `male` or `female` |
| `country_of_residence` | Required, 1–100 characters |
| `state` | Required, 1–100 characters |
| `city` | Required, 1–100 characters |
| `bio` | Required, 1–1000 characters |
| `headline` | Required, 1–255 characters |
| `profile_pic` | Required JPEG or PNG smaller than 5 MB |
| `current_occupation` | Required, 1–255 characters |
| `years_of_exp` | Required non-negative integer |
| `expertise` | Required non-empty list of strings |

Optional fields:

- `middle_name`: trimmed, 1–100 characters when supplied.
- `company`: trimmed, 1–255 characters when supplied.
- `social_links`: JSON list of objects containing `linkedin` and/or `x` URLs.
  When supplied, the list must be non-empty and every object must contain at
  least one link.

`expertise` and `social_links` accept either JSON-encoded form values or
repeated form fields. For example:

```text
expertise=["tech","science"]
social_links=[{"linkedin":"https://www.linkedin.com/in/ada","x":"https://x.com/ada"}]
```

The endpoint resolves the instructor from the authenticated session, rejects an
existing profile, validates the upload, generates a UUID object key, uploads the
picture to `MINIO_BUCKET`, and stores the public URL. Upload failures return
`503 {"detail": "Profile picture service unavailable"}`. Commit failures roll
back and delete the newly uploaded object on a best-effort basis.

## Instructor Registration

`POST /instructor/api/v1/register` is public. It creates an instructor account,
persists an email verification notification, and writes a transactional outbox
row so the notification is dispatched after the database commit.

Request body:

```json
{
  "email": "ada@example.com",
  "password": "StrongPass1!",
  "confirm_password": "StrongPass1!"
}
```

The request schema rejects extra fields, normalizes the email by trimming and
lowercasing it, and enforces the same password policy as student registration:
8–128 characters with an uppercase letter, a lowercase letter, a number, and a
special character. The two password fields must match.

The endpoint:

1. Hashes the password with Argon2.
2. Generates a URL-safe verification token and stores it in Redis with the
   instructor UUID as its value and a 120-second TTL.
3. Builds `{APP_HOST}/email-verification?token={token}`, using the same
   frontend path as student registration. A missing `http://`/`https://` scheme
   is added automatically.
4. Inserts the `Instructor` with `auth_method=EMAIL` and
   `is_email_verified=False`.
5. Inserts an email `Notification` with
   `belongs_to=INSTRUCTOR`, `template_type=email_verification`,
   `content={"link": ...}`, and `to_email`.
6. Inserts an `Outbox` row with
   `payload={"argument": {"notification_id": "..."}, "task_name": "process_notification"}`,
   `table="notification"`, and `ref_id=<notification UUID>`.
7. Commits the instructor, notification, and outbox rows in one transaction.

Successful response (`201 Created`):

```json
{
  "instructor_id": "6f8c3e5e-3fbc-4f2e-8f19-3e0f6f8a9b21",
  "message": "Registration successful. Please verify your email."
}
```

Errors:

- `409 Conflict` when the email is already registered.
- `422 Unprocessable Entity` when request validation fails.
- `503 Service Unavailable` when Redis cannot store the verification token.

If the database transaction fails after the Redis token is written, the token is
deleted on a best-effort basis. No task is published directly from the request.

The `student` table also includes a non-null `role` column defaulting to
`student`. Instructor email verification is completed through
`PATCH /instructor/api/v1/verify-email`.

## Instructor Login

`POST /instructor/api/v1/login` is public.

Request body:

```json
{
  "email": "ada@example.com",
  "password": "StrongPass1!"
}
```

The request schema rejects extra fields and normalizes the email by trimming and
lowercasing it. Unknown emails, accounts without a password hash, and incorrect
passwords all return `401 {"detail": "Invalid email or password"}`.

If the email is not verified, the endpoint creates a fresh 120-second
verification token, queues another email verification notification and outbox
row, and returns
`403 {"detail": "Email is not verified. A verification email has been sent."}`.

When 2FA is disabled, a successful login stores `session:<session_id>` as
`{"id": "...", "email": "...", "role": "instructor"}` with a 604800-second TTL,
sets the signed refresh cookie, and returns:

```json
{
  "accessToken": "..."
}
```

When `is_2fa_enabled` is true, the endpoint:

1. Generates a zero-padded six-digit OTP code.
2. Hashes the code with Argon2 and generates an opaque challenge token.
3. Stores the token in Redis for 24 hours with a JSON value containing the
   instructor UUID, the code hash, and the code expiry:
   `{"instructor_id": "...", "code_hash": "...", "exp": 1760000120}`.
   `exp` is the current Unix time plus 120 seconds, so the code is logically
   valid for two minutes even though the challenge token remains for 24 hours.
4. Inserts an email `Notification` with `belongs_to=INSTRUCTOR`,
   `template_type=otp`, `content={"otp_code": "..."}`, and `to_email`. The
   plaintext code is sent by email and is never stored in Redis.
5. Inserts an `Outbox` row and commits the notification and outbox together.
6. Redirects with `302` to
   `{APP_HOST}/otp-verification?token={token}`.

No login session or refresh cookie is created during the 2FA challenge. Redis
failures while creating a session or OTP challenge return
`503 {"detail": "Authentication service unavailable"}`. Redis failures while
resending email verification return
`503 {"detail": "Email verification service unavailable"}`. If the notification
transaction fails after the OTP token is written, the token is deleted on a
best-effort basis and the transaction is rolled back.

## Instructor OTP Verification

`POST /instructor/api/v1/verify-otp` is public and completes a 2FA challenge.

Request body:

```json
{
  "code": "012345",
  "token": "otp-challenge-token"
}
```

`code` must be exactly six digits, and `token` is trimmed before lookup. The
endpoint reads the challenge from Redis and verifies the submitted code against
`code_hash` with Argon2. A missing or malformed challenge returns
`400 {"detail": "Invalid or expired OTP token"}`. A challenge whose `exp` is in
the past returns `403 {"detail": "Invalid or expired OTP token"}` and is kept in
Redis so it can be replaced through the resend endpoint. An incorrect code
returns `400 {"detail": "Invalid OTP code"}` without consuming the challenge; an
unknown instructor returns `404 {"detail": "Instructor not found"}`.

On success the challenge is deleted before the login session is created, so it
cannot be replayed. The endpoint then stores
`{"id": "...", "email": "...", "role": "instructor"}`, sets the signed refresh
cookie, and returns `200 {"accessToken": "..."}` without redirecting. Redis
failures return `503 {"detail": "Authentication service unavailable"}`.

## Instructor OTP Resend

`POST /instructor/api/v1/resend-otp` is public.

Request body:

```json
{
  "token": "otp-challenge-token"
}
```

A missing or malformed challenge returns
`403 {"detail": "otp verification session expired"}`. A challenge that has not
expired returns `403 {"detail": "OTP has not expired"}` without changing the
stored code. Once `exp` is in the past, the endpoint generates and hashes a new
six-digit code, overwrites the same token with
`{"instructor_id": "...", "code_hash": "...", "exp": now + 120}`, sets a
24-hour TTL, queues a new `otp` email notification and outbox row in one
transaction, and returns `200 {"message": "OTP resent successfully"}`. An
unknown instructor returns `404 {"detail": "Instructor not found"}`; Redis
failures return `503 {"detail": "Authentication service unavailable"}`.

## Instructor Google Authentication

`GET /instructor/api/v1/google?code=...&state=...` is public and is the
browser-facing instructor OAuth callback. It exchanges the code and fetches the
Google userinfo using instructor-local helpers. If no instructor exists, a new
one is created with `is_email_verified=true`, `auth_method=google`, the default
`instructor` role, and no password hash. Existing unverified instructors are
marked verified without changing their `auth_method`.

When 2FA is enabled, Google login creates the same hashed OTP challenge as
password login and redirects to `{APP_HOST}/otp-verification?token=...`; no
session or refresh cookie is created. Otherwise it creates the instructor
session, sets the signed refresh cookie, and redirects to
`{APP_HOST}/dashboard`.

Any failure, including a missing code, HTTP error, invalid userinfo response,
database error, or Redis error, redirects with `302` to
`{APP_HOST}/{state}?msg=google_authentication_failed`. A request without
`state` is rejected with `422`.

## Instructor Current Session

`GET /instructor/api/v1/me` requires a Bearer access token and returns the
authenticated instructor's Redis session identity:

```json
{
  "id": "0f8fad5b-d9cb-469f-a165-70867728950e",
  "email": "ada@example.com"
}
```

The `role` field is not returned. No database lookup is performed. Missing,
invalid, or incomplete sessions return `401`, including a session without
non-empty string `id` and `email` values or a legacy session that only contains
`userId`.

## Instructor Logout

`POST /instructor/api/v1/logout` requires a Bearer access token. It deletes
`session:<sessionId>`, clears the refresh cookie with `Max-Age=0`, and returns
`200 {"message": "Logged out successfully"}`. Redis failures return
`503 {"detail": "Logout service unavailable"}` and do not claim success.

## Instructor Email Verification

`PATCH /instructor/api/v1/verify-email` is public.

Request body:

```json
{
  "token": "verification-token"
}
```

The token is read from Redis and resolved to an instructor UUID. Invalid or
expired tokens return `400 {"detail": "Invalid or expired verification token"}`;
a missing instructor returns `404 {"detail": "Instructor not found"}`. On
success the instructor's `is_email_verified` flag is set to true, the token is
deleted, and the endpoint returns
`200 {"message": "Email verified successfully"}`. Already-verified accounts
receive the same response. Redis failures return `503`.

## Instructor Password Reset

### Request a reset email

`POST /instructor/api/v1/email` is public.

Request body:

```json
{
  "email": "ada@example.com"
}
```

The endpoint always returns
`200 {"message": "If the email is registered, a password reset link has been sent."}`
to avoid account enumeration. When the instructor exists, it generates a
300-second (5-minute) token, builds `{APP_HOST}/forgot-password?token={token}`,
and queues a `forgot_password` notification plus outbox row.

### Reset the password

`PATCH /instructor/api/v1/password-reset` is public.

Request body:

```json
{
  "password": "NewStrong2!",
  "confirm_password": "NewStrong2!",
  "token": "password-reset-token"
}
```

Passwords follow the same policy as registration and must match. Invalid or
expired tokens return
`400 {"detail": "Invalid or expired password reset token"}`; a missing
instructor returns `404 {"detail": "Instructor not found"}`. On success the new
Argon2 hash is stored, the email is marked verified if necessary, the token is
deleted, and the endpoint returns
`200 {"message": "Password reset successfully"}`. Redis failures return
`503 {"detail": "Password reset service unavailable"}`.

## Student Account Flags

The `student` table includes `is_suspended` and `is_verified`. Both are non-null
booleans that default to `false`. No route currently changes or enforces them.

## Verification Model

The verification service defines one SQLModel table, `verification`. No
verification routes are exposed yet.

- UUID `id` primary key
- Required indexed foreign key `instructor_id` → `instructor.instructor_id`
- Required JSON `id_verification` and `academic_verification` objects
- `status` enum with `pending`, `verified`, and `rejected` values; defaults to
  `pending`
- Nullable `verified_at` datetime and `verified_by` UUID
- Nullable `rejected_reason` stored as SQL `Text`
- Standard `created_at` and `updated_at` timestamps

`verified_by` currently has no database foreign key because no Admin model
exists yet. It is ready to become a FK once one is added. Model-level validation
is intentionally omitted.

## Email Verification

`PATCH /student/api/v1/verify-email` is public.

Request body:

```json
{
  "token": "verification-token"
}
```

The token is read from Redis and resolved to a student UUID. Invalid or expired
tokens return `400`; a missing student returns `404`. On success the student's
`is_email_verified` flag is set to true, the token is deleted, and the endpoint
returns `200 {"message": "Email verified successfully"}`. Already-verified
accounts receive the same response.

## Password Reset

### Request a reset email

`POST /student/api/v1/email` is public.

Request body:

```json
{
  "email": "ada@example.com"
}
```

The endpoint always returns
`200 {"message": "If the email is registered, a password reset link has been sent."}`
to avoid account enumeration. When the student exists, it generates a
300-second (5-minute) token, builds `{APP_HOST}/forgot-password?token={token}`,
and queues a `forgot_password` notification plus outbox row.

### Reset the password

`PATCH /student/api/v1/password-reset` is public.

Request body:

```json
{
  "password": "NewStrong2!",
  "confirm_password": "NewStrong2!",
  "token": "password-reset-token"
}
```

Passwords must be 8-128 characters and contain uppercase, lowercase, numeric,
and special characters. Invalid or expired tokens return `400`; a missing
student returns `404`. On success the new Argon2 hash is stored, the email is
marked verified if necessary, the token is deleted, and the endpoint returns
`200 {"message": "Password reset successfully"}`.

Redis failures for the login, verification, and reset flows return `503`.

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

`TemplateManager` accepts either the string values `otp`, `email_verification`,
and `forgot_password` or their `TemplateType` enum members. `get_template()`
returns a frozen `template` dataclass with `body`, `version_number`, and
`template_path` fields.

Templates are registered in `template_registery`:

```python
template_registery = {
    TemplateType.EMAIL_VERIFICATION: {
        "template_dir": "templates/email/email_verification",
        "template": "v1.html",
    },
    TemplateType.FORGOT_PASSWORD: {
        "template_dir": "templates/email/forgot_password",
        "template": "v1.html",
    },
}
```

`template_dir` contains the versioned HTML files, and `template` pins the version
used by `get_template()`. `version_number` is derived from the pinned filename,
so `v1.html` returns `1`. Template paths are resolved relative to `backend/`.

The email verification and forgot-password templates render `{{ link }}` as the
`href` value. The OTP template renders `{{ otp_code }}` in its message body.
`template_build()` is the static Jinja rendering helper used by
`get_template()`.

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

`WebsocketNotificationManager` tracks multiple sockets per user ID and sends
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
`notification.process_notification` Celery task. Registration notifications are
dispatched through the transactional `outbox` table described below.

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
   countdown and preserves the custom `x-retries` header.
3. Loads the notification.
4. Returns if the status is not `pending`, so `processed` and `failed`
   notifications are never delivered again.
5. Resolves the notification provider and calls `provider.process(notification)`.
6. On success, marks the notification `processed`.
7. On `NotificationError`, rejects the message with `requeue=True`. When the
   task was published to RabbitMQ, the notification stays `pending` and
   RabbitMQ redelivers it until the `task` delivery limit is exhausted, then
   dead-letters it to `task.dlq`. When the task is invoked by the outbox
   dispatcher, the exception is returned to the outbox retry budget instead.

`max_retries=5` and `default_retry_delay=10` now apply only to lock contention.
They do not limit provider failures, because provider failures use the broker
delivery limit and the DLQ retry budget instead.

The process lock is released in a `finally` block, including when a retry or
reject is raised.

### Transactional outbox

The `outbox` table stores tasks that must be dispatched after the surrounding
database transaction commits:

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | Primary key |
| `status` | `pending`, `processed`, `failed` | Defaults to `pending` |
| `retry_after` | timezone-aware datetime or null | Earliest next attempt |
| `num_of_retries` | integer | Defaults to `0` |
| `payload` | JSON object | `{argument: {...}, task_name: "process_notification"}` |
| `table` | string | Referenced table name, for example `notification` |
| `ref_id` | string | Referenced row ID |

`outbox.process_outbox` runs every 10 seconds through Celery Beat. Each run
selects up to 50 pending rows whose `retry_after` is null or in the past:

1. Acquire `outbox:process:<id>` in Redis with a 60-second TTL.
2. Resolve `payload["task_name"]` through
   `celery_app/task_registry.py`.
3. Call the registered function with `payload["argument"]` as keyword
   arguments. Synchronous Celery tasks are invoked in a worker thread so their
   internal `asyncio.run` does not conflict with the outbox event loop.
4. On success, set the outbox row to `processed` and commit.
5. On failure, schedule the next attempt at
   `utc_now() + 10 seconds * attempt`, up to five attempts.
6. On the fifth failure, set the outbox row to `failed`, clear `retry_after`,
   and mark the referenced notification `failed` in the same transaction.

The Redis lock is always released in a `finally` block. If the lock is already
held, the row is skipped without consuming its retry budget.

Run the scheduler in addition to the worker:

```powershell
.venv\Scripts\celery.exe -A celery_app beat --loglevel=INFO --schedule=/tmp/celerybeat-schedule
```

### Queue topology

- `task` - main quorum queue with `x-delivery-limit=5`; dead-letters to
  `task.dlx` with routing key `task.dlq`.
- `task.dlq` - quorum dead-letter queue with no delivery limit. It is consumed
  by `dlq-consumer`.
- `task.retry` - quorum delay queue with no consumer,
  `x-message-ttl=300000`, and `x-dead-letter-strategy=at-least-once`. After five
  minutes it dead-letters messages back to the `task` exchange.
- All exchanges are topic exchanges. Celery can use RabbitMQ native delayed
  delivery for retry countdowns instead of holding messages in a worker.
- `task_acks_late=True`, `task_reject_on_worker_lost=True`, and
  `task_acks_on_failure_or_timeout=False` make worker crashes requeue messages
  so the delivery limit eventually moves poison messages to the DLQ.
- The worker declares all three queues before consuming and only consumes
  `task`:

  ```powershell
  .venv\Scripts\celery.exe -A celery_app worker --loglevel=INFO --queues=task
  ```

### DLQ consumer

`python -m dlq.consumer` consumes `task.dlq` and uses the custom `x-retries`
header:

1. First DLQ delivery (header missing): set `x-retries=1`, republish to
   `task.retry`, then acknowledge.
2. Header present and `x-retries + 1 < 3`: increment `x-retries`, republish to
   `task.retry`, then acknowledge.
3. Header present and `x-retries + 1 >= 3`: call the terminal handler
   `mark_notif_failed(notification_id)`, then acknowledge. The handler takes a
   row lock and changes only `pending` notifications to `failed`;
   `processed` and `failed` notifications are left unchanged.

Republishing strips RabbitMQ death headers (`x-death` and the
`x-first-death-*`/`x-last-death-*` headers), clears `eta`/`expires`, resets
Celery's `retries` header to `0`, and preserves `x-retries`. This avoids
RabbitMQ cycle detection and resets the per-cycle delivery count.

The final failure normally happens on the third DLQ arrival, after two real
five-minute waits in `task.retry`. Within each cycle the task is redelivered up
to `x-delivery-limit=5` times before RabbitMQ dead-letters it, so a terminal
notification can take roughly ten minutes to reach `failed`.

Unknown task names, malformed bodies, invalid retry headers, invalid
notification IDs, and arguments that do not match the handler are logged and
acknowledged as poison messages. Handler, database, and publish failures are
logged, throttled for five seconds, and rejected with `requeue=True`. The
consumer enables publisher confirms and acknowledges only after the retry
publish succeeds or the terminal handler commits.

Student and instructor registration are the current outbox producers. Other
features can insert an `Outbox` row in the same transaction as their business
write and let the Beat job dispatch the registered task.

## API

| Method | Path | Description | Authentication |
|--------|------|-------------|----------------|
| `GET` | `/health` | Health check | Public |
| `POST` | `/student/api/v1/register` | Register a student | Public |
| `POST` | `/instructor/api/v1/register` | Register an instructor | Public |
| `POST` | `/instructor/api/v1/login` | Log in or start instructor 2FA | Public |
| `POST` | `/instructor/api/v1/verify-otp` | Verify an instructor OTP challenge | Public |
| `POST` | `/instructor/api/v1/resend-otp` | Replace an expired instructor OTP challenge | Public |
| `GET` | `/instructor/api/v1/google` | Log in with a Google OAuth code and state | Public |
| `GET` | `/instructor/api/v1/me` | Return the current instructor session identity | Bearer token |
| `POST` | `/instructor/api/v1/logout` | Delete the instructor session and clear the refresh cookie | Bearer token |
| `PATCH` | `/instructor/api/v1/verify-email` | Verify an instructor email address | Public |
| `POST` | `/instructor/api/v1/email` | Request an instructor password-reset email | Public |
| `PATCH` | `/instructor/api/v1/password-reset` | Reset an instructor password | Public |
| `POST` | `/instructor/api/v1/profile` | Create the authenticated instructor's profile | Bearer token |
| `POST` | `/student/api/v1/login` | Log in and issue tokens | Public |
| `GET` | `/student/api/v1/google` | Log in with a Google OAuth code and state | Public |
| `GET` | `/student/api/v1/me` | Return the current session identity | Bearer token |
| `POST` | `/student/api/v1/logout` | Delete the session and clear the refresh cookie | Bearer token |
| `GET` | `/student/api/v1/profile` | Return the current student's profile | Bearer token |
| `PATCH` | `/student/api/v1/profile` | Update names and replace the profile picture | Bearer token |
| `PATCH` | `/student/api/v1/verify-email` | Verify an email address | Public |
| `POST` | `/student/api/v1/email` | Request a password-reset email | Public |
| `PATCH` | `/student/api/v1/password-reset` | Reset a password | Public |
| `POST` | `/auth/api/v1/token-refresh` | Rotate tokens | Refresh cookie |
| `GET` | `/docs` | FastAPI documentation | Bearer token |
| `GET` | `/openapi.json` | OpenAPI schema | Bearer token |

## Testing

```powershell
.venv\Scripts\python -m pytest
```

The suite covers authentication, login and token issuance, Google OAuth and
required state-based failure redirects, current-session identity, logout and
refresh-cookie clearing, profile lookup, profile name updates, profile-picture
validation and UUID object keys, MinIO upload and overwrite behavior, instructor
account/profile/education/experience/payment model defaults and constraints,
instructor registration validation, instructor login and 2FA OTP challenges,
instructor OTP verification and resend, code hashing and expiry/TTL behavior,
instructor Google login and 2FA redirects, instructor email verification,
instructor password reset, instructor profile creation and picture upload,
instructor current-session and logout routes, role defaults and the shared
session contract,
token TTL and cleanup, duplicate-email, Redis and transaction failure handling,
verification model defaults and constraints, student suspension/verification
defaults, storage failure handling, email verification, password reset, CORS,
caching, rate limiting, refresh-token rotation, request logging, PostgreSQL
configuration, session cleanup, password hashing, Redis token creation, student
auth-method defaults, notification model defaults, Celery queue topology, lock
handling, retry behavior, outbox dispatch and retry budgeting, DLQ republish and
terminal failure handling, provider dispatch, email template rendering, SendGrid
error handling, Kafka producer publishing, Kafka consumer dispatch and commits,
Termii SMS delivery, WebSocket authentication and connection management, and
processed-notification caching.

## Known Limitations

- Password reset does not invalidate existing access or refresh sessions.
- OTP challenges created before the hashed-challenge change use the old
  plaintext `otp_code` payload and are rejected; affected instructors must log
  in again.
- There is no general-purpose notification enqueue endpoint. New producers
  should insert an `Outbox` row in the same transaction as their business
  write.
- Kafka is not included in the Compose stack. Set `KAFKA_BOOTSTRAP_SERVERS` to
  an existing broker for push delivery and set `KAFKA_CONSUMER_ENABLED=true`
  to deliver Kafka messages to WebSocket clients.
- SendGrid, Termii, and Kafka configuration defaults are placeholders and must
  be replaced before production use.
- There is no migration framework. Existing databases need the `notification`,
  `outbox`, `instructor`, `instructor_profile`, `education`, `experience`,
  `payment_info`, and `verification` tables created manually because
  `AUTO_CREATE_TABLES` only creates missing tables on a fresh database. Existing
  `student` tables also need `is_suspended` and `is_verified` boolean columns
  added with `NOT NULL DEFAULT false`, plus a `role` column added with
  `VARCHAR(20) NOT NULL DEFAULT 'student'`. Existing `instructor` tables need a
  `role` column added with `VARCHAR(20) NOT NULL DEFAULT 'instructor'`. Existing
  `instructor_profile` tables need `linkedin_url` dropped, nullable JSON
  `social_links` added, `company` made nullable, and `bio` changed to
  `VARCHAR(1000)`. If an earlier schema exists, drop the unused `release_after`
  column manually.

## License

ISC
