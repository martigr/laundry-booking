# Laundry Booking

A small web app for booking the shared washing machine in an apartment building.
Project page: https://martigr.github.io/laundry-booking/
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

- Login per resident (user with a free display name), several residents can form a household; "stay signed in" option
- Week view (default) and month overview, usable on phones
- Daily slots: Vormittag 07–12, Nachmittag 12–17, Abend 17–22
- One click on a free slot books it, one click on your own booking cancels it
- "Ganzer Tag" books all free slots of a day at once
- Adjacent bookings show up as one block in your upcoming bookings (and cancel together)
- "Now in the laundry room" card, next free slot and your upcoming bookings
- Past slots can't be booked or cancelled; booking is possible up to N weeks ahead
- Minimal admin area: create/delete users, group them into households, reset passwords, set the booking horizon;
  admins can cancel any booking
- Mail notifications, each switched on per user in the profile: reminder before your own
  booking (30 min to 1 day ahead), "a slot became free" in the next 7 days, "forgot password";
  addresses are confirmed by a link first; every mail has a one-click unsubscribe link
- Calendar subscription: your own bookings, or those of your whole household, as an iCal feed for the phone's calendar
  (iPhone, Android with ICSx⁵, Google Calendar); the secret link can be renewed or deleted in the profile
- Security: CSRF protection on all forms, login throttling, strict security headers (CSP),
  sessions end on password change; see [SECURITY.md](SECURITY.md)
- German UI

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

## Production (Docker Compose)

```bash
cp .env.example .env && chmod 600 .env   # fill in SECRET_KEY, admin, BASE_URL and the mail settings
docker compose up -d --build
```

The app listens on `127.0.0.1:5000` only; put an HTTPS reverse proxy in front of it (see *HTTPS* below).

This starts two containers from the same image and the same `/data` volume:

- `web`: the app (Gunicorn) on port 5000
- `notifier`: runs `flask --app laundry send-notifications --loop 60`, which queues due reminders and
  sends all waiting mails once a minute. Without mail settings it just idles.

### Prebuilt image

Every release publishes the image to the GitHub Container Registry (`linux/amd64` and
`linux/arm64`), so you don't need to build it yourself:

```bash
docker pull ghcr.io/martigr/laundry-booking:1   # tags: latest, 1, 1.0, 1.0.0
```

To use it with Docker Compose, replace `build: .` and `image: laundry-booking` in both services of
`docker-compose.yml` with `image: ghcr.io/martigr/laundry-booking:1`, then `docker compose up -d`.
In the `docker run` example below, use `ghcr.io/martigr/laundry-booking:1` instead of building.
The tag `1` follows all 1.x releases; pin a full version (`1.0.0`) to update only by hand.

### Plain `docker run`

```bash
docker build -t laundry-booking .
docker run -d --name laundry -p 127.0.0.1:5000:5000 \
  -v laundry-data:/data \
  -e SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  -e ADMIN_USERNAME=admin -e ADMIN_PASSWORD='at-least-10-characters' \
  laundry-booking
```

- The app runs as an unprivileged user (UID/GID 1000 by default); the database lives in `/data`.
- `ADMIN_USERNAME`/`ADMIN_PASSWORD` create the first admin when the database has no users yet.
  Afterwards the admin creates all other users in the app under *Verwaltung*.
- Serve it behind HTTPS (see *HTTPS* below) with `SESSION_COOKIE_SECURE=1` and `TRUSTED_PROXIES=1`.
- Keep the `SECRET_KEY`: changing it logs everybody out and invalidates the links in sent mails.
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

### HTTPS (reverse proxy)

