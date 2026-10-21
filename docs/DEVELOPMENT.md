# Developer Guide

How the laundry booking app is built, and how to change it safely.
For installation and operation see the [README](../README.md).

## Overview

A small Flask app for one shared washing machine in an apartment building.
Residents log in, see a week (or month) calendar with three slots per day
and book a free slot (or a whole day) with one click. There are no quotas: first come, first
served. Admins manage users and the booking horizon.

- **Backend:** Python package `laundry/` built with an app factory and blueprints;
  SQLite through the standard library, no ORM
- **Frontend:** server-rendered Jinja templates, one stylesheet, no build step
  and practically no JavaScript (only the confirmation dialog in `static/confirm.js`
  and the live password-repeat check in `static/profile.js`); no inline scripts, the CSP forbids them
- **UI language:** German. Code, comments, commit messages and docs: English.

## Project layout

```
laundry/                  # the application package
├── __init__.py           # create_app(): config, blueprints, /health, schema setup
├── db.py                 # connection per request, schema, init_db(), settings
├── users.py              # create_user(), get_user*(), set_password(), clean_email(), UserError
├── mail.py               # is_configured(), deliver(): one plain-text mail over SMTP
├── notifications.py      # outbox: queue_*() for reminders/freed slots/password reset, run_once()
├── slots.py              # SLOTS, now() (the only clock), date formatting, booking horizon
├── auth.py               # bp "auth": login/logout, login throttling, CSRF, login_required/admin_required
├── security.py           # security headers (CSP etc.), /.well-known/security.txt
├── accesslog.py          # Gunicorn logger class that blanks tokens of mail links in the access log
├── bookings.py           # bp "bookings": week/month view, book (slot or day), cancel
├── profile.py            # bp "profile": display name, mail settings, calendar link, password; confirm address, unsubscribe
├── ical.py               # bp "ical": the user's own bookings as an iCalendar feed (calendar subscription)
├── admin.py              # bp "admin": users, settings
├── cli.py                # flask commands: create-user, make-admin, seed-demo, send-notifications, backup
├── static/style.css      # all styles (design tokens as CSS variables)
├── static/confirm.js     # styled confirmation dialog for forms with data-confirm
├── static/profile.js     # live "passwords don't match" hint on the profile page
└── templates/
    ├── base.html         # layout: header, nav, flash messages, confirmation <dialog>
    ├── _macros.html      # slot_cell(): renders one slot (book / cancel / static)
    ├── _logo.html        # inline SVG logo
    ├── index.html        # week view + side cards
    ├── month.html        # month overview (links into week view)
    ├── login.html        # overrides block body (no header)
    ├── profile.html      # display name, mail notifications, password
    ├── admin.html
    ├── auth_base.html    # layout of the logged-out pages (login, forgot/reset password, unsubscribe)
    ├── forgot_password.html, reset_password.html, unsubscribe.html, confirm_email.html
tests/
├── conftest.py           # fixtures: app, client, login, csrf, query, reservations
├── test_auth.py          # login, logout, CSRF, SECRET_KEY, profile
├── test_bookings.py      # booking rules, cancel permissions, views, escaping
├── test_admin.py         # admin area
├── test_cli.py           # CLI commands, first-admin bootstrap
├── test_ical.py          # calendar feed: content, time zones, link create/renew/delete, 404s
├── test_notifications.py # reminders, freed slots, password reset, unsubscribe, retries, SMTP settings
└── test_security.py      # headers, input limits, redirects, login throttling, sessions, security.txt, backup
docker-entrypoint.sh      # fixes /data ownership, drops root (PUID/PGID)
docker-compose.yml        # web + notifier container, settings from .env (.env.example)
SECURITY.md               # how to report vulnerabilities
scripts/docker-smoke-test.sh  # starts a built image and checks health, user, volumes (CI)
pyproject.toml            # project metadata, pytest and ruff config
requirements.txt          # pinned runtime dependencies (installed by the Dockerfile)
requirements-dev.txt      # + pytest, ruff
requirements-audit.txt    # pip-audit, only for the audit workflow
.github/workflows/ci.yml  # ruff, pytest, shellcheck + Docker build and smoke test
.github/workflows/release.yml  # on tag vX.Y.Z: CI, image to ghcr.io, GitHub release
.github/workflows/audit.yml  # pip-audit of the pinned dependencies (also weekly)
.github/dependabot.yml    # weekly update PRs for pip packages and GitHub Actions
.github/workflows/pages.yml  # deploys site/ + docs/screenshots to GitHub Pages
site/index.html           # project page (GitHub Pages), self-contained HTML/CSS
```

