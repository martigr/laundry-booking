# Developer Guide

How the laundry booking app is built, and how to change it safely.
For installation and operation see the [README](../README.md).

## Overview

A small Flask app for one shared washing machine in an apartment building.
Residents log in, see a week (or month) calendar with three slots per day
and book a free slot (or a whole day) with one click. There are no quotas: first come, first
served. Admins manage users and the booking horizon and see an audit log.

- **Backend:** Python package `laundry/` built with an app factory and blueprints;
  SQLite through the standard library, no ORM
- **Frontend:** server-rendered Jinja templates, one stylesheet, no build step
  and practically no JavaScript (only the confirmation dialog in `static/confirm.js`,
  keeping the scroll position after booking in `static/scroll.js`,
  the live password-repeat check in `static/profile.js`, the push button in `static/push.js` and the service
  worker of the home screen app;
  the booking note popover uses the HTML `popover` attribute, no script); no inline scripts, the CSP
  forbids them
- **UI language:** German. Code, comments, commit messages and docs: English.

## Project layout

```
laundry/                  # the application package
├── __init__.py           # create_app(): config, blueprints, /health, schema setup; __version__
├── audit.py              # audit log: record() of logins, bookings and setting changes, rotation
├── db.py                 # connection per request, schema, init_db(), settings
├── users.py              # create_user(), get_user*(), set_password(), clean_email(), UserError
├── mail.py               # is_configured(), deliver(): one plain-text mail over SMTP
├── notifications.py      # outbox: queue_*() for reminders/freed slots/password reset, run_once()
├── push.py               # Web Push: is_configured(), valid_subscription(), deliver() via pywebpush, VAPID key
├── slots.py              # SLOTS, now() (the only clock), date formatting, booking horizon
├── auth.py               # bp "auth": login/logout, login throttling, CSRF, login_required/admin_required
├── security.py           # security headers (CSP etc.), /.well-known/security.txt
├── privacy.py            # public privacy notice /privacy
├── accesslog.py          # Gunicorn logger class that blanks tokens of mail links in the access log
├── bookings.py           # bp "bookings": week/month view, book (slot or day), cancel, booking notes
├── profile.py            # bp "profile": display name, notification settings, push devices, calendar link, password; confirm address, unsubscribe
├── ical.py               # bp "ical": the bookings of the user's household as an iCalendar feed (calendar subscription)
├── guide.py              # bp "help": the help page (how to book and cancel, with screenshots)
├── pwa.py                # bp "pwa": service worker /sw.js and offline page /offline (home screen app)
├── admin.py              # bp "admin": users, households, settings, audit log page
├── cli.py                # flask commands: create-user, make-admin, seed-demo, send-notifications, gen-vapid-key, backup
├── static/style.css      # all styles (design tokens as CSS variables)
├── static/confirm.js     # styled confirmation dialog for forms with data-confirm
├── static/scroll.js      # keeps the scroll position after calendar/profile forms; floating flash message
├── static/profile.js     # live "passwords don't match" hint on the profile page
├── static/push.js        # profile page: "Push aktivieren/deaktivieren" for this device
├── static/pwa.js         # registers the service worker
├── static/offline.js     # offline page: reload when back online
├── static/manifest.webmanifest, static/icons/*.png  # home screen app (scripts/icons.py)
├── static/help/*.png     # screenshots of the help page (scripts/screenshots.py)
└── templates/
    ├── base.html         # layout: header, nav, flash messages, confirmation <dialog>
    ├── _macros.html      # slot_cell(): renders one slot (book / cancel / static); note_button(): note badge + popover
    ├── _logo.html        # inline SVG logo
    ├── index.html        # week view + side cards
    ├── month.html        # month overview (links into week view)
    ├── login.html        # overrides block body (no header)
    ├── profile.html      # display name, notifications (mail/push), calendar link, password
    ├── admin.html
    ├── audit.html        # audit log table with filter and paging
    ├── help.html         # help page for residents
    ├── auth_base.html    # layout of the logged-out pages (login, forgot/reset password, unsubscribe)
    ├── privacy.html      # privacy notice; own logged-out layout, normal layout when logged in
    ├── offline.html      # "Keine Verbindung", cached by the service worker
    ├── sw.js             # the service worker (a template for its URLs and cache name)
    ├── forgot_password.html, reset_password.html, unsubscribe.html, confirm_email.html
tests/
├── conftest.py           # fixtures: app, client, login, csrf, query, reservations
├── test_auth.py          # login, logout, CSRF, SECRET_KEY, profile
├── test_bookings.py      # booking rules, cancel permissions, views, escaping
├── test_admin.py         # admin area
├── test_audit.py         # audit log entries, page, rotation; version in the footer
├── test_cli.py           # CLI commands, first-admin bootstrap
├── test_help.py          # help page: content, settings, screenshots exist
├── test_privacy.py       # privacy page with and without login, contact, footer links
├── test_pwa.py           # manifest, icons, service worker, offline page
├── test_ical.py          # calendar feed: content, time zones, link create/renew/delete, 404s
├── test_notifications.py # reminders, freed slots, password reset, unsubscribe, retries, SMTP settings
├── test_push.py          # push devices (subscribe/unsubscribe, validation), push reminders/freed slots, VAPID key
└── test_security.py      # headers, input limits, redirects, login throttling, sessions, security.txt, backup
docker-entrypoint.sh      # fixes /data ownership, drops root (PUID/PGID)
docker-compose.yml        # web + notifier container, settings from .env (.env.example)
SECURITY.md               # how to report vulnerabilities
scripts/docker-smoke-test.sh  # starts a built image and checks health, user, volumes (CI)
scripts/screenshots.py        # retakes the README and help page screenshots (needs Playwright)
scripts/icons.py              # draws the home screen icons from the logo (needs Pillow)
scripts/backup-nas.sh         # example: daily backup to an NFS-mounted NAS (see README)
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
`TRUSTED_PROXIES` (wraps the app in werkzeug's `ProxyFix` when > 0), `SECURITY_CONTACT`,
`PRIVACY_CONTROLLER`, `PRIVACY_CONTACT`, `BASE_URL`,
the `SMTP_*`/`MAIL_*` settings and `VAPID_PRIVATE_KEY`/`VAPID_SUBJECT` (push).

## Data model

```
users                                   reservations
─────                                   ────────────
id            INTEGER PK                id        INTEGER PK
username      TEXT UNIQUE (lowercase)   day       TEXT  ISO date 'YYYY-MM-DD'
password      TEXT  werkzeug hash       slot      TEXT  one of SLOTS names
display_name  TEXT  shown in calendar   user_id   INTEGER → users.id ON DELETE CASCADE
is_admin      INTEGER 0/1               note      TEXT  shown to everyone, max. 80 chars, NULL = none
email         TEXT  optional              UNIQUE (day, slot)
email_verified INTEGER 0/1  mails only go out when 1
reminder_minutes INTEGER 0 = off, else lead time of the reminder
reminder_mail INTEGER 0/1  reminder by mail (default 1)
reminder_push INTEGER 0/1  reminder by push
notify_freed  INTEGER 0/1  freed slots by mail
freed_push    INTEGER 0/1  freed slots by push
session_version INTEGER  +1 on every password change
calendar_token TEXT UNIQUE  secret of the calendar feed URL, NULL = none
household_id  INTEGER → households.id ON DELETE SET NULL, NULL = none

