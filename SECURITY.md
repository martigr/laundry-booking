# Security Policy

Laundry Booking is a small open-source app, maintained in spare time. Security reports are
welcome and taken seriously.

## Supported versions

Only the latest state of the `main` branch gets fixes. If you run an instance, update to it.

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through GitHub:
[Report a vulnerability](https://github.com/martigr/laundry-booking/security/advisories/new)
(repository *Security* tab → *Report a vulnerability*).

Helpful to include:

- what an attacker can do, and which role they need (anonymous, resident, admin)
- steps or a request to reproduce it, and the commit you tested
- if you have one, a suggested fix

You can write in German or English. What to expect:

- a first answer within about a week
- a fix or a plan within about 30 days for confirmed issues, depending on severity
- credit in the advisory and release notes, if you want it

A problem with a **specific installation** (for example a building's instance that isn't
behind HTTPS, or a leaked password) goes to its operator: see `/.well-known/security.txt` of
that instance. Operators set the contact with `SECURITY_CONTACT`.

## Scope

In scope: the code in this repository (the Flask app, `Dockerfile`, `docker-entrypoint.sh`,
`docker-compose.yml`).

Out of scope:

- instances that ignore the [production checklist](README.md#security-checklist)
  (no HTTPS, weak `SECRET_KEY`, port 5000 exposed to the internet)
- denial of service by flooding the server with traffic
- the demo users of `flask seed-demo` (development only)
- reports from automated scanners without a demonstrated impact

Please test only against your own instance (`flask --app laundry --debug seed-demo` gives you
one in a minute), never against someone else's building.

## Security design

How the app protects sessions, forms, passwords and mail links is described in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#security).
