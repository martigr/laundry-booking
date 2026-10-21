"""Calendar subscription: the user's own bookings as an iCalendar feed (RFC 5545).

Calendar apps can't log in, so the feed URL carries a secret token (`users.calendar_token`).
The profile creates, renews and deletes it; a renewed token makes the old URL return 404.
"""
import secrets
from datetime import UTC, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, current_app

from . import slots
from .bookings import group_blocks
from .db import get_db
from .notifications import link
from .slots import SLOT_NAMES, parse_day

bp = Blueprint('ical', __name__)

KEEP_PAST = timedelta(weeks=4)  # past bookings stay in the feed this long
REFRESH = 'PT1H'                # refresh interval suggested to calendar apps (Google ignores it)


def new_token():
    return secrets.token_urlsafe(32)


def feed_url(token):
    return link(f'/calendar/{token}.ics')


def webcal_url(token):
    """Same URL with the webcal scheme: tapping it on an iPhone opens "subscribe to calendar"."""
    return 'webcal://' + feed_url(token).split('://', 1)[1]


def escape(text):
    return text.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def fold(line):
    """Split a content line into lines of at most 75 octets; continuation lines start with a space."""
    data = line.encode()
    parts = []
    limit = 75
    while len(data) > limit:
        cut = limit
        while data[cut] & 0xC0 == 0x80:  # don't split a UTF-8 character
            cut -= 1
        parts.append(data[:cut])
        data = data[cut:]
        limit = 74  # the leading space counts
    parts.append(data)
    return '\r\n '.join(p.decode() for p in parts)


def utc(local, tz):
    """Naive local time of the building as an iCalendar UTC timestamp."""
    return local.replace(tzinfo=tz).astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')


def build_feed(user_id):
    tz = ZoneInfo(current_app.config['TIMEZONE'])
    current = slots.now()
    host = urlsplit(current_app.config['BASE_URL']).hostname or 'localhost'
    rows = get_db().execute('SELECT id, day, slot FROM reservations WHERE user_id = ? AND day >= ?',
                            (user_id, (current.date() - KEEP_PAST).isoformat())).fetchall()
    rows = sorted(({'id': r['id'], 'day': parse_day(r['day']), 'slot': r['slot']} for r in rows),
                  key=lambda r: (r['day'], SLOT_NAMES.index(r['slot'])))

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//laundry-booking//Waschkueche//DE',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'X-WR-CALNAME:Waschküche',
        f'REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}',
        f'X-PUBLISHED-TTL:{REFRESH}',
    ]
    for b in group_blocks(rows):
        # Adjacent slots are one event; its UID changes when the block does, and calendar apps
        # replace a subscribed calendar as a whole, so the old event simply disappears
        lines += [
            'BEGIN:VEVENT',
            f'UID:waschkueche-{"-".join(map(str, b["ids"]))}@{host}',
            f'DTSTAMP:{utc(current, tz)}',
            f'DTSTART:{utc(b["start"], tz)}',
            f'DTEND:{utc(b["end"], tz)}',
            f'SUMMARY:{escape("Waschküche: " + b["name"])}',
            f'DESCRIPTION:{escape("Gebucht in der Waschküche-App: " + link("/"))}',
            'TRANSP:OPAQUE',
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return ''.join(fold(line) + '\r\n' for line in lines)


@bp.route('/calendar/<token>.ics')
def feed(token):
    row = get_db().execute('SELECT id FROM users WHERE calendar_token = ?', (token,)).fetchone()
    if row is None:
        abort(404)
    return Response(build_feed(row['id']), mimetype='text/calendar',
                    headers={'Content-Disposition': 'inline; filename="waschkueche.ics"'})
