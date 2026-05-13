# xboxlive-protect API — Authentication

## How it works

xboxlive-protect uses server-side sessions stored in SQLite. Logging in sets
an `xblp_session` cookie (HttpOnly, SameSite=Strict, Secure). The cookie value
is a 64-character random hex string that maps to a row in the `sessions` table.
There are no API keys and no JWTs; this is a single-user appliance.

Sessions have sliding expiration: each authenticated request extends the session
by another 30 days from now. An idle session expires after 30 days.

## Default credentials

On a fresh install the API seeds one user automatically:

| Field    | Value               |
|----------|---------------------|
| Username | `admin`             |
| Password | `xboxlive-protect`  |

The seeded account has `must_change_password = true`. Every endpoint except the
four listed below returns **403** with `{"error": "password_change_required"}`
until the password is changed. This gate ensures the first thing a user does
is set a real password.

**Endpoints exempt from the gate** (work with the default password):

| Method | Path                    | Auth required? |
|--------|-------------------------|----------------|
| POST   | `/api/v1/auth/login`    | No             |
| POST   | `/api/v1/auth/logout`   | Yes            |
| GET    | `/api/v1/auth/me`       | Yes            |
| POST   | `/api/v1/auth/password` | Yes            |

## Changing the password

```
POST /api/v1/auth/password
Content-Type: application/json

{"old_password": "xboxlive-protect", "new_password": "your-new-password"}
```

A successful change (204 No Content):
- Sets `must_change_password = false` on the user row
- Revokes all *other* active sessions (the current session stays valid)
- Writes a `password_changed` entry to the audit log

## Endpoints

### POST /api/v1/auth/login

Request body: `{"username": "admin", "password": "..."}`

Success: 200 with `{"username": "admin", "must_change_password": true/false}`.
Sets `xblp_session` cookie.

Failure: 401 for unknown user or wrong password. Both cases write a
`login_failed` entry to the audit log. No lockout or rate limiting is applied
(see DESIGN.md §6.2–6.3).

### POST /api/v1/auth/logout

Revokes the current session and clears the cookie. Returns 204 No Content.

### GET /api/v1/auth/me

Returns `{"username": "...", "must_change_password": true/false}` for the
currently authenticated user. Returns 401 if not authenticated.

### POST /api/v1/auth/password

See "Changing the password" above.

## Session cookie attributes

| Attribute    | Value                                              |
|--------------|----------------------------------------------------|
| Name         | `xblp_session`                                     |
| HttpOnly     | Yes                                                |
| SameSite     | Strict                                             |
| Secure       | Yes (production); controlled by `XBLP_COOKIE_SECURE` |
| Max-Age      | 30 days (configurable via `XBLP_SESSION_LIFETIME_DAYS`) |

## Development (Windows / HTTP)

On Windows the session cookie's Secure flag is off by default and nftables
is skipped. Set the following env vars (or put them in a `.env` file in the
working directory):

```
XBLP_COOKIE_SECURE=false
XBLP_NFT_ENABLED=false
XBLP_DB_PATH=state.db
```

Then run:

```
python -m xblp_api
```

The API listens on `http://localhost:8080` by default.

## "Log out everywhere"

Not yet exposed as an endpoint (planned for Stage 2 settings routes), but the
underlying `revoke_all_for_user` function is already implemented.
