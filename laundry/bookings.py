"""Week and month views, booking and cancelling."""
import sqlite3
from calendar import monthrange
from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import notifications, slots
from .auth import login_required
from .db import get_db
from .slots import MONTHS, SLOT_NAMES, WEEKDAYS, WEEKDAYS_LONG, fmt_day, last_bookable_day, parse_day, slot_bounds

bp = Blueprint('bookings', __name__)


def household_ids():
    """Ids of the current user and their housemates: their bookings count as the user's own."""
    if 'household_ids' not in g:
        g.household_ids = {r['id'] for r in get_db().execute(
            'SELECT id FROM users WHERE id = ? OR household_id = ?', (g.user['id'], g.user['household_id']))}
    return g.household_ids


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
    ours = bool(res) and res['user_id'] in household_ids()  # booked by me or a housemate
    if res:
        state = 'mine' if ours else 'taken'
    elif past:
        state = 'past'
    elif day > last_day:
        state = 'locked'
    else:
        state = 'free'
    return {
        'day': day.isoformat(),
        'slot': slot_name,
        'label': f'{slots.fmt_day(day)} · {slot_name}',
        'time': slots.slot_time(slot_name),
        'short': slots.slot_time_short(slot_name),
        'past': past,
        'running': start <= current < end,
        'res': res,
        'mine': mine,
        'ours': ours,
        'state': state,
        'bookable': state == 'free',
        'cancelable': bool(res) and not past and (ours or bool(g.user['is_admin'])),
    }


def build_days(first, count, current):
    today = current.date()
    last_day = last_bookable_day(today)
    res = load_reservations(first, first + timedelta(days=count - 1))
    days = []
    for i in range(count):
        d = first + timedelta(days=i)
        cells = [build_cell(d, name, res.get((d.isoformat(), name)), current, last_day) for name in SLOT_NAMES]
        free = sum(c['bookable'] for c in cells)
        days.append({
            'date': d,
            'wd': WEEKDAYS[d.weekday()],
            'wd_long': WEEKDAYS_LONG[d.weekday()],
            'is_today': d == today,
            'is_past': d < today,
            'cells': cells,
            # Button for /book-day; with a single free slot it would be the same as clicking that slot
            'book_day': ('Ganzer Tag' if free == len(SLOT_NAMES) else 'Alle freien') if free >= 2 else None,
        })
    return days


def slot_list(names):
    """'ganzer Tag' for all slots of a day, otherwise the names, e.g. 'Vormittag, Abend'."""
    return 'ganzer Tag' if list(names) == SLOT_NAMES else ', '.join(names)


def group_blocks(rows):
    """Merge reservations of consecutive slots on the same day into blocks.

    `rows` need `id`, `day` (date) and `slot`, sorted by day and slot order.
    """
    blocks = []
    for r in rows:
        last = blocks[-1] if blocks else None
        if last and last['day'] == r['day'] and SLOT_NAMES.index(last['slots'][-1]) + 1 == SLOT_NAMES.index(r['slot']):
            last['slots'].append(r['slot'])
            last['ids'].append(r['id'])
        else:
            blocks.append({'day': r['day'], 'slots': [r['slot']], 'ids': [r['id']]})
    for b in blocks:
        start, end = slot_bounds(b['day'], b['slots'][0])[0], slot_bounds(b['day'], b['slots'][-1])[1]
        if b['slots'] == SLOT_NAMES:
            b['name'] = 'Ganzer Tag'
        elif len(b['slots']) > 1:
            b['name'] = f"{b['slots'][0]} – {b['slots'][-1]}"
        else:
            b['name'] = b['slots'][0]
        b.update(start=start, end=end, time=f'{start:%H:%M} – {end:%H:%M}')
    return blocks


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
                # The running booking plus the same user's adjacent slots that day
                same = [{'id': x['id'], 'day': d, 'slot': n} for n in SLOT_NAMES
                        if (x := res.get((d.isoformat(), n))) and x['user_id'] == r['user_id']]
                block = next(b for b in group_blocks(same) if r['id'] in b['ids'])
                running = {'res': r, 'name': block['name'], 'end': block['end'],
                           'mine': r['user_id'] == g.user['id'],
                           'progress': int((current - block['start']) / (block['end'] - block['start']) * 100)}
            if r is None:
                next_free = {'slot': name, 'label': 'heute' if d == today else fmt_day(d), 'week': d.isoformat()}
                break
        d += timedelta(days=1)

    ids = sorted(household_ids())
    rows = get_db().execute(f"""
        SELECT r.id, r.day, r.slot, r.user_id, u.display_name
        FROM reservations r JOIN users u ON u.id = r.user_id
        WHERE r.user_id IN ({", ".join("?" * len(ids))}) AND r.day >= ?
    """, (*ids, today.isoformat())).fetchall()
    by_user = {}
    for r in sorted(rows, key=lambda r: (r['day'], SLOT_NAMES.index(r['slot']))):
        day = parse_day(r['day'])
        if slot_bounds(day, r['slot'])[1] > current:
            by_user.setdefault(r['user_id'], []).append(
                {'id': r['id'], 'day': day, 'slot': r['slot'], 'who': r['display_name']})
    upcoming = []
    for uid, own in by_user.items():  # per member, so a housemate's adjacent slot stays a separate block
        for b in group_blocks(own):
            b['who'] = None if uid == g.user['id'] else own[0]['who']
            upcoming.append(b)
    upcoming.sort(key=lambda b: b['start'])
    for b in upcoming:
        b.update(wd=WEEKDAYS[b['day'].weekday()], label='heute' if b['day'] == today else fmt_day(b['day']))

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
        if parse_day(first.isoformat()) is None:
            raise ValueError('out of range')
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
    # Only local paths. '//host' leaves the site, and browsers read '\' as '/' and drop tabs and
    # line breaks, so '/\host' and '/<tab>/host' would too
    if target.startswith('/') and not target.startswith('//') and '\\' not in target and target.isprintable():
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


