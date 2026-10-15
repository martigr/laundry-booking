# Laundry Booking

A small web app for booking the shared washing machine in an apartment building.
Residents see a week calendar with three slots per day and book a free slot with one click.
Whoever is first gets the slot; if something doesn't fit, people talk to each other.

## Features

- Login per household (user with a free display name); "stay signed in" option
- Week view (default) and month overview, usable on phones
- Daily slots: Vormittag 07–12, Nachmittag 12–17, Abend 17–22
- One click on a free slot books it, one click on your own booking cancels it
- "Now in the laundry room" card, next free slot and your upcoming bookings
- Past slots can't be booked or cancelled; booking is possible up to N weeks ahead
- Minimal admin area: create/delete users, reset passwords, set the booking horizon;
  admins can cancel any booking
- CSRF protection on all forms; German UI

## Quick start (development)

Requires Python 3.13.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
flask --app laundry --debug seed-demo   # demo users: alice / password123 (admin), bob / password456
flask --app laundry --debug run         # http://localhost:5000
```

In debug mode a development `SECRET_KEY` is used and the database is created at `instance/laundry.db`.

Run the checks:

```bash
ruff check .
pytest
```

## Production (Docker)

```bash
docker build -t laundry-booking .
docker run -d --name laundry -p 5000:5000 \
  -v laundry-data:/data \
  -e SECRET_KEY='a-long-random-secret' \
  -e ADMIN_USERNAME=admin -e ADMIN_PASSWORD='change-me-please' \
  laundry-booking
```

- The container runs Gunicorn as an unprivileged user; the database lives in the `/data` volume.
- `ADMIN_USERNAME`/`ADMIN_PASSWORD` create the first admin when the database has no users yet.
  Afterwards the admin creates all other users in the app under *Verwaltung*.
- Serve it behind HTTPS (reverse proxy) and set `SESSION_COOKIE_SECURE=1`.
- `/health` is used by the Docker health check.

Without Docker: `gunicorn --workers 2 --bind 0.0.0.0:5000 "laundry:create_app()"` with the same environment variables.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | – (required outside debug mode) | Session signing |
| `DATABASE` | `instance/laundry.db` (`/data/laundry.db` in Docker) | SQLite file |
| `APP_TIMEZONE` | `Europe/Zurich` | Defines "today" and "now" |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | – | First admin on an empty database |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` behind HTTPS |
| `LOG_LEVEL` | `INFO` | Log level |

## Command line

```bash
flask --app laundry create-user <username> [--name "Display name"] [--admin]   # asks for the password
flask --app laundry make-admin <username>
flask --app laundry seed-demo                                                  # demo data, development only
```

In Docker: `docker exec -it laundry flask --app laundry create-user ...`

## Development

Architecture, data model, routes, security notes and conventions: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
Guidance for Claude Code: [CLAUDE.md](CLAUDE.md).
