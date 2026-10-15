"""Week and month views, booking and cancelling."""
import sqlite3
from calendar import monthrange
from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import slots
from .auth import login_required
from .db import get_db
from .slots import MONTHS, SLOT_NAMES, WEEKDAYS, WEEKDAYS_LONG, fmt_day, last_bookable_day, parse_day, slot_bounds

bp = Blueprint('bookings', __name__)


def load_reservations(start, end):
    rows = get_db().execute("""
        SELECT r.id, r.day, r.slot, r.user_id, u.display_name
        FROM reservations r JOIN users u ON u.id = r.user_id
        WHERE r.day BETWEEN ? AND ?
    """, (start.isoformat(), end.isoformat())).fetchall()
    return {(r['day'], r['slot']): r for r in rows}


def build_cell(day, slot_name, res, current, last_day):
    """Everything the templates need to render one slot; keep decisions here, not in Jinja."""
    start, end = slot_bounds(day, slot_name)
    past = end <= current
    mine = bool(res) and res['user_id'] == g.user['id']
    if res:
        state = 'mine' if mine else 'taken'
    elif past:
        state = 'past'
    elif day > last_day:
        state = 'locked'
    else:
        state = 'free'
    return {
        'day': day.isoformat(),
        'slot': slot_name,
        'time': slots.slot_time(slot_name),
        'short': slots.slot_time_short(slot_name),
        'past': past,
        'running': start <= current < end,
        'res': res,
        'mine': mine,
        'state': state,
        'bookable': state == 'free',
        'cancelable': bool(res) and not past and (mine or bool(g.user['is_admin'])),
    }


def build_days(first, count, current):
    today = current.date()
    last_day = last_bookable_day(today)
    res = load_reservations(first, first + timedelta(days=count - 1))
    days = []
    for i in range(count):
        d = first + timedelta(days=i)
        days.append({
            'date': d,
            'wd': WEEKDAYS[d.weekday()],
            'wd_long': WEEKDAYS_LONG[d.weekday()],
            'is_today': d == today,
            'is_past': d < today,
            'cells': [build_cell(d, name, res.get((d.isoformat(), name)), current, last_day)
                      for name in SLOT_NAMES],
        })
    return days


def sidebar_info(current):
    """Who is in the laundry room right now, the next free slot and my upcoming bookings."""
    today = current.date()
    last_day = last_bookable_day(today)
    res = load_reservations(today, last_day)

    running = None
    next_free = None
    d = today
    while d <= last_day and next_free is None:
        for name in SLOT_NAMES:
            start, end = slot_bounds(d, name)
            if end <= current:
                continue
            r = res.get((d.isoformat(), name))
            if start <= current and r:
                running = {'res': r, 'end': end, 'mine': r['user_id'] == g.user['id'],
                           'progress': int((current - start) / (end - start) * 100)}
            if r is None:
                next_free = {'slot': name, 'label': 'heute' if d == today else fmt_day(d), 'week': d.isoformat()}
                break
        d += timedelta(days=1)

    rows = get_db().execute(
        'SELECT id, day, slot FROM reservations WHERE user_id = ? AND day >= ?',
        (g.user['id'], today.isoformat())).fetchall()
    upcoming = []
    for r in rows:
        day = parse_day(r['day'])
        if slot_bounds(day, r['slot'])[1] > current:
            upcoming.append({'id': r['id'], 'day': day, 'slot': r['slot'], 'wd': WEEKDAYS[day.weekday()],
                             'label': 'heute' if day == today else fmt_day(day)})
    upcoming.sort(key=lambda u: (u['day'], SLOT_NAMES.index(u['slot'])))

    return {'running': running, 'next_free': next_free, 'upcoming': upcoming[:6]}


@bp.route('/')
@login_required
def index():
    current = slots.now()
    ref = parse_day(request.args.get('week')) or current.date()
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)

    if monday.month == sunday.month:
        title = f'{monday.day}. – {sunday.day}. {MONTHS[monday.month - 1]}'
    else:
        title = f'{monday.day}. {MONTHS[monday.month - 1]} – {sunday.day}. {MONTHS[sunday.month - 1]}'

    return render_template(
        'index.html',
        days=build_days(monday, 7, current),
        title=title,
        kicker=f'KW {monday.isocalendar().week} · {sunday.year}',
        prev_week=(monday - timedelta(days=7)).isoformat(),
        next_week=(monday + timedelta(days=7)).isoformat(),
        this_week=monday.isoformat(),
        month_param=monday.strftime('%Y-%m'),
        **sidebar_info(current),
    )


@bp.route('/month')
@login_required
def month():
    current = slots.now()
    today = current.date()
    try:
        y, m = map(int, request.args.get('month', '').split('-'))
        first = date(y, m, 1)
    except ValueError:
        first = today.replace(day=1)
    last = first.replace(day=monthrange(first.year, first.month)[1])

    return render_template(
        'month.html',
        days=build_days(first, last.day, current),
        leading=first.weekday(),
        title=f'{MONTHS[first.month - 1]} {first.year}',
        prev_month=(first - timedelta(days=1)).strftime('%Y-%m'),
        next_month=(last + timedelta(days=1)).strftime('%Y-%m'),
        this_month=today.strftime('%Y-%m'),
        week_param=today.isoformat() if first <= today <= last else first.isoformat(),
    )


def back_to_calendar():
    target = request.form.get('next', '')
    # Only local paths; '//host' and '/\host' would leave the site
    if target.startswith('/') and not target.startswith(('//', '/\\')):
        return redirect(target)
    return redirect(url_for('bookings.index'))


@bp.route('/book', methods=['POST'])
@login_required
def book():
    day = parse_day(request.form.get('day'))
    slot = request.form.get('slot')
    if day is None or slot not in SLOT_NAMES:
        abort(400)

    current = slots.now()
    label = f'{fmt_day(day)} · {slot}'
    if slot_bounds(day, slot)[1] <= current:
        flash(f'{label} liegt in der Vergangenheit.', 'warn')
    elif day > last_bookable_day(current.date()):
        flash(f'{label} kann noch nicht gebucht werden.', 'warn')
    else:
        try:
            # UNIQUE (day, slot) decides who was first, also for simultaneous clicks
            with get_db() as con:
                con.execute('INSERT INTO reservations (day, slot, user_id) VALUES (?, ?, ?)',
                            (day.isoformat(), slot, g.user['id']))
            flash(f'Gebucht: {label}', 'ok')
        except sqlite3.IntegrityError:
            flash(f'{label} ist leider schon vergeben.', 'warn')
    return back_to_calendar()


@bp.route('/cancel/<int:rid>', methods=['POST'])
@login_required
def cancel(rid):
    con = get_db()
    r = con.execute('SELECT * FROM reservations WHERE id = ?', (rid,)).fetchone()
    if r is None:
        abort(404)
    if r['user_id'] != g.user['id'] and not g.user['is_admin']:
        abort(403)
    day = parse_day(r['day'])
    if slot_bounds(day, r['slot'])[1] <= slots.now():
        flash('Vergangene Buchungen können nicht storniert werden.', 'warn')
    else:
        with con:
            con.execute('DELETE FROM reservations WHERE id = ?', (rid,))
        flash(f'Storniert: {fmt_day(day)} · {r["slot"]}', 'ok')
    return back_to_calendar()


@bp.app_template_filter('slot_time')
def slot_time_filter(slot_name):
    return slots.slot_time(slot_name)