@bp.route('/book-day', methods=['POST'])
@login_required
def book_day():
    """Book every free slot of a day that hasn't ended yet; slots someone else has stay theirs."""
    day = parse_day(request.form.get('day'))
    if day is None:
        abort(400)

    current = slots.now()
    open_slots = [name for name in SLOT_NAMES if slot_bounds(day, name)[1] > current]
    if not open_slots:
        flash(f'{fmt_day(day)} liegt in der Vergangenheit.', 'warn')
    elif day > last_bookable_day(current.date()):
        flash(f'{fmt_day(day)} kann noch nicht gebucht werden.', 'warn')
    else:
        with get_db() as con:
            # One transaction; UNIQUE (day, slot) skips slots that are already booked
            booked = [name for name in open_slots if con.execute(
                'INSERT INTO reservations (day, slot, user_id) VALUES (?, ?, ?) ON CONFLICT (day, slot) DO NOTHING',
                (day.isoformat(), name, g.user['id'])).rowcount]
            # Slots of housemates count as already booked by us, not as taken
            others = [r['slot'] for r in con.execute(
                'SELECT slot, user_id FROM reservations WHERE day = ?', (day.isoformat(),))
                if r['slot'] in open_slots and r['user_id'] not in household_ids()]
        taken = [name for name in open_slots if name in others]
        if booked:
            note = f' ({", ".join(taken)} {"war" if len(taken) == 1 else "waren"} schon vergeben)' if taken else ''
            flash(f'Gebucht: {fmt_day(day)} · {slot_list(booked)}{note}', 'ok')
        elif taken:
            flash(f'Am {fmt_day(day)} ist nichts mehr frei.', 'warn')
        else:
            flash(f'{fmt_day(day)} ist schon für {"euch" if len(household_ids()) > 1 else "dich"} gebucht.', 'ok')
    return back_to_calendar()


def cancel_reservations(ids):
    """Cancel the given reservations: 404 if one is missing, 403 if one isn't your household's (unless admin)."""
    con = get_db()
    rows = con.execute(f'SELECT * FROM reservations WHERE id IN ({", ".join("?" * len(ids))})', ids).fetchall()
    if len(rows) != len(ids):
        abort(404)
    if not g.user['is_admin'] and any(r['user_id'] not in household_ids() for r in rows):
        abort(403)
    current = slots.now()
    rows = [r for r in rows if slot_bounds(parse_day(r['day']), r['slot'])[1] > current]
    if not rows:
        flash('Vergangene Buchungen können nicht storniert werden.', 'warn')
        return
    with con:
        con.executemany('DELETE FROM reservations WHERE id = ?', [(r['id'],) for r in rows])
        notifications.queue_freed_slots(con, [(r['id'], parse_day(r['day']), r['slot']) for r in rows],
                                        cancelled_by=g.user['id'])
    by_day = {}
    for r in sorted(rows, key=lambda r: (r['day'], SLOT_NAMES.index(r['slot']))):
        by_day.setdefault(r['day'], []).append(r['slot'])
    flash('Storniert: ' + '; '.join(f'{fmt_day(parse_day(d))} · {slot_list(names)}' for d, names in by_day.items()),
          'ok')


@bp.route('/cancel/<int:rid>', methods=['POST'])
@login_required
def cancel(rid):
    cancel_reservations([rid])
    return back_to_calendar()


@bp.route('/cancel', methods=['POST'])
@login_required
def cancel_many():
    """Cancel a block of bookings at once (form field `id`, repeated)."""
    ids = sorted(set(request.form.getlist('id', type=int)))
    if not ids:
        abort(400)
    cancel_reservations(ids)
    return back_to_calendar()
