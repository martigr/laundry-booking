# Developer Guide

How the laundry booking app is built, and how to change it safely.
For installation and operation see the [README](../README.md).

## Overview

A small Flask app for one shared washing machine in an apartment building.
Residents log in, see a week (or month) calendar with three slots per day
and book a free slot with one click. There are no quotas: first come, first
served. Admins manage users and the booking horizon.

- **Backend:** one module, `laundery/app.py` (Flask, SQLite, no ORM)
- **Frontend:** server-rendered Jinja templates, one stylesheet, no build step
  and practically no JavaScript (only `confirm()` dialogs)
- **UI language:** German. Code, comments, commit messages and docs: English.

## Project layout

```
laundery/                 # app root (folder name is a historical typo, keep it:
│                         #  Dockerfile and docs depend on it)
├── app.py                # config, DB schema, auth, routes, CLI
├── seed.py               # demo users for local testing
├── static/style.css      # all styles (design tokens as CSS variables)
└── templates/
    ├── base.html         # layout: header, nav, flash messages
    ├── _macros.html      # slot_cell(): renders one slot (book / cancel / static)
    ├── _logo.html        # inline SVG logo
    ├── index.html        # week view + side cards
    ├── month.html        # month overview (links into week view)
    ├── login.html        # overrides block body (no header)
    ├── profile.html      # display name, password
    └── admin.html        # users, settings
tests/
├── conftest.py           # puts laundery/ on sys.path, temp DATABASE
└── test_app.py           # pytest suite (Flask test client)
docs/DEVELOPMENT.md       # this file
```

## Running locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cd laundery
python seed.py            # demo users: alice / password123 (admin), bob / password456
python app.py             # http://localhost:5000
```

Run the tests from the repository root with `pytest`.

## Configuration

All configuration comes from environment variables (read at import time):

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | insecure placeholder (warning logged) | Session signing |
| `DATABASE` | `./laundry.db` | SQLite file |
| `APP_TIMEZONE` | `Europe/Zurich` | Defines "today"/"now" for past/running slots |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | – | Create the first admin if the DB has no users |
| `SESSION_COOKIE_SECURE` | `0` | Set `1` behind HTTPS |
| `SESSION_COOKIE_SAMESITE` | `Lax` | Cookie SameSite policy |
| `LOG_LEVEL` | `INFO` | Logging |
| `PORT` | `5000` | Dev server only |

## Data model

```
users                                   reservations
─────                                   ────────────
id            INTEGER PK                id        INTEGER PK
username      TEXT UNIQUE               day       TEXT  ISO date 'YYYY-MM-DD'
password      TEXT  werkzeug hash       slot      TEXT  one of SLOTS names
display_name  TEXT  shown in calendar   user_id   INTEGER → users.id
is_admin      INTEGER 0/1               UNIQUE (day, slot)