households
──────────
id    INTEGER PK
name  TEXT UNIQUE (case-insensitive)

notifications (outbox)
──────────────────────
dedupe_key  TEXT UNIQUE   e.g. 'reminder:<reservation id>' (push: plus ':push:<subscription id>'),
                          'freed:<day>:<user id>:<mail | push:<subscription id>>:<queued at>'
to_addr, subject, body    push: to_addr = host of the push service, body = JSON payload
created_at, expires_at    not sent after expires_at (slot start, reset link validity)
sent_at, attempts, last_error
push_subscription_id      → push_subscriptions.id ON DELETE CASCADE, NULL = mail
slots                     freed-slot messages: the slot names, 'Vormittag+Abend'
send_after                not sent before (freed-slot messages wait FREED_HOLD), NULL = at once

push_subscriptions (one per browser/device)
──────────────────
user_id     INTEGER → users.id ON DELETE CASCADE
endpoint    TEXT UNIQUE   URL at the browser's push service; a secret of the device
p256dh, auth TEXT         the browser's keys (base64url), for encrypting the message
created_at  TEXT

login_failures (failed logins of the last 15 minutes)
──────────────
key   TEXT   'user:<username>' or 'ip:<address>'
at    TEXT   ISO time; older rows are deleted on the next failure

settings
────────
key   TEXT PK      'horizon_weeks', 'audit_days'
value TEXT         defaults in db.DEFAULT_SETTINGS

