"""Slot definitions, the clock and German date formatting."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from .db import get_setting

# Bookable slots: (name, start, end). Stored by name, so renaming one orphans existing bookings.
SLOTS = [
    ('Vormittag', time(7, 0), time(12, 0)),
    ('Nachmittag', time(12, 0), time(17, 0)),
    ('Abend', time(17, 0), time(22, 0)),
]
SLOT_NAMES = [s[0] for s in SLOTS]

WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
WEEKDAYS_LONG = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
          'August', 'September', 'Oktober', 'November', 'Dezember']


def now():
    """Current local time of the building (naive). The only clock the app uses; tests replace it."""
    return datetime.now(ZoneInfo(current_app.config['TIMEZONE'])).replace(tzinfo=None)


def slot_bounds(day, slot_name):
    _, start, end = SLOTS[SLOT_NAMES.index(slot_name)]
    return datetime.combine(day, start), datetime.combine(day, end)


def slot_time(slot_name):
    _, start, end = SLOTS[SLOT_NAMES.index(slot_name)]
    return f'{start:%H:%M} – {end:%H:%M}'


def slot_time_short(slot_name):
    _, start, end = SLOTS[SLOT_NAMES.index(slot_name)]
    return f'{start.hour}–{end.hour} Uhr'


# Days accepted from requests; far outside, the calendar's date arithmetic would overflow
MIN_DAY, MAX_DAY = date(2000, 1, 1), date(2999, 12, 31)


def parse_day(value):
    """The date of an ISO string, or None if it isn't one or lies outside MIN_DAY..MAX_DAY."""
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return day if MIN_DAY <= day <= MAX_DAY else None


def fmt_day(d):
    return f'{WEEKDAYS[d.weekday()]} {d.day}. {MONTHS[d.month - 1][:3]}.'


def last_bookable_day(today):
    return today + timedelta(weeks=int(get_setting('horizon_weeks')))
