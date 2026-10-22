"""Calendar subscription: the bookings of the user's household as an iCalendar feed (RFC 5545).

A user in a household (`users.household_id`) gets the bookings of all its members, everyone else
only their own. Calendar apps can't log in, so the feed URL carries a secret token
(`users.calendar_token`, one per user). The profile creates, renews and deletes it; a renewed token
makes the old URL return 404.
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


def build_feed(user):
    tz = ZoneInfo(current_app.config['TIMEZONE'])
    current = slots.now()
    host = urlsplit(current_app.config['BASE_URL']).hostname or 'localhost'
    # household_id = NULL matches nothing, so users without a household get only their own
    rows = get_db().execute("""
        SELECT r.id, r.day, r.slot, r.user_id, u.display_name
        FROM reservations r JOIN users u ON u.id = r.user_id
        WHERE (r.user_id = ? OR u.household_id = ?) AND r.day >= ?
    """, (user['id'], user['household_id'], (current.date() - KEEP_PAST).isoformat())).fetchall()
    by_user = {}
    for r in sorted(rows, key=lambda r: (r['day'], SLOT_NAMES.index(r['slot']))):
        by_user.setdefault(r['user_id'], []).append(
            {'id': r['id'], 'day': parse_day(r['day']), 'slot': r['slot'], 'who': r['display_name']})
    blocks = []
    for own in by_user.values():  # per member, so adjacent slots of two members stay two events
        for b in group_blocks(own):
            # In a household feed the name says who booked
            b['summary'] = f'Waschküche: {b["name"]}' + (f' ({own[0]["who"]})' if user['household_id'] else '')
            blocks.append(b)
    blocks.sort(key=lambda b: b['start'])

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
    for b in blocks:
        # Adjacent slots are one event; its UID changes when the block does, and calendar apps
        # replace a subscribed calendar as a whole, so the old event simply disappears
        lines += [
            'BEGIN:VEVENT',
            f'UID:waschkueche-{"-".join(map(str, b["ids"]))}@{host}',
            f'DTSTAMP:{utc(current, tz)}',
            f'DTSTART:{utc(b["start"], tz)}',
            f'DTEND:{utc(b["end"], tz)}',
            f'SUMMARY:{escape(b["summary"])}',
            f'DESCRIPTION:{escape("Gebucht in der Waschküche-App: " + link("/"))}',
            'TRANSP:OPAQUE',
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return ''.join(fold(line) + '\r\n' for line in lines)


@bp.route('/calendar/<token>.ics')
def feed(token):
    user = get_db().execute('SELECT * FROM users WHERE calendar_token = ?', (token,)).fetchone()
    if user is None:
        abort(404)
    return Response(build_feed(user), mimetype='text/calendar',
                    headers={'Content-Disposition': 'inline; filename="waschkueche.ics"'})