audit_log
─────────
at        TEXT     ISO local time
user_id   INTEGER  who acted; no foreign key, entries outlive deleted users; NULL for failed logins and the CLI
username  TEXT     their username, what was typed at a failed login, or 'Kommandozeile'
action    TEXT     key of audit.ACTIONS
detail    TEXT     German, max. 200 characters
ip        TEXT     request.remote_addr, NULL for the CLI
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
  once and persists in the file. `enable_wal()` retries for up to 30 s: SQLite doesn't wait for a lock
  when switching the journal mode, so on a new database the second worker would otherwise fail
  with "database is locked" while the first is still in `init_db()`.
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
  (Post/Redirect/Get). Exception: `/push/subscribe` and `/push/unsubscribe`, which `static/push.js` calls with
  `fetch` (form-encoded, with `csrf_token`) and which answer 204. Feedback goes through `flash(message, 'ok' | 'warn')`.
- After book/cancel, `back_to_calendar()` returns to the form's `next` field. The templates fill it with
  `request.script_root ~ request.full_path`: `full_path` alone lacks the prefix when a proxy mounts the app
  below a path (`SCRIPT_NAME`, e.g. `/waschkueche`), and the redirect would leave the app. Only local
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
| `/cancel/<id>` | POST | `bookings.cancel` | owner, housemate or admin | Only before the slot has ended |
| `/cancel` | POST | `bookings.cancel_many` | owner, housemate or admin | `id` (repeated), `next`; cancels a block, skips ended slots |
| `/note` | POST | `bookings.note` | owner or housemate | `id` (repeated), `note`, `next`; sets the note of a block (empty clears it), skips ended slots; 400 without ids or above 80 characters |
| `/help` | GET | `help.index` | user | Help page: calendar, booking, cancelling, month view, phone, households, profile |
| `/profile` | GET/POST | `profile.index` | user | `action=name`, `action=notifications` (`email`, `reminder_minutes`, checkboxes `reminder_mail`, `reminder_push`, `notify_freed`, `freed_push`; the push ones only count while push is configured; a changed address needs confirming), `action=verify_email` (send the confirmation again), `action=calendar` (create or renew the calendar link), `action=calendar_delete`, or `action=password` with `old_password`, `new_password`, `new_password2` (must match) |
| `/admin` | GET | `admin.index` | admin | Users, households and settings |
| `/admin/users` | POST | `admin.create` | admin | Create user |
| `/admin/users/<id>/reset` | POST | `admin.reset_password` | admin | New random password (12 characters), shown once in the response itself (not flashed, so not in the cookie); ends the user's sessions |
| `/admin/users/<id>/delete` | POST | `admin.delete` | admin | Not yourself; bookings are deleted too |
| `/admin/users/<id>/household` | POST | `admin.set_household` | admin | `household_id` (empty = none); 400 for an unknown household |
| `/admin/households` | POST | `admin.create_household` | admin | `name` (1–40 characters, unique ignoring case) |
| `/admin/households/<id>/delete` | POST | `admin.delete_household` | admin | Members stay, their `household_id` becomes NULL |
| `/admin/settings` | POST | `admin.settings` | admin | `horizon_weeks` (1–52), `audit_days` (1–3650); a missing field stays unchanged, an invalid one saves nothing |
| `/admin/audit` | GET | `admin.audit_log` | admin | Audit log, newest first, 50 per page; `?kind=login\|booking\|settings\|admin`, `?page=N`; 400 for an unknown kind or page < 1 |
| `/forgot-password` | GET/POST | `auth.forgot_password` | public, 404 without mail | Queues a reset mail (at most one per user in 15 min); same answer whether the account exists or not |
| `/reset-password/<token>` | GET/POST | `auth.reset_password` | token | Signed, valid 1 h, single use (contains part of the password hash) |
| `/push/subscribe` | POST | `profile.push_subscribe` | user, 404 without push | `endpoint`, `p256dh`, `auth` from `static/push.js`; 204; 400 unless the endpoint is `https` at a known push service and the keys have the right size; an endpoint of another user moves to this one; at most 10 devices per user (oldest dropped) |
| `/push/unsubscribe` | POST | `profile.push_unsubscribe` | user | `endpoint`; 204 (also if unknown), 400 without endpoint |
| `/verify-email/<token>` | GET/POST | `profile.confirm_email` | token | Signed, valid 7 days, only for the address it was sent to; GET shows a button, POST confirms |
| `/unsubscribe/<token>` | GET/POST | `profile.unsubscribe` | token | GET shows a button, POST switches the notification off by mail (push stays) |
| `/calendar/<token>.ics` | GET | `ical.feed` | token | The bookings of the token owner's household (or their own) as iCalendar; 404 for unknown tokens |
| `/health` | GET | `health` | public | Liveness probe |
| `/sw.js` | GET | `pwa.service_worker` | public | Service worker; `Cache-Control: no-cache` |
| `/offline` | GET | `pwa.offline` | public | "Keine Verbindung", cached by the service worker; nothing personal |
| `/.well-known/security.txt` | GET | `security_txt` | public | RFC 9116; `SECURITY_CONTACT` plus the project's advisory link, `Expires` 180 days ahead |
| `/privacy` | GET | `privacy` | public | Privacy notice (German); `PRIVACY_CONTROLLER`/`PRIVACY_CONTACT`, audit retention from the settings; contact links only as `mailto:`/`http(s):` |