## Running locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
flask --app laundry --debug seed-demo
flask --app laundry --debug run
```

`--debug` matters: without it, `create_app()` refuses to start without a `SECRET_KEY`.
That rule protects production from running with a known key.

### Demo data vs. test data

`seed-demo` is a **development tool**. It fills your local database with demo users
and a few bookings around the current week, so you have something to click through.
It is idempotent and safe to run repeatedly.

Tests do **not** use it. Every test gets a fresh temporary database with exactly two
users (`alice` admin, `bob`) and a fixed clock, created by the `app` fixture in
`tests/conftest.py`. Tests need a small, exact and stable state; demo data is meant to
look realistic and may change. Both use the same `users.create_user()`, so no code is
duplicated.

## Configuration

`create_app(test_config=None)` reads the environment (see the README for the table) and
then applies `test_config` on top. Config keys: `SECRET_KEY`, `DATABASE`, `TIMEZONE`,
`ADMIN_USERNAME`, `ADMIN_PASSWORD`, the Flask session cookie settings, `MAX_CONTENT_LENGTH` (64 KB),
`TRUSTED_PROXIES` (wraps the app in werkzeug's `ProxyFix` when > 0), `SECURITY_CONTACT`, `BASE_URL`
and the `SMTP_*`/`MAIL_*` settings.

## Data model

```
users                                   reservations
─────                                   ────────────
id            INTEGER PK                id        INTEGER PK
username      TEXT UNIQUE (lowercase)   day       TEXT  ISO date 'YYYY-MM-DD'
password      TEXT  werkzeug hash       slot      TEXT  one of SLOTS names
display_name  TEXT  shown in calendar   user_id   INTEGER → users.id ON DELETE CASCADE
is_admin      INTEGER 0/1               UNIQUE (day, slot)
email         TEXT  optional
email_verified INTEGER 0/1  mails only go out when 1
reminder_minutes INTEGER 0 = off
notify_freed  INTEGER 0/1
session_version INTEGER  +1 on every password change
calendar_token TEXT UNIQUE  secret of the calendar feed URL, NULL = none

notifications (outbox)
──────────────────────
dedupe_key  TEXT UNIQUE   e.g. 'reminder:<reservation id>', 'freed:<reservation id>:<user id>'
to_addr, subject, body
created_at, expires_at    not sent after expires_at (slot start, reset link validity)
sent_at, attempts, last_error

login_failures (failed logins of the last 15 minutes)
──────────────
key   TEXT   'user:<username>' or 'ip:<address>'
at    TEXT   ISO time; older rows are deleted on the next failure

