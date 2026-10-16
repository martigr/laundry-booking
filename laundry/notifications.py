"""Mail notifications.

Everything goes through the `notifications` table (an outbox): views and the reminder scan only
queue rows, `flask send-notifications` sends them. A request never waits for the mail server, a
failed send is retried, and `dedupe_key` makes sure nobody gets the same mail twice.
"""
from datetime import timedelta

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer

from . import mail, slots
from .db import get_db
from .slots import fmt_day, parse_day, slot_bounds, slot_time

REMINDER_CHOICES = [
    (0, 'Keine Erinnerung'),
    (30, '30 Minuten vorher'),
    (60, '1 Stunde vorher'),
    (120, '2 Stunden vorher'),
    (1440, '1 Tag vorher'),
]
FREED_WINDOW = timedelta(days=7)   # only announce freed slots that start within this window
RESET_VALID = timedelta(hours=1)
MAX_ATTEMPTS = 5
KEEP_SENT = timedelta(days=30)


# --- Links and tokens ------------------------------------------------------------------------

def link(path):
    return current_app.config['BASE_URL'].rstrip('/') + path


def _unsubscribe_serializer():
    return URLSafeSerializer(current_app.config['SECRET_KEY'], salt='unsubscribe')


def unsubscribe_token(user_id, kind):
    return _unsubscribe_serializer().dumps({'u': user_id, 'k': kind})


def read_unsubscribe_token(token):
    """(user_id, kind) or None."""
    try:
        data = _unsubscribe_serializer().loads(token)
        return data['u'], data['k']
    except (BadSignature, KeyError, TypeError):
        return None


def _reset_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='password-reset')


def reset_token(user):
    # The hash fragment makes the link single-use: it stops working once the password changes
    return _reset_serializer().dumps({'u': user['id'], 'h': user['password'][-16:]})


def read_reset_token(token):
    """User id if the token is valid, unexpired and unused, else None."""
    try:
        data = _reset_serializer().loads(token, max_age=int(RESET_VALID.total_seconds()))
    except (BadSignature, TypeError):
        return None
    row = get_db().execute('SELECT id, password FROM users WHERE id = ?', (data.get('u'),)).fetchone()
    if row is None or row['password'][-16:] != data.get('h'):
        return None
    return row['id']


# --- Queueing --------------------------------------------------------------------------------

def _queue(con, dedupe_key, to_addr, subject, body, expires_at=None):
    con.execute(
        'INSERT OR IGNORE INTO notifications (dedupe_key, to_addr, subject, body, created_at, expires_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (dedupe_key, to_addr, subject, body, slots.now().isoformat(timespec='seconds'),
         expires_at.isoformat(timespec='seconds') if expires_at else None))


def _footer(user_id, kind, what):
    return (f'\n--\nDu bekommst diese Mail, weil du {what} eingeschaltet hast.\n'
            f'Abbestellen: {link("/abbestellen/" + unsubscribe_token(user_id, kind))}\n'
            f'Einstellungen: {link("/profile")}\n')


def queue_freed_slots(con, freed, cancelled_by):
    """Bookings were cancelled: tell everyone who wants to know about freed slots.

    `freed` is a list of (reservation_id, day, slot). Subscribers get one mail per day,
    so cancelling a whole day doesn't send three mails.
    """
    if not mail.is_configured():
        return
    now = slots.now()
    by_day = {}
    for rid, day, slot in freed:
        start = slot_bounds(day, slot)[0]
        if now < start <= now + FREED_WINDOW:
            by_day.setdefault(day, []).append((rid, slot, start))
    if not by_day:
        return
    users = con.execute("""
        SELECT id, display_name, email FROM users
        WHERE notify_freed = 1 AND email IS NOT NULL AND email != '' AND id != ?
    """, (cancelled_by,)).fetchall()
    for day, entries in sorted(by_day.items()):
        entries.sort(key=lambda e: e[2])
        names = [slot for _, slot, _ in entries]
        lines = ''.join(f'- {slot} ({slot_time(slot)} Uhr)\n' for slot in names)
        verb = 'ist' if len(names) == 1 else 'sind'
        for u in users:
            body = (f'Hallo {u["display_name"]}\n\n'
                    f'Am {fmt_day(day)} {verb} wieder frei:\n{lines}\n'
                    f'Jetzt buchen: {link("/?week=" + day.isoformat())}\n\n'
                    f'Wer zuerst bucht, wäscht.\n'
                    + _footer(u['id'], 'freed', '„Slot frei geworden“'))
            _queue(con, f'freed:{entries[0][0]}:{u["id"]}', u['email'],
                   f'Waschküche: {fmt_day(day)} {" + ".join(names)} {verb} frei', body,
                   expires_at=entries[-1][2])