### Booking rules

All of them live in `bookings.book()` and `bookings.cancel()`; the UI only mirrors them.

- A slot is **past** once its end time is reached. It can't be booked or cancelled.
  A **running** slot can still be booked if it is free.
- Booking is allowed up to `today + horizon_weeks` (`slots.last_bookable_day()`).
- **Whole day:** `book_day()` inserts each slot of the day that hasn't ended with
  `ON CONFLICT (day, slot) DO NOTHING` in one transaction. Slots someone else already has
  stay theirs; the flash message names them (slots of housemates don't count as taken). No quota,
  like single slots.
- Users cancel their own bookings and those of their housemates; admins can cancel any.
  `cancel_reservations()` is shared by `/cancel/<id>` and `/cancel`: 404 if any id is missing, 403 if
  any booking isn't your household's.
- **Notes:** `note()` sets the same note on every booking of a block (`MAX_NOTE` = 80 characters after
  `clean_note()` joined it into one line; empty = NULL). Owner or housemate only, admins can't edit others'
  notes (they can cancel the booking). Ended slots are skipped, like when cancelling; same 404/403 rules
  (`open_reservations()`); `clear=1` deletes the note like an empty one. `group_blocks()` gives a block's `note`
  (each different note once, joined with " · "), and `build_days()` sets `block` on every booked cell.
  Display: the `note_button()` macro in `_macros.html` renders a small badge in the corner of the cell (dark
  speech bubble = note, dashed "+" = your own upcoming booking without one) and a popover (`popover` attribute,
  `popovertarget`, styled like `.modal`; no JavaScript): the note for everyone else, a form for the household.
  The form edits the cell's whole block. The same badge sits on the date badge in the upcoming list. Cells keep
  their size; the note text itself only appears in the popover, the month pill's tooltip and the
  "Jetzt in der Waschküche" card.
- **Households:** `bookings.household_ids()` returns the current user's id plus those of everyone with
  the same `household_id` (cached on `g`). A booking by any of them counts as the user's own:
  `build_cell()` sets `ours` (state `mine`, blue, cancelable, no "Fremde Buchung" dialog) next to
  `mine` (booked by the user personally, labelled "Du"). Bookings still belong to the person who made
  them, so reminders go to them and the cell shows their name.
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
slot within the horizon and the upcoming bookings of the user's household (housemates' with their
name). `group_blocks()` merges adjacent slots of the same day and user into one block ("Nachmittag – Abend",
"Ganzer Tag"), both in the upcoming list (one cancel button per block) and for the
running booking.