settings
────────
key   TEXT PK      e.g. 'horizon_weeks'
value TEXT         defaults in db.DEFAULT_SETTINGS
```

- `UNIQUE (day, slot)` is the only guard against double bookings. `book()` relies on
  the `IntegrityError` instead of checking first, so two simultaneous clicks cannot
  both succeed.
- Usernames are stored lowercase and trimmed; login is therefore case-insensitive.
- Slots are stored by **name**. `slots.SLOTS` maps names to times. Renaming a slot
  orphans existing rows.
- `PRAGMA foreign_keys = ON` is set on every connection, so deleting a user deletes
  their bookings.

### Database access

- `db.get_db()` returns the connection of the current request (stored on `g`, closed on
  teardown). Use `with get_db() as con:` for writes; it commits on success and rolls
  back on errors.
- `db.init_db()` runs inside `create_app()`, so once per Gunicorn worker. It runs in a
  `BEGIN IMMEDIATE` transaction, which takes SQLite's write lock, so parallel workers run
  it one after another. It creates the schema (`CREATE TABLE IF NOT EXISTS`) and the first
  admin from `ADMIN_USERNAME`/`ADMIN_PASSWORD` on an empty database. WAL mode is enabled
  once and persists in the file.
- Migrations: columns added after the first release are listed in `db.ADDED_COLUMNS`;
  `init_db()` adds each one that is missing (`ALTER TABLE ... ADD COLUMN`). New tables just go
  into `SCHEMA` (`CREATE TABLE IF NOT EXISTS`). Other changes need their own idempotent step in
  `init_db()`. Always add a test that builds the old schema by hand
  (`test_columns_are_added_to_an_existing_database`).

## Request flow

- `before_request` hooks (registered by `auth.init_app`): `check_csrf()` for every POST,
  then `load_user()`, which sets `g.user` (a `sqlite3.Row` or `None`) and ends the
  session if the user was deleted or the password changed since login (`session['sv']` no longer
  matches `users.session_version`).
- `after_request`: `security.add_headers()` sets the security headers on every response.
- Decorators: `@login_required` redirects to the login page; `@admin_required` also
  returns 403 for non-admins.
- State-changing actions are plain HTML form POSTs that answer with a redirect
  (Post/Redirect/Get). Feedback goes through `flash(message, 'ok' | 'warn')`.
- After book/cancel, `back_to_calendar()` returns to the form's `next` field. Only local
  paths are accepted; `//host`, anything with a backslash or a control character (browsers drop
  tabs and line breaks, so `/<tab>/host` would become `//host`) and absolute URLs fall back to `/`.
- Dates from requests go through `slots.parse_day()`, which only accepts `MIN_DAY..MAX_DAY`
  (2000–2999), so the calendar arithmetic can't overflow.

### Routes

| Route | Method | Endpoint | Access | Purpose |
|---|---|---|---|---|
| `/login` | GET/POST | `auth.login` | public | `remember` makes the session last 30 days; 429 when throttled |
| `/logout` | POST | `auth.logout` | public | POST so other sites can't log you out |
| `/` | GET | `bookings.index` | user | Week view; `?week=YYYY-MM-DD` (any day of the week) |
| `/month` | GET | `bookings.month` | user | Month overview; `?month=YYYY-MM` |
| `/book` | POST | `bookings.book` | user | `day`, `slot`, `next` |
| `/book-day` | POST | `bookings.book_day` | user | `day`, `next`; books all free slots of the day that haven't ended |
| `/cancel/<id>` | POST | `bookings.cancel` | owner or admin | Only before the slot has ended |
| `/cancel` | POST | `bookings.cancel_many` | owner or admin | `id` (repeated), `next`; cancels a block, skips ended slots |
| `/profile` | GET/POST | `profile.index` | user | `action=name`, `action=notifications` (a changed address needs confirming), `action=verify_email` (send the confirmation again), `action=calendar` (create or renew the calendar link), `action=calendar_delete`, or `action=password` with `old_password`, `new_password`, `new_password2` (must match) |
| `/admin` | GET | `admin.index` | admin | Users and settings |
| `/admin/users` | POST | `admin.create` | admin | Create user |
| `/admin/users/<id>/reset` | POST | `admin.reset_password` | admin | New random password (12 characters), shown once in the response itself (not flashed, so not in the cookie); ends the user's sessions |
| `/admin/users/<id>/delete` | POST | `admin.delete` | admin | Not yourself; bookings are deleted too |
| `/admin/settings` | POST | `admin.settings` | admin | `horizon_weeks` (1–52) |
| `/forgot-password` | GET/POST | `auth.forgot_password` | public, 404 without mail | Queues a reset mail (at most one per user in 15 min); same answer whether the account exists or not |
| `/reset-password/<token>` | GET/POST | `auth.reset_password` | token | Signed, valid 1 h, single use (contains part of the password hash) |
| `/verify-email/<token>` | GET/POST | `profile.confirm_email` | token | Signed, valid 7 days, only for the address it was sent to; GET shows a button, POST confirms |
| `/unsubscribe/<token>` | GET/POST | `profile.unsubscribe` | token | GET shows a button, POST switches the notification off |
| `/calendar/<token>.ics` | GET | `ical.feed` | token | The token owner's bookings as iCalendar; 404 for unknown tokens |
| `/health` | GET | `health` | public | Liveness probe |
| `/.well-known/security.txt` | GET | `security_txt` | public | RFC 9116; `SECURITY_CONTACT` plus the project's advisory link, `Expires` 180 days ahead |