settings
────────
key   TEXT PK      e.g. 'horizon_weeks'
value TEXT         defaults in DEFAULT_SETTINGS
```

- `UNIQUE (day, slot)` is the only guard against double bookings. `book()`
  relies on the `IntegrityError` instead of checking first, so two
  simultaneous clicks cannot both succeed.
- Slots are stored by **name**. `SLOTS` in `app.py` maps names to start/end
  times. Renaming a slot orphans existing rows, so add a migration if you do.
- Deleting a user also deletes all of their reservations.

### Schema initialisation and changes

`init_db()` runs on import (so it also runs under Gunicorn, once per worker).
It opens one `BEGIN IMMEDIATE` transaction, which takes SQLite's write lock,
so parallel workers run it one after another. Inside it:

1. `CREATE TABLE IF NOT EXISTS` for the current schema,
2. bootstrap the first admin from env vars.

There are no migrations yet. Once the app holds real data, a schema change
needs an idempotent migration step inside `init_db()` (it runs on every
start) plus a test that builds the old schema by hand and checks the result.

## Request flow

- `before_request` hooks: `check_csrf()` (every POST) and `load_user()` sets
  `g.user` (a `sqlite3.Row` or `None`).
- Decorators: `@login_required` redirects to `/login`; `@admin_required`
  also returns 403 for non-admins.
- State-changing actions are plain HTML form POSTs that answer with a redirect
  (Post/Redirect/Get). Feedback goes through `flash(message, 'ok' | 'warn')`,
  rendered in `base.html`.
- After book/cancel, `back_to_calendar()` returns to the form's `next` field
  (local paths only, so it can't be used as an open redirect).

### Routes

| Route | Method | Access | Purpose |
|---|---|---|---|
| `/login`, `/logout` | GET/POST, GET | public | Session login; `remember` makes the session permanent (30 days) |
| `/` | GET | user | Week view; `?week=YYYY-MM-DD` (any day of the week) |
| `/month` | GET | user | Month overview; `?month=YYYY-MM` |
| `/book` | POST | user | `day`, `slot`, `next` |
| `/cancel/<id>` | POST | owner or admin | Cancel a booking that has not ended |
| `/profile` | GET/POST | user | `action=name` or `action=password` |
| `/admin` | GET | admin | Users and settings |
| `/admin/users` | POST | admin | Create user |
| `/admin/users/<id>/reset` | POST | admin | New random password, shown once via flash |
| `/admin/users/<id>/delete` | POST | admin | Delete user and their bookings (not yourself) |
| `/admin/settings` | POST | admin | `horizon_weeks` (1–52) |
| `/health` | GET | public | Liveness probe |

### Booking rules

All of them live in `book()` and `cancel()`; the UI only mirrors them.

- A slot is **past** once its end time is reached. It can't be booked or
  cancelled. A **running** slot can still be booked if free.
- Booking is allowed up to `today + horizon_weeks` (`last_bookable_day()`).
- Users cancel their own bookings; admins can cancel any.
- "Now" always comes from `now()`, which uses `APP_TIMEZONE`. Tests
  monkeypatch it; never call `datetime.now()` directly.

### Calendar rendering

`build_cell()` turns (day, slot, reservation) into a dict that the template
consumes: `state` (`free | mine | taken | past | locked`), `bookable`,
`cancelable`, `running`, display times. The `slot_cell` macro picks the
markup: a booking form, a cancel form, or a plain `<div>`. Keep the decisions
in `build_cell()` and keep the templates free of logic.

`sidebar_info()` computes the "Jetzt in der Waschküche" card, the next free
slot within the horizon and the user's upcoming bookings.

## Security

- **CSRF:** every POST must carry `csrf_token` (`{{ csrf_token() }}` in the
  form). The token lives in the session and is checked with `compare_digest`.
  Each new form needs the hidden field, or it fails with a 400.
- **Session fixation:** `session.clear()` on login.
- **Escaping:** Jinja autoescape covers HTML. For values inside inline JS
  (e.g. `onsubmit` confirm texts) use `|tojson` in a *single-quoted*
  attribute; `|e` is not safe there. `test_display_name_is_escaped` covers this.
- **Passwords:** werkzeug hashes, minimum length `MIN_PASSWORD_LENGTH`.

## Frontend

- Design tokens are CSS variables in `:root` of `style.css` (colours, radius).
  Blue = your booking, grey = someone else's, dashed = free, orange = today and
  running slot.
- Layout: flexbox/grid. The week grid has 7 columns on desktop and switches at
  `max-width: 860px` to one row per day with the three slots next to each other.
- System font stack, no external requests (no CDNs, no Google Fonts).
- Touch targets are at least 44 px; use real `<button>`/`<a>` elements and
  `aria-label` on icon-only buttons.

## CLI

```bash
cd laundery
flask --app app create-user <username> [--name "Display name"] [--admin]
flask --app app make-admin <username>
```

## Testing

`tests/test_app.py` uses a temp DB per test, two users (`alice` admin, `bob`)
and a fixed time (Thursday 2026-10-15 14:20). Helpers:

- `token(client)` – fetches the CSRF token from the session
- `login(client, username)` – logs in and returns a fresh token
- `reservations()` – current bookings as `(day, slot, username)` tuples

Add a test for every new route or rule, including the 400/403 paths.

## Deployment

The `Dockerfile` runs Gunicorn with 4 workers on port 5000. Mount a volume
and point `DATABASE` at it, or the data is lost with the container. Set
`SECRET_KEY`, and `SESSION_COOKIE_SECURE=1` when served over HTTPS.