## Calendar subscription

`ical.py` serves the bookings of a user's **household** as an iCalendar feed (RFC 5545) at
`/calendar/<token>.ics`, so phones can subscribe to it (iOS natively, Android with ICSx⁵, Google
Calendar via its web UI). Other households' bookings are never in it.

- Households: an admin creates them and assigns users (`users.household_id`) in the admin area,
  so several accounts (e.g. a couple, each with their own login) see each other's bookings in their
  subscribed calendar and can cancel them in the app (see *Booking rules*). A user without a household gets only their own bookings. Bookings still
  belong to the user; deleting a household only clears `household_id`. In a household feed the
  `SUMMARY` names who booked ("Waschküche: Abend (Bob)"), and blocks are merged per member only.
  Only admins assign households, so nobody can add themselves to another household's feed.

- Calendar apps can't log in, so the URL carries a secret: `users.calendar_token`
  (`secrets.token_urlsafe(32)`), created in the profile. Each member has their own link.
 "Neuen Link erstellen" replaces it (the
  old URL answers 404), "Link löschen" sets it to NULL; deleting the user removes it too. It is a
  random value in the database rather than a signed token, so each user can revoke theirs without
  touching `SECRET_KEY`. It does not change with the password: the feed only shows bookings, and
  re-subscribing every device after each password change would be a nuisance.
- Content: bookings from 4 weeks ago (`KEEP_PAST`) on, adjacent slots merged with
  `bookings.group_blocks()` into one event; a booking note goes into `DESCRIPTION` ("Notiz: …").
  The `UID` is built from the reservation ids, so it
  changes when a block grows or shrinks; subscribed calendars are replaced as a whole on every
  refresh, so the old event disappears. Times are converted from the building's `TIMEZONE` to UTC
  (`…Z`), which avoids a `VTIMEZONE` block. `REFRESH-INTERVAL`/`X-PUBLISHED-TTL` suggest one
  hour; Apple respects it, Google refreshes when it likes (often only daily).
- Written by hand (escaping, 75-octet line folding, CRLF) instead of adding a dependency.
- The profile shows the `https://` link (with a copy button from `static/profile.js`, hidden
  where `navigator.clipboard` is missing) and a `webcal://` link that opens "subscribe" on iOS.
  Both are built from `BASE_URL`.

## Audit log

`audit.record(con, action, detail)` writes one row to `audit_log` inside the caller's transaction, so
an entry only exists if the change was committed. The actor defaults to `g.user`; pass `user=` when
nobody is logged in yet (login, mail links) and `user=False, username=...` for failed logins. Every
`action` must be a key of `audit.ACTIONS`, which maps it to the German label and the filter group.

Logged: logins, failed and blocked logins, logouts, password reset by mail link; booking and
cancelling (with the owner's name when it wasn't one's own booking), booking notes (with the text); profile changes (display name,
notification settings, confirmed address, unsubscribe, push switched on/off for a device (with the push service's host,
never the endpoint), calendar link, password); admin actions
(users, households, settings) and `create-user`/`make-admin` on the command line. Rejected requests
(400/403/404, invalid input) are not logged, except logins.

- **Rotation:** each `record()` deletes entries older than the `audit_days` setting (default 90,
  set in the admin area). No separate job is needed; lowering the value takes effect with the next entry.
- **Blocked logins** are logged at most once per username and address per `LOGIN_WINDOW`, so
  hammering `/login` while blocked can't fill the table. Failed logins are already limited by the
  throttling.
- Passwords and tokens never go into `detail`. The table holds IP addresses and usernames: keep the
  retention as short as you need.

When you add a user action, add an `ACTIONS` entry, call `record()` in the view's transaction and
extend `tests/test_audit.py`.

## Notifications (mail and push)