### Booking rules

All of them live in `bookings.book()` and `bookings.cancel()`; the UI only mirrors them.

- A slot is **past** once its end time is reached. It can't be booked or cancelled.
  A **running** slot can still be booked if it is free.
- Booking is allowed up to `today + horizon_weeks` (`slots.last_bookable_day()`).
- **Whole day:** `book_day()` inserts each slot of the day that hasn't ended with
  `ON CONFLICT (day, slot) DO NOTHING` in one transaction. Slots someone else already has
  stay theirs; the flash message names them. No quota, like single slots.
- Users cancel their own bookings; admins can cancel any. `cancel_reservations()` is shared by
  `/cancel/<id>` and `/cancel`: 404 if any id is missing, 403 if any booking isn't yours.
- "Now" always comes from `slots.now()`, which uses the `TIMEZONE` config. Call it as
  `slots.now()` (module attribute), never `from .slots import now`, and never
  `datetime.now()`: the tests replace `slots.now` with a fixed time.

### Calendar rendering

`bookings.build_cell()` turns (day, slot, reservation) into a dict for the templates:
`state` (`free | mine | taken | past | locked`), `bookable`, `cancelable`, `running`
and the display times. The `slot_cell` macro picks the markup: a booking form, a cancel
form or a plain `<div>`. Keep decisions in `build_cell()`; keep the templates free of
logic. `build_days()` also sets `book_day`, the label of the day's whole-day button
("Ganzer Tag", or "Alle freien" if some slots are gone), or `None` when fewer than two
slots are bookable.

`bookings.sidebar_info()` computes the "Jetzt in der Waschküche" card, the next free
slot within the horizon and the user's upcoming bookings. `group_blocks()` merges
adjacent slots of the same day and user into one block ("Nachmittag – Abend",
"Ganzer Tag"), both in the upcoming list (one cancel button per block) and for the
running booking.

## Calendar subscription

`ical.py` serves a user's **own** bookings as an iCalendar feed (RFC 5545) at
`/calendar/<token>.ics`, so phones can subscribe to it (iOS natively, Android with ICSx⁵, Google
Calendar via its web UI). Other households' bookings are never in it.

- Calendar apps can't log in, so the URL carries a secret: `users.calendar_token`
  (`secrets.token_urlsafe(32)`), created in the profile. "Neuen Link erstellen" replaces it (the
  old URL answers 404), "Link löschen" sets it to NULL; deleting the user removes it too. It is a
  random value in the database rather than a signed token, so each user can revoke theirs without
  touching `SECRET_KEY`. It does not change with the password: the feed only shows bookings, and
  re-subscribing every device after each password change would be a nuisance.
