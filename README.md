# Laundry Booking System

A Flask-based web application for managing shared laundry room reservations in apartment buildings.

## Features

- Login per household (user with a free display name); "stay signed in" option
- Week view (default) and month view, usable on phones
- Daily time slots: Vormittag 07–12, Nachmittag 12–17, Abend 17–22
- One click on a free slot books it, one click on your own booking cancels it
- "Now in the laundry room" card, next free slot and your upcoming bookings
- No quota: whoever is first gets the slot; past slots can't be booked or cancelled
- Minimal admin area: create/delete users, reset passwords, set how many weeks ahead bookings are allowed; admins can cancel any booking
- CSRF protection on all forms

## Requirements

- Python 3.13+
- Flask
- SQLite3 (built-in)

## Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Running Locally (Development)

Use the built-in Flask server only for development. From the `laundery` folder:

```bash
# set FLASK_APP if needed and run for development
cd laundery
python app.py
```

The app will be available at `http://localhost:5000`.

## Running Locally (Production-like)

Run a production WSGI server on your machine. Example (Unix):

```bash
# using Gunicorn
pip install -r requirements.txt
cd laundery
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

This guide targets Linux-based deployments; on Linux use Gunicorn as shown above. For Windows, use Docker or WSL if needed.

## Docker (Production)

Set a safe secret and run Gunicorn inside the container:

```bash
# build
docker build -t laundery-booking .
# run (replace SECRET_KEY and optionally DATABASE)
docker run -e SECRET_KEY='a-very-long-random-secret' -p 5000:5000 laundery-booking
```

Notes:
- Set `SECRET_KEY` to a long random string in production. Do not commit it to source control.
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` create the first admin when the database has no users yet.
- `APP_TIMEZONE` (default `Europe/Zurich`) decides what "today" and "now" mean.
- You can set `DATABASE` to point to a persistent file or external DB.
- The app exposes `/health` for health checks.

## Database

The app uses SQLite with automatic schema initialization. The database file (`reservations.db`) is created on first run.

Databases from the first version (bookings per apartment) are migrated automatically on startup: every user keeps their login, the apartment name becomes the display name and each booking goes to the user of that apartment. Afterwards, give yourself admin rights:

```bash
cd laundery
flask --app app make-admin <username>
```

## Users

Admins create users in the app under *Verwaltung*. From the command line:

```bash
cd laundery
flask --app app create-user ralph --name "Ralph" --admin   # asks for the password
python seed.py                                            # demo users alice (admin) and bob
```

## Development

Architecture, data model, routes, security notes and conventions: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Guidance for Claude Code: [CLAUDE.md](CLAUDE.md).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