Reminders and "a slot became free" go out by mail, by push or both; each user picks the channels per
notification in the profile (`reminder_mail`/`reminder_push` with the lead time `reminder_minutes`,
`notify_freed`/`freed_push`). Password reset and address confirmation are mail only.

All of it goes through the `notifications` table (outbox pattern):

1. Something queues a row: `bookings.cancel_reservations()` calls `queue_freed_slots()` (one message per day,
   subscriber and channel, also when a whole day is cancelled: the message waits `FREED_HOLD` (2 min,
   `send_after`), and slots of the same day cancelled meanwhile are added to it and restart the wait
   (`_queue_freed()`), so cancelling slot by slot doesn't send one message each), `auth.forgot_password()`
   calls `queue_password_reset()`, and `run_once()` calls `queue_due_reminders()`.
   `dedupe_key` is UNIQUE and inserts use `INSERT OR IGNORE`, so a message is queued at most once.
   Push messages get one row per device of the user (`_queue_push()`), so each device is retried on its own.
2. `flask send-notifications` (`--loop 60` in the `notifier` container) runs `run_once()`: queue due
   reminders, send unsent rows that haven't expired, retry failures up to `MAX_ATTEMPTS`, delete
   rows older than 30 days. Rows of a channel that is no longer configured wait.

Why: a request never waits for the mail server or a push service, an outage only delays messages, and with
several Gunicorn workers nothing is sent twice. Never call `mail.deliver()` or `push.deliver()` from a view.

### Push

Web Push with VAPID through `pywebpush`; no third-party account. Push is on while `VAPID_PRIVATE_KEY`
is set (`flask gen-vapid-key` prints one: base64url of the raw P-256 private key). `create_app()` fails
at startup if the key can't be read (`push.check_config()`). `VAPID_SUBJECT` is the contact sent to the
push services, by default `mailto:MAIL_FROM`, else `BASE_URL`.

- **Subscribing:** the profile's push card (only when push is configured) has one button, run by
  `static/push.js`. "Push aktivieren" asks for permission (must happen in the click handler), subscribes
  with the server's public key (`data-key`, `push.public_key()`) via the service worker's `pushManager` and
  POSTs `endpoint`, `p256dh` and `auth` as form fields with the CSRF token to `/push/subscribe`. "Push
  deaktivieren" POSTs to `/push/unsubscribe` and then unsubscribes the browser. The button shows "active"
  only when the browser has a subscription *and* the server has it (`data-endpoints`), so a subscription
  the server dropped can be renewed; one made with an older key is replaced. Without support (no service
  worker or `PushManager`, plain HTTP, iPhone outside the home screen app) or with blocked permission the
  card says so and hides the button.
- **A device belongs to one user:** subscribing an endpoint that another user had moves it (the old row
  and its queued messages are deleted), so a shared tablet only gets the last subscriber's messages. At
  most `MAX_PUSH_DEVICES` (10) per user; deleting a user deletes their devices.
- **Sending:** `push.deliver()` encrypts the JSON payload (`{"title", "body", "url", "tag"}`) for the
  device and signs the request with the VAPID key. `ttl` is the time until `expires_at` (slot start), else
  one day. 404/410 from the push service mean the subscription is gone (`push.Gone`): `send_pending()`
  deletes it, which cascades to its queued rows. Other errors are retried like mails.
- **Service worker:** `templates/sw.js` shows the notification on `push` (icon `icon-192.png`, `tag`
  replaces an older notification of the same booking or day) and on `notificationclick` focuses an open
  window or opens `url`; URLs of other origins fall back to the app's start page.
- Push needs HTTPS (or `localhost`). iOS/iPadOS 16.4+ only deliver push to the home screen app.

Addresses must be confirmed: saving a new address in the profile (or an admin creating a user
with one) sets `email_verified = 0` and queues `queue_email_verification()` (at most one per user
and address in 15 minutes; "Bestätigungsmail nochmals senden" in the profile). Reminders,
freed-slot mails and reset links only go to confirmed addresses, so a typo can't hand a stranger
the account. Addresses that existed before the column was added count as confirmed (step in
`init_db()`).

Rules: reminders go out within `reminder_minutes` before the slot start; back-to-back slots of the
same user on one day are one block with one reminder before its first slot; freed slots are
announced only for slots starting within 7 days (`FREED_WINDOW`), not to the person who cancelled.
Without `SMTP_HOST`/`MAIL_FROM` no mail is queued and the "Passwort vergessen?" link is hidden; without
`VAPID_PRIVATE_KEY` no push. Mail and push texts are German; links use `BASE_URL`, since the notifier has no request.

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
  in reset/unsubscribe URLs), `Cache-Control: no-store` except for static files and `/sw.js`
  (`no-cache`), and HSTS when the request came over HTTPS. JavaScript goes into files in `static/`.