- Content: bookings from 4 weeks ago (`KEEP_PAST`) on, adjacent slots merged with
  `bookings.group_blocks()` into one event. The `UID` is built from the reservation ids, so it
  changes when a block grows or shrinks; subscribed calendars are replaced as a whole on every
  refresh, so the old event disappears. Times are converted from the building's `TIMEZONE` to UTC
  (`…Z`), which avoids a `VTIMEZONE` block. `REFRESH-INTERVAL`/`X-PUBLISHED-TTL` suggest one
  hour; Apple respects it, Google refreshes when it likes (often only daily).
- Written by hand (escaping, 75-octet line folding, CRLF) instead of adding a dependency.
- The profile shows the `https://` link (with a copy button from `static/profile.js`, hidden
  where `navigator.clipboard` is missing) and a `webcal://` link that opens "subscribe" on iOS.
  Both are built from `BASE_URL`.

## Mail notifications

All mail goes through the `notifications` table (outbox pattern):

1. Something queues a row: `bookings.cancel_reservations()` calls `queue_freed_slots()` (one mail per day and subscriber, also when a whole day is cancelled), `auth.forgot_password()`
   calls `queue_password_reset()`, and `run_once()` calls `queue_due_reminders()`.
   `dedupe_key` is UNIQUE and inserts use `INSERT OR IGNORE`, so a mail is queued at most once.
2. `flask send-notifications` (`--loop 60` in the `notifier` container) runs `run_once()`: queue due
   reminders, send unsent rows that haven't expired, retry failures up to `MAX_ATTEMPTS`, delete
   rows older than 30 days.

Why: a request never waits for the mail server, an outage only delays mails, and with several
Gunicorn workers nothing is sent twice. Never call `mail.deliver()` from a view.

Addresses must be confirmed: saving a new address in the profile (or an admin creating a user
with one) sets `email_verified = 0` and queues `queue_email_verification()` (at most one per user
and address in 15 minutes; "Bestätigungsmail nochmals senden" in the profile). Reminders,
freed-slot mails and reset links only go to confirmed addresses, so a typo can't hand a stranger
the account. Addresses that existed before the column was added count as confirmed (step in
`init_db()`).

Rules: reminders go out within `reminder_minutes` before the slot start; freed slots are
announced only for slots starting within 7 days (`FREED_WINDOW`), not to the person who cancelled.
Without `SMTP_HOST`/`MAIL_FROM` nothing is queued and the "Passwort vergessen?" link is hidden.
Mail texts are plain text and German; links use `BASE_URL`, since the notifier has no request.

## Security

- **Secret key:** required unless in debug/testing mode (`create_app` raises otherwise).
- **CSRF:** every POST must carry `csrf_token` (`{{ csrf_token() }}` in a hidden field).
  The token lives in the session and is compared with `secrets.compare_digest`. A new form
  without the field fails with 400. (A small hand-written check instead of Flask-WTF,
  since the app has no other use for WTForms.)
- **Sessions:** `session.clear()` on login (against session fixation) and logout;
  HttpOnly and SameSite=Lax cookies; Secure via `SESSION_COOKIE_SECURE`. Flask sessions are
  signed cookies, so the server can't delete them; instead `set_password()` increments
  `users.session_version` and `load_user()` ends every session that carries an older version. The
  session where the user changed their own password keeps working (`auth.keep_session()`).
  Sessions from before the column existed have no `sv` and match the default 0.
- **Login throttling:** every failed login adds a row for the username and one for the client
  address to `login_failures`. With 10 (`MAX_FAILURES_PER_USER`) or 20 (`MAX_FAILURES_PER_IP`) in
  the last 15 minutes, `/login` answers 429 without checking the password. A successful login
  clears the username's rows. Trade-off: someone can lock a username for 15 minutes; residents with
  "stay signed in" don't notice. Unknown usernames are checked against a dummy hash, so the
  response time doesn't reveal whether an account exists. Failed and blocked logins are logged
  with `%r` (no forged log lines). The client address is `request.remote_addr`, which is the
  proxy's unless `TRUSTED_PROXIES` is set.
