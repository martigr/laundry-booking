# CLAUDE.md

Flask + SQLite app for booking the shared washing machine in an apartment
building. Full developer guide: `docs/DEVELOPMENT.md`. Read it before
changing the data model, booking rules or security code.

## Commands

```bash
pip install -r requirements-dev.txt          # setup (use a venv)
ruff check . && pytest                       # both must pass before committing (CI runs the same)
docker build -t laundry-booking:ci . && scripts/docker-smoke-test.sh   # after Dockerfile/entrypoint changes
flask --app laundry --debug seed-demo        # demo users alice/password123 (admin), bob/password456
flask --app laundry --debug run              # dev server on :5000
```

## Layout

- `laundry/`: the app package. `create_app()` in `__init__.py`; blueprints `auth`, `bookings`,
  `profile`, `admin`; data access in `db.py` and `users.py`; clock and slot definitions in `slots.py`;
  CLI in `cli.py`.
- `laundry/templates/`: Jinja templates extending `base.html`; slot rendering in `_macros.html`.
- `laundry/static/style.css`: all styling; colours are CSS variables in `:root`.
- `tests/`: pytest, one file per area; fixtures in `conftest.py` (`app`, `client`, `login`, `csrf`, `query`).

## Rules

- UI text is **German** (Swiss style: "ss" instead of "ß"). Code, comments, docs and commits are English.
- No quotas, by design: whoever books first gets the slot. Don't add limits unless asked.
- One account per household: bookings belong to `users.id`; `display_name` is what others see.
- Get the current time only via `slots.now()` (module attribute; tests replace it), never `datetime.now()`.
- DB access through `get_db()` (one connection per request); writes inside `with get_db() as con:`.
- Create users only through `users.create_user()` (validation, lowercase usernames).
- Every POST form needs `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- Put booking and permission rules in the view functions, not only in templates.
  Compute cell state in `bookings.build_cell()`, not in Jinja.
- Confirmations via `data-confirm*` attributes on the form (styled dialog in `static/confirm.js`),
  not `confirm()`. Values inside inline JS attributes: `|tojson` in a single-quoted attribute, never `|e`.
- Schema lives in `db.SCHEMA`/`init_db()`. New columns on existing tables go into `db.ADDED_COLUMNS`
  (added on startup if missing); other changes need an idempotent step in `init_db()`; always with a
  test that builds the old schema.
- Mail only through the outbox: queue with the `notifications.queue_*()` functions (unique `dedupe_key`),
  `flask send-notifications` sends. Never call `mail.deliver()` from a view. Tests use the `mailbox` fixture.
- Keep it dependency-light: no ORM, no JS framework, no build step, no external CDNs or fonts.
  Dependencies are pinned in `requirements*.txt`.
- `seed-demo` is for local demo data only; tests build their own state in fixtures.
- Add or adjust tests for every behaviour change, including 400/403/404 paths.
- When changing routes, config or the schema, update `docs/DEVELOPMENT.md` and the `README.md`.