- **Limits:** `MAX_CONTENT_LENGTH` = 64 KB (413 above), passwords 10–128 characters
  (`users.check_password()`, also for `ADMIN_PASSWORD`), dates 2000–2999.
- **Escaping:** Jinja autoescape covers HTML. Confirmation texts go into plain `data-confirm*`
  attributes (autoescaped) and `confirm.js` inserts them with `textContent`, so no inline JS is
  needed. Should you ever put values into inline JS, use `|tojson` in a *single-quoted*
  attribute; `|e` is not safe there. `test_display_name_is_escaped` covers this.
- **Passwords:** werkzeug hashes (scrypt), length `db.MIN_PASSWORD_LENGTH`..`users.MAX_PASSWORD_LENGTH`.
- **Calendar feed token:** random, 256 bits, one per user, revocable in the profile; the feed
  only answers GET and contains nothing but the bookings of the owner's household (or their own).
- **Push endpoints** come from the browser, so they are user input that the notifier POSTs to:
  `push.valid_subscription()` only accepts `https` URLs on port 443 without credentials at the browsers'
  push services (`push.PUSH_HOSTS`: Google, Mozilla, Apple, Microsoft), so nobody can make the server call
  internal addresses. The endpoint is a secret of the device: errors (`push.PushError`), logs, `to_addr`
  and the audit log only carry the push service's host.
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
  Gunicorn runs with `--no-control-socket`: its default socket path is under `$HOME`, which is
  still `/root` after `setpriv`, and `gunicornc` is not needed.

## Frontend

- The footer of every logged-in page shows `laundry.__version__` (`app_version` in templates). It is
  not shown on the logged-out pages, so the version isn't public.
- Every page links to `/privacy`: logged in via the footer in `base.html`, logged out via the
  `login-footer` in `auth_base.html` (no version). `privacy.html` renders the normal layout for
  users and its own header and footer (without version) otherwise. When the page states what the
  app stores or for how long, keep it in sync with the code (e.g. new tables, retention, cookies).

- Design tokens are CSS variables in `:root` of `style.css`. Blue = yours or your household's,
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
- Scroll position: calendar forms (they carry a hidden `next`) and forms inside an element with
  `data-keep-scroll` (the profile page) make `static/scroll.js` remember `scrollY` in
  `sessionStorage`; when the redirect lands on the same page it scrolls back and
  turns the `.flashes` box into a message floating at the top of the screen (hides after 5 s or on
  tap). `confirm.js` resubmits with `requestSubmit()` so confirmed forms are covered too. Without
  JavaScript the page starts at the top with the message in place, as before. Not on the admin
  page: the box with a generated password must stay visible at the top.
- Screenshots are taken by `scripts/screenshots.py`, each set on its own temporary database with
  fixed data and a fixed clock (Thursday afternoon, so today and a running slot are visible):
  - `readme`: `docs/screenshots/` for the README and the project page (desktop 1280 px wide, phone
    390 px at 2x; households, five users, a week of audit entries, the notification settings with push
    active). The Pages workflow copies them.
  - `help`: `static/help/` for the help page (demo data, logged in as bob, highlights in orange).

  Both sets run with mail and push configured (nothing is sent). Headless Chromium has no push service,
  so the script grants the notification permission and replaces `PushManager.getSubscription()` with a
  stand-in (`FAKE_PUSH`); the profile then shows the push button as a real browser would.

  Retake them when the UI changes visibly: `python scripts/screenshots.py` (both sets) or
  `python scripts/screenshots.py help`. It needs Playwright, which is not in `requirements-dev.txt`:
  `pip install playwright && playwright install chromium`, or point `CHROMIUM=` at an installed
  Chromium. Then copy the printed sizes into the `width`/`height` attributes in `help.html` (2x
  images: half) and of `week.png` in `site/index.html`. The images are saved as palette PNGs.
