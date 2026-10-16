# Laundry Booking

A small web app for booking the shared washing machine in an apartment building.
Residents see a week calendar with three slots per day and book a free slot with one click.
Whoever is first gets the slot; if something doesn't fit, people talk to each other.

## Screenshots

![Week view](docs/screenshots/week.png)

<table>
  <tr>
    <td width="32%" valign="top"><img src="docs/screenshots/phone.png" alt="Week view on a phone"><br><sub>On the phone</sub></td>
    <td valign="top">
      <img src="docs/screenshots/month.png" alt="Month overview"><br><sub>Month overview</sub><br><br>
      <img src="docs/screenshots/admin.png" alt="Admin area"><br><sub>Admin area</sub>
    </td>
  </tr>
</table>

![Login](docs/screenshots/login.png)

## Features

- Login per household (user with a free display name); "stay signed in" option
- Week view (default) and month overview, usable on phones
- Daily slots: Vormittag 07–12, Nachmittag 12–17, Abend 17–22
- One click on a free slot books it, one click on your own booking cancels it
- "Ganzer Tag" books all free slots of a day at once
- Adjacent bookings show up as one block in your upcoming bookings (and cancel together)
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

- The app runs as an unprivileged user (UID/GID 1000 by default); the database lives in `/data`.
- `ADMIN_USERNAME`/`ADMIN_PASSWORD` create the first admin when the database has no users yet.
  Afterwards the admin creates all other users in the app under *Verwaltung*.
- Serve it behind HTTPS (reverse proxy) and set `SESSION_COOKIE_SECURE=1`.
- `/health` is used by the Docker health check.

### Volumes and permissions

SQLite needs write access to the database file **and** its folder. The container starts as
root only long enough to give `/data` to the app user, then drops to that user:

| Setup | What happens |
|---|---|
| Named volume (`-v laundry-data:/data`) | Works as is. |
| Host folder (`-v /srv/laundry:/data`) | The entrypoint changes the owner of the folder and the `laundry.db*` files to `PUID:PGID`. Set `-e PUID=$(id -u) -e PGID=$(id -g)` so they stay owned by your host user (handy for backups). |
| `docker run --user 1234:1234` | No owner change (no root). The folder must already be writable for that UID: `sudo chown 1234:1234 /srv/laundry`. |

If the folder is not writable, the app stops at startup and names the path, the owner and the
`chown` command to fix it.

Run CLI commands as the app user, otherwise root may create database files the app can't write:
`docker exec -it --user 1000:1000 laundry flask --app laundry create-user ...` (use your `PUID:PGID`).
A restart fixes the ownership again.

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
| `PUID`, `PGID` | `1000` | Docker only: user/group the app runs as and that owns `/data` |

## Command line

```bash
flask --app laundry create-user <username> [--name "Display name"] [--admin]   # asks for the password
flask --app laundry make-admin <username>
flask --app laundry seed-demo                                                  # demo data, development only
```

In Docker: `docker exec -it --user 1000:1000 laundry flask --app laundry create-user ...` (see *Volumes and permissions*)

## Development

Architecture, data model, routes, security notes and conventions: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
Guidance for Claude Code: [CLAUDE.md](CLAUDE.md).