- **Headers** (`security.add_headers()`): a strict CSP (`script-src 'self'`, no inline scripts;
  `style-src` allows inline `style=""` attributes; `frame-ancestors 'none'`, `form-action 'self'`),
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` (tokens
  in reset/unsubscribe URLs), `Cache-Control: no-store` except for static files, and HSTS when the
  request came over HTTPS. JavaScript goes into files in `static/`.
- **Limits:** `MAX_CONTENT_LENGTH` = 64 KB (413 above), passwords 10–128 characters
  (`users.check_password()`, also for `ADMIN_PASSWORD`), dates 2000–2999.
- **Escaping:** Jinja autoescape covers HTML. Confirmation texts go into plain `data-confirm*`
  attributes (autoescaped) and `confirm.js` inserts them with `textContent`, so no inline JS is
  needed. Should you ever put values into inline JS, use `|tojson` in a *single-quoted*
  attribute; `|e` is not safe there. `test_display_name_is_escaped` covers this.
- **Passwords:** werkzeug hashes (scrypt), length `db.MIN_PASSWORD_LENGTH`..`users.MAX_PASSWORD_LENGTH`.
- **Calendar feed token:** random, 256 bits, one per user, revocable in the profile; the feed
  only answers GET and contains nothing but the owner's bookings.
- **Tokens in mail links:** signed with `SECRET_KEY` (itsdangerous). Reset tokens expire after one
  hour and stop working once the password changed; unsubscribe tokens only switch a notification
  off and need a POST (link scanners only GET). `queue_password_reset()` queues at most one reset
  mail per user within `RESET_INTERVAL` (15 min), since anyone can ask for one.
- **Admin password reset:** the new password is rendered into the response of the POST, not
  flashed: Flask sessions are signed but not encrypted, so a flash message would sit readable in
  the cookie. A reload re-submits the form and creates another password.
- **Access log:** the Dockerfile starts Gunicorn with `--logger-class
  laundry.accesslog.RedactingLogger`, which replaces the token in `/reset-password/`,
  `/unsubscribe/`, `/verify-email/` and `/calendar/` paths with `***` in every log field. Reverse proxy logs
  are the operator's job (README, *Logs*).
- **Last admin:** an admin can't delete themselves (view and UI), and there is no way to remove
  admin rights, so at least one admin always remains.
- **Reporting:** `SECURITY.md` (GitHub private advisories) and `/.well-known/security.txt`.
- **Container:** `docker-entrypoint.sh` starts as root only to `chown` the data folder and the
  `laundry.db*` files to `PUID:PGID` (default 1000), then `exec setpriv` drops to that user
  before the app starts. With `--user` it skips the `chown`. `db.ensure_writable()` checks at
  startup that the database file and folder are writable and fails with a message naming the
  path, owner and fix, instead of SQLite's "unable to open database file".
  On Windows (no `os.getuid`) the message leaves out the UID and the `chown` hint.

## Frontend

- Design tokens are CSS variables in `:root` of `style.css`. Blue = your booking,
  grey = someone else's, dashed = free, orange = today and the running slot.
- Layout: flexbox/grid. The week grid has 7 columns on desktop and switches at
  `max-width: 860px` to one row per day with the three slots next to each other.
- System font stack, no external requests (no CDNs, no Google Fonts).
- Touch targets are at least 44 px; use real `<button>`/`<a>` elements and
  `aria-label` on icon-only buttons.
- Confirmations: give the form `data-confirm="Frage"` and optionally `data-confirm-title`,
  `data-confirm-ok` (button text) and `data-confirm-danger` (red button). `confirm.js` shows
  the `<dialog>` from `base.html`; without JavaScript the form submits directly, so the
  permission check belongs in the view as always. Never use `window.confirm()` directly.
- The README screenshots live in `docs/screenshots/` (desktop 1280 px wide, phone 390 px
  at 2x). Retake them when the UI changes visibly, with demo data and a daytime clock so
  the "running" slot is visible.

## Testing

Fixtures in `tests/conftest.py`:

| Fixture | Gives you |
|---|---|
| `app` | App with a temp DB, users `alice` (admin) and `bob`, clock fixed at Thu 2026-10-15 14:20 |
| `client` | Flask test client |
| `login` | `login('bob')` logs in and returns a CSRF token; `c=` for another client |
| `csrf` | `csrf()` returns the CSRF token of the client's session |
| `query` | `query(sql, *params)` returns rows as tuples |
| `reservations` | current bookings as `(day, slot, username)` tuples |
| `mailbox` | mails "sent" in the test as `(to, subject, body)`; `mail.deliver` is replaced, mail is configured with `BASE_URL=https://wk.test` |

