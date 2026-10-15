# CLAUDE.md

Flask + SQLite app for booking the shared washing machine in an apartment
building. Full developer guide: `docs/DEVELOPMENT.md`. Read it before
changing the data model, booking rules or security code.

## Commands

```bash
pip install -r requirements-dev.txt   # setup (use a venv)
pytest                                # run tests from repo root; must pass before committing
cd laundry && python seed.py         # demo users alice/password123 (admin), bob/password456
cd laundry && python app.py          # dev server on :5000
```

## Layout

- `laundry/app.py`: the whole backend (config, DB schema, auth, CSRF, routes, CLI).
- `laundry/templates/`: Jinja templates extending `base.html`; slot rendering in `_macros.html`.
- `laundry/static/style.css`: all styling; colours are CSS variables in `:root`.
- `tests/test_app.py`: pytest suite with fixed time and temp DB.

## Rules

- UI text is **German** (Swiss style: "ss" instead of "ß"). Code, comments, docs and commits are English.
- No quotas, by design: whoever books first gets the slot. Don't add limits unless asked.
- One account per household: bookings belong to `users.id`; `display_name` is what others see.
- Get the current time only via `now()` (uses `APP_TIMEZONE`, monkeypatched in tests), never `datetime.now()`.
- Every POST form needs `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- Put booking and permission rules in the route handlers (`book`, `cancel`, `admin_*`), not only in templates.
  Compute cell state in `build_cell()`, not in Jinja.
- Values inside inline JS attributes: `|tojson` in a single-quoted attribute, never `|e`.
- Schema lives in `init_db()` (runs on every start, in all Gunicorn workers, inside `BEGIN IMMEDIATE`).
  No migrations exist yet; once there is production data, schema changes need an idempotent
  migration step there plus a test that builds the old schema.
- Keep it dependency-light: no ORM, no JS framework, no build step, no external CDNs or fonts.
- Add or adjust tests for every behaviour change, including 400/403 paths.
- When changing routes, config or the schema, update `docs/DEVELOPMENT.md` and the `README.md`.