- **Home screen app:** `base.html` links `static/manifest.webmanifest` (name, `display: standalone`,
  icons; URLs relative to the manifest, so a path prefix works), the `apple-touch-icon` (iOS ignores
  the manifest icons) and `static/pwa.js`, which registers the service worker from `data-sw`
  (only in secure contexts: HTTPS or localhost). The icons are committed PNGs drawn by
  `python scripts/icons.py` (needs Pillow); rerun it when the logo or `--accent` changes.
  The worker (`templates/sw.js`, served at `/sw.js` so its scope is the whole app) also shows push
  messages (see *Push*). It **never caches pages**: they show bookings, carry CSRF tokens and must be current. It precaches only `/offline`,
  `style.css` and `offline.js`, answers GET navigations from the network (with navigation preload)
  and falls back to the offline page when that fails; forms (POST) and other requests pass through.
  The cache name contains `__version__`, so each release installs a new worker and refreshes the
  cache. The worker fetches `/offline` with the visitor's cookies, so that page must not show
  anything personal or consume flash messages (`test_pwa.py` checks both).
- The help page (`/help`, "Hilfe" in the navigation) explains booking and cancelling with these
  screenshots. Its texts follow the settings (slot times, booking horizon), the role (admin
  paragraph) and whether mail and push are configured (the "Benachrichtigungen" section only appears
  with at least one of them, its push part and screenshot only with push). When the calendar, booking or cancelling changes, update
  `help.html` too.

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
| `pushbox` | switches push on (`VAPID_PRIVATE_KEY`) and records push messages as `(endpoint, payload dict, ttl)`; `push.deliver` is replaced |

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
bump `version` in `pyproject.toml` and `__version__` in `laundry/__init__.py` (`test_version_matches_pyproject`
checks they match; semantic versioning: patch for fixes, minor for features, major
when an update needs manual steps) in a PR, merge it, then on `main`:
`git tag v1.2.3 && git push origin v1.2.3`. The first push creates the package as private; make it
public once under *Packages → laundry-booking → Package settings → Change visibility*.

**Pre-releases** for trying a version on staging: set the version to e.g. `1.6.0-beta.1` (both places),
push the tag `v1.6.0-beta.1` on the commit to test (it may be on a PR branch). The workflow publishes only
the image tag `1.6.0-beta.1` (not `1.6`, `1` or `latest`, so production on `:1` doesn't pick it up) and
marks the GitHub release as pre-release. Generated notes only list merged PRs, which is wrong for a beta
from an open PR, so write the notes into an annotated tag; the workflow uses its message (Markdown):
`git tag -a --cleanup=verbatim v1.6.0-beta.1 -F notes.md && git push origin v1.6.0-beta.1`. Without
`--cleanup=verbatim` git drops every line starting with `#` as a comment, i.e. the Markdown headings. A
lightweight tag still gets generated notes. Further betas count up (`-beta.2`); for the final release set
`1.6.0` and tag as usual (generated notes).

The project page in `site/` is published by `.github/workflows/pages.yml` on every push to `main`
that touches `site/` or `docs/screenshots/` (or by hand via *Run workflow*). The workflow copies
`docs/screenshots/` to `screenshots/` next to `index.html`, so retaken README screenshots show up
there too. One-time setup: *Settings → Pages → Source: GitHub Actions*.

## Deployment

See the README, in particular *HTTPS*, *Backup and restore* and the *Security checklist*. In short:
build the Docker image, mount a volume at `/data`, set `SECRET_KEY` (and the first admin), put it
behind an HTTPS reverse proxy, set `SESSION_COOKIE_SECURE=1` and `TRUSTED_PROXIES=1`, and back up
with `flask backup`. `docker-compose.yml` publishes port 5000 on `127.0.0.1` only.