def queue_due_reminders(con):
    """Queue reminders for bookings that start within each user's chosen lead time."""
    now = slots.now()
    longest = max(m for m, _ in REMINDER_CHOICES)
    rows = con.execute("""
        SELECT r.id, r.day, r.slot, u.id AS user_id, u.display_name, u.email, u.reminder_minutes
        FROM reservations r JOIN users u ON u.id = r.user_id
        WHERE u.reminder_minutes > 0 AND u.email IS NOT NULL AND u.email != ''
          AND r.day BETWEEN ? AND ?
    """, (now.date().isoformat(), (now + timedelta(minutes=longest)).date().isoformat())).fetchall()
    for r in rows:
        day = parse_day(r['day'])
        start = slot_bounds(day, r['slot'])[0]
        if not start - timedelta(minutes=r['reminder_minutes']) <= now < start:
            continue
        when = 'heute' if day == now.date() else ('morgen' if day == now.date() + timedelta(days=1) else fmt_day(day))
        body = (f'Hallo {r["display_name"]}\n\n'
                f'Erinnerung: Du hast die Waschküche {when} gebucht,\n'
                f'{fmt_day(day)}, {r["slot"]} ({slot_time(r["slot"])} Uhr).\n\n'
                f'Kommt etwas dazwischen? Storniere den Slot, damit andere ihn nutzen können:\n'
                f'{link("/?week=" + day.isoformat())}\n'
                + _footer(r['user_id'], 'reminder', 'Erinnerungen'))
        _queue(con, f'reminder:{r["id"]}', r['email'],
               f'Waschküche: {when} {r["slot"]} ({slot_time(r["slot"])})', body, expires_at=start)


def queue_password_reset(con, user):
    now = slots.now()
    body = (f'Hallo {user["display_name"]}\n\n'
            f'Für dein Konto „{user["username"]}“ wurde ein neues Passwort angefordert.\n'
            f'Über diesen Link setzt du es (gültig für 1 Stunde):\n\n'
            f'{link("/passwort-zuruecksetzen/" + reset_token(user))}\n\n'
            f'Warst du das nicht? Dann ignoriere diese Mail, dein Passwort bleibt unverändert.\n')
    _queue(con, f'reset:{user["id"]}:{now.isoformat(timespec="seconds")}', user['email'],
           'Waschküche: Passwort zurücksetzen', body, expires_at=now + RESET_VALID)


# --- Sending ---------------------------------------------------------------------------------

def send_pending():
    """Send queued mails. Returns (sent, failed)."""
    con = get_db()
    now = slots.now().isoformat(timespec='seconds')
    rows = con.execute("""
        SELECT * FROM notifications
        WHERE sent_at IS NULL AND attempts < ? AND (expires_at IS NULL OR expires_at > ?)
        ORDER BY id
    """, (MAX_ATTEMPTS, now)).fetchall()
    sent = failed = 0
    for n in rows:
        try:
            mail.deliver(n['to_addr'], n['subject'], n['body'])
        except Exception as e:  # any SMTP/network error: keep it queued and retry next run
            failed += 1
            current_app.logger.warning('Mail %s to %s failed: %s', n['id'], n['to_addr'], e)
            with con:
                con.execute('UPDATE notifications SET attempts = attempts + 1, last_error = ? WHERE id = ?',
                            (str(e)[:500], n['id']))
        else:
            sent += 1
            with con:
                con.execute('UPDATE notifications SET sent_at = ?, attempts = attempts + 1 WHERE id = ?',
                            (slots.now().isoformat(timespec='seconds'), n['id']))
    return sent, failed


def run_once():
    """One pass of the notifier: queue due reminders, send everything pending, prune old rows."""
    if not mail.is_configured():
        return 0, 0
    con = get_db()
    with con:
        queue_due_reminders(con)
        con.execute('DELETE FROM notifications WHERE created_at < ?',
                    ((slots.now() - KEEP_SENT).isoformat(timespec='seconds'),))
    return send_pending()