Add a test for every new route or rule, including the 400/403/404 paths.

CI (`.github/workflows/ci.yml`) runs on every pull request and every push to `main`:

- **test:** `ruff check .`, `pytest`, `shellcheck` on the shell scripts
- **docker:** `docker build`, then `scripts/docker-smoke-test.sh`, which starts the image three
  times: with a named volume (health check, app runs as UID 1000, `create-user` via
  `docker exec`, reset token not in the access log), with a root-owned host folder and `PUID/PGID` (ownership gets fixed) and
  with `--user` on a folder it can't write (must stop with the clear error message).

**Audit** (`.github/workflows/audit.yml`) runs `pip-audit -r requirements-dev.txt` on every pull
request, every push to `main` and every Monday, because new vulnerabilities are published without
any change in this repository. Locally: `pip install -r requirements-audit.txt && pip-audit -r
requirements-dev.txt`.

**Dependabot** (`.github/dependabot.yml`) opens one grouped PR per week for the pip packages and one
for the GitHub Actions. CI and the audit run on it like on any PR; merge it when they are green.

**Workflow hardening:** every workflow sets `permissions` explicitly (`contents: read`, plus what
Pages needs), actions are pinned to a commit SHA with the version as a comment
(`actions/checkout@<sha>  # v5`), and `actions/checkout` runs with `persist-credentials: false`, so
the token isn't left in `.git/config` for later steps. When adding an action, pin it the same way;
Dependabot keeps the SHAs current.

Run the Docker part locally with
`docker build -t laundry-booking:ci . && scripts/docker-smoke-test.sh`.

**Release** (`.github/workflows/release.yml`) runs on a pushed tag `vX.Y.Z`: it runs the CI workflow
(`workflow_call`), checks that the tag matches `version` in `pyproject.toml`, builds the image for
`linux/amd64` and `linux/arm64`, pushes it to `ghcr.io/martigr/laundry-booking` with the tags
`X.Y.Z`, `X.Y`, `X` and `latest`, and creates the GitHub release with generated notes. To release:
bump `version` in `pyproject.toml` (semantic versioning: patch for fixes, minor for features, major
when an update needs manual steps) in a PR, merge it, then on `main`:
`git tag v1.2.3 && git push origin v1.2.3`. The first push creates the package as private; make it
public once under *Packages → laundry-booking → Package settings → Change visibility*.

The project page in `site/` is published by `.github/workflows/pages.yml` on every push to `main`
that touches `site/` or `docs/screenshots/` (or by hand via *Run workflow*). The workflow copies
`docs/screenshots/` to `screenshots/` next to `index.html`, so retaken README screenshots show up
there too. One-time setup: *Settings → Pages → Source: GitHub Actions*.

## Deployment

See the README, in particular *HTTPS*, *Backup and restore* and the *Security checklist*. In short:
build the Docker image, mount a volume at `/data`, set `SECRET_KEY` (and the first admin), put it
behind an HTTPS reverse proxy, set `SESSION_COOKIE_SECURE=1` and `TRUSTED_PROXIES=1`, and back up
with `flask backup`. `docker-compose.yml` publishes port 5000 on `127.0.0.1` only.