The app itself speaks plain HTTP. Put a reverse proxy in front of it that terminates HTTPS, and
keep port 5000 reachable only from that proxy (`127.0.0.1:5000`, as in `docker-compose.yml`).
[Caddy](https://caddyserver.com/) on the same host gets the certificate by itself:

```
# /etc/caddy/Caddyfile
waschkueche.example.ch {
    reverse_proxy 127.0.0.1:5000
}
```

With nginx (certificate e.g. from certbot):

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Then set in `.env`:

- `SESSION_COOKIE_SECURE=1`: the session cookie is only sent over HTTPS.
- `TRUSTED_PROXIES=1`: the app trusts the `X-Forwarded-For`/`-Proto` headers of one proxy. It needs
  them for the real client address (login throttling, logs) and to know the request came over
  HTTPS (then it sends `Strict-Transport-Security`). Use the number of proxies in the chain; leave
  it at `0` when nothing sits in front of the app, otherwise clients can fake their address.
  Without it, every request seems to come from the proxy (or Docker's gateway), so the limit of
  20 failed logins per address applies to all residents together.
- `BASE_URL=https://waschkueche.example.ch` for the links in mails and the calendar link.

### Backup and restore

All data lives in one SQLite file. `flask backup` copies it consistently while the app runs:

```bash
docker compose exec --user 1000:1000 web flask --app laundry backup /data/backup.db
docker compose cp web:/data/backup.db ./laundry-$(date +%F).db
docker compose exec web rm /data/backup.db
```

Run this daily (cron) and keep copies on another machine. Backups contain password hashes and mail
addresses: store them private (`chmod 600`) or encrypted, and delete old ones. Try a restore
once, before you need it:

```bash
docker compose stop
docker compose run --rm --no-deps --entrypoint sh -v "$PWD":/backup web \
  -c 'cp /backup/laundry-2026-10-17.db /data/laundry.db && rm -f /data/laundry.db-wal /data/laundry.db-shm'
docker compose start      # the entrypoint gives the restored file back to the app user
```

Without Docker: `flask --app laundry backup <file>`; to restore, stop the app and replace the
database file (delete `laundry.db-wal` and `laundry.db-shm` next to it).

### Updates

```bash
git pull
docker compose build --pull && docker compose up -d   # --pull also gets security fixes of the base image
```

With the prebuilt image: `docker compose pull && docker compose up -d`. Releases are listed under
*Releases* on GitHub.

Update at least monthly. To hear about security fixes, watch the repository on GitHub
(*Watch → Custom → Security alerts*).

### Logs

`docker compose logs web` shows requests and logins. Failed logins are logged as
`Failed login for 'name' from <address>`, blocked ones as `Login blocked`; after 10 failures for a
username or 20 from one address within 15 minutes, logins are refused for the rest of that time.
Logs contain IP addresses and usernames; `docker-compose.yml` therefore keeps at most 3 × 10 MB per
container. The tokens of mail links (password reset, unsubscribe, confirm address) and of calendar links appear
as `***` in the app's access log. Your reverse proxy logs them in full if it keeps an access log (nginx does
by default, Caddy doesn't): turn it off for these paths or keep those logs short and private.

### Security checklist

Before the app goes online:

- [ ] HTTPS reverse proxy in front, port 5000 not reachable from outside
- [ ] `SESSION_COOKIE_SECURE=1`, `TRUSTED_PROXIES` matches the proxies, `BASE_URL` uses `https://`
- [ ] `SECRET_KEY` long and random (`python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`)
- [ ] `.env` readable only by you (`chmod 600 .env`) and never committed
- [ ] After the first start: log in, change the admin password in the profile and remove
      `ADMIN_PASSWORD` from `.env`
- [ ] `SECURITY_CONTACT` set, so people know whom to tell about a problem
- [ ] Daily backups on another machine, restore tried once
- [ ] Updates at least monthly (`docker compose build --pull`)

### Mail (Infomaniak)

Use a mailbox of your domain, e.g. `waschkueche@example.ch`, and set in `.env`:

```
SMTP_HOST=mail.infomaniak.com
SMTP_PORT=587               # or 465 with SMTP_SECURITY=ssl
SMTP_SECURITY=starttls
SMTP_USER=waschkueche@example.ch
SMTP_PASSWORD=...           # the mailbox password
MAIL_FROM=waschkueche@example.ch   # the mailbox itself or one of its aliases
BASE_URL=https://waschkueche.example.ch
```

Any other SMTP provider works the same way. Mails are queued in the database and sent by the
`notifier`; if the mail server is unreachable they are retried (up to 5 times) and dropped once
they are no longer relevant (e.g. a reminder after the slot started). Residents enter their address
and choose their notifications under *Profil*; they get mails only after confirming the address
with the link in the first mail; the "Passwort vergessen?" link on the login page
only appears when mail is configured.

For local development, [Mailpit](https://mailpit.axllent.org/) catches all mails in a web inbox:
`docker run -d -p 1025:1025 -p 8025:8025 axllent/mailpit`, then `SMTP_HOST=localhost SMTP_PORT=1025
SMTP_SECURITY=none MAIL_FROM=test@example.ch` and open http://localhost:8025.

### Calendar subscription

Every resident can create a personal calendar link under *Profil → Kalender abonnieren*
(`BASE_URL/calendar/<token>.ics`). It contains their own bookings, or, if an admin has put them
into a household (*Verwaltung → Haushalte*), the bookings of all its members, each named. Calendar apps fetch it
themselves, so new bookings show up after the next refresh, not instantly:

- **iPhone/iPad:** tap the `webcal://` link in the profile, or *Settings → Calendar → Accounts →
  Add Account → Other → Add Subscribed Calendar*. iOS refreshes about hourly.
- **Android:** the app [ICSx⁵](https://icsx5.bitfire.at/) subscribes on the device and writes into
  the normal calendar; set the interval to 1 hour.
- **Google Calendar:** *Other calendars → From URL* in the browser. Google fetches the feed from its
  own servers, so the app must be reachable from the internet, and it refreshes only every few
  hours to a day.

The link needs HTTPS with a valid certificate (iOS rejects self-signed ones) and a correct
`BASE_URL`. Anyone with the link sees that household's bookings; "Neuen Link erstellen"
in the profile makes the old one stop working.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | – (required outside debug mode) | Session signing |
| `DATABASE` | `instance/laundry.db` (`/data/laundry.db` in Docker) | SQLite file |
| `APP_TIMEZONE` | `Europe/Zurich` | Defines "today" and "now" |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | – | First admin on an empty database (password at least 10 characters) |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` behind HTTPS |
| `TRUSTED_PROXIES` | `0` | Number of reverse proxies whose `X-Forwarded-For`/`-Proto` headers are trusted (see *HTTPS*) |
| `SECURITY_CONTACT` | – | Contact for security reports in `/.well-known/security.txt`, e.g. `mailto:verwaltung@example.ch` |
| `LOG_LEVEL` | `INFO` | Log level |
| `PUID`, `PGID` | `1000` | Docker only: user/group the app runs as and that owns `/data` |
| `BASE_URL` | `http://localhost:5000` | Public address, used for links in mails and the calendar link |
| `SMTP_HOST`, `SMTP_PORT` | –, `587` | Mail server; mail is off while `SMTP_HOST` or `MAIL_FROM` is empty |
| `SMTP_SECURITY` | `starttls` | `starttls`, `ssl` (port 465) or `none` (local test servers only) |
| `SMTP_USER`, `SMTP_PASSWORD` | – | SMTP login |
| `MAIL_FROM`, `MAIL_FROM_NAME` | –, `Waschküche` | Sender address and name |

## Command line

```bash
flask --app laundry create-user <username> [--name "Display name"] [--admin]   # asks for the password
flask --app laundry make-admin <username>
flask --app laundry seed-demo                                                  # demo data, development only
flask --app laundry send-notifications [--loop SECONDS]                        # queue reminders, send mails
flask --app laundry backup <file>                                              # consistent copy of the database
```

In Docker: `docker exec -it --user 1000:1000 laundry flask --app laundry create-user ...` (see *Volumes and permissions*)

## Development

Architecture, data model, routes, security notes and conventions: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
Reporting vulnerabilities: [SECURITY.md](SECURITY.md).
Guidance for Claude Code: [CLAUDE.md](CLAUDE.md).
The project page lives in `site/` and is deployed to GitHub Pages by `.github/workflows/pages.yml`.

## License

[MIT](LICENSE)
