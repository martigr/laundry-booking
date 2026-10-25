"""Notifications by mail and push.

Everything goes through the `notifications` table (an outbox): views and the reminder scan only
queue rows, `flask send-notifications` sends them. A request never waits for the mail server or a
push service, a failed send is retried, and `dedupe_key` makes sure nobody gets the same message
twice. Push messages have one row per device (`push_subscription_id`); their `body` is the JSON
payload the service worker shows.
"""
import json
from datetime import datetime, timedelta

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer

from . import mail, push, slots
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
RESET_INTERVAL = timedelta(minutes=15)  # at most one reset (or verification) mail per user in this time
VERIFY_VALID = timedelta(days=7)
MAX_ATTEMPTS = 5
KEEP_SENT = timedelta(days=30)
PUSH_TTL = timedelta(days=1)  # how long a push service keeps a message for an offline device, if it doesn't expire


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


def _verify_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='verify-email')


def verify_token(user_id, email):
    return _verify_serializer().dumps({'u': user_id, 'e': email})


def read_verify_token(token):
    """(user_id, email) if the token is valid and unexpired, else None."""
    try:
        data = _verify_serializer().loads(token, max_age=int(VERIFY_VALID.total_seconds()))
        return data['u'], data['e']
    except (BadSignature, KeyError, TypeError):
        return None


# --- Queueing --------------------------------------------------------------------------------

def _queue(con, dedupe_key, to_addr, subject, body, expires_at=None, push_subscription_id=None):
    con.execute(
        'INSERT OR IGNORE INTO notifications (dedupe_key, to_addr, subject, body, created_at, expires_at, '
        'push_subscription_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (dedupe_key, to_addr, subject, body, slots.now().isoformat(timespec='seconds'),
         expires_at.isoformat(timespec='seconds') if expires_at else None, push_subscription_id))


def _queue_push(con, dedupe_key, user_id, title, body, url, tag, expires_at):
    """One row per device of the user, so each is retried on its own. `to_addr` is only the push
    service's host (for the logs); the endpoint URL stays in push_subscriptions."""
    payload = json.dumps({'title': title, 'body': body, 'url': url, 'tag': tag}, ensure_ascii=False)
    for s in con.execute('SELECT id, endpoint FROM push_subscriptions WHERE user_id = ?', (user_id,)).fetchall():
        _queue(con, f'{dedupe_key}:push:{s["id"]}', push.service_host(s['endpoint']), title, payload,
               expires_at, s['id'])


def _mail_to(user, switch):
    """Mail for this notification: switched on and a confirmed address."""
    return mail.is_configured() and user[switch] and user['email'] and user['email_verified']


def _push_to(user, switch):
    return push.is_configured() and user[switch]


def _footer(user_id, kind, what):
    return (f'\n--\nDu bekommst diese Mail, weil du {what} eingeschaltet hast.\n'
            f'Abbestellen: {link("/unsubscribe/" + unsubscribe_token(user_id, kind))}\n'
            f'Einstellungen: {link("/profile")}\n')


def queue_freed_slots(con, freed, cancelled_by):
    """Bookings were cancelled: tell everyone who wants to know about freed slots.

    `freed` is a list of (reservation_id, day, slot). Subscribers get one message per day and channel,
    so cancelling a whole day doesn't send three.
    """
    if not (mail.is_configured() or push.is_configured()):
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
        SELECT id, display_name, email, email_verified, notify_freed, freed_push FROM users
        WHERE (notify_freed = 1 OR freed_push = 1) AND id != ?
    """, (cancelled_by,)).fetchall()
    for day, entries in sorted(by_day.items()):
        entries.sort(key=lambda e: e[2])
        names = [slot for _, slot, _ in entries]
        lines = ''.join(f'- {slot} ({slot_time(slot)} Uhr)\n' for slot in names)
        verb = 'ist' if len(names) == 1 else 'sind'
        subject = f'Waschküche: {fmt_day(day)} {" + ".join(names)} {verb} frei'
        url = link('/?week=' + day.isoformat())
        key = f'freed:{entries[0][0]}'
        for u in users:
            if _mail_to(u, 'notify_freed'):
                body = (f'Hallo {u["display_name"]}\n\n'
                        f'Am {fmt_day(day)} {verb} wieder frei:\n{lines}\n'
                        f'Jetzt buchen: {url}\n\n'
                        f'Wer zuerst bucht, wäscht.\n'
                        + _footer(u['id'], 'freed', '„Slot frei geworden“'))
                _queue(con, f'{key}:{u["id"]}', u['email'], subject, body, expires_at=entries[-1][2])
            if _push_to(u, 'freed_push'):
                times = ', '.join(slot_time(slot) for slot in names)
                _queue_push(con, f'{key}:{u["id"]}', u['id'], subject,
                            f'{times} Uhr. Wer zuerst bucht, wäscht.', url, key, entries[-1][2])


def queue_due_reminders(con):
    """Queue reminders for bookings that start within each user's chosen lead time."""
    now = slots.now()
    longest = max(m for m, _ in REMINDER_CHOICES)
    rows = con.execute("""
        SELECT r.id, r.day, r.slot, u.id AS user_id, u.display_name, u.email, u.email_verified,
               u.reminder_minutes, u.reminder_mail, u.reminder_push
        FROM reservations r JOIN users u ON u.id = r.user_id
        WHERE u.reminder_minutes > 0 AND (u.reminder_mail = 1 OR u.reminder_push = 1)
          AND r.day BETWEEN ? AND ?
    """, (now.date().isoformat(), (now + timedelta(minutes=longest)).date().isoformat())).fetchall()
    for r in rows:
        day = parse_day(r['day'])
        start = slot_bounds(day, r['slot'])[0]
        if not start - timedelta(minutes=r['reminder_minutes']) <= now < start:
            continue
        when = 'heute' if day == now.date() else ('morgen' if day == now.date() + timedelta(days=1) else fmt_day(day))
        url = link('/?week=' + day.isoformat())
        if _mail_to(r, 'reminder_mail'):
            body = (f'Hallo {r["display_name"]}\n\n'
                    f'Erinnerung: Du hast die Waschküche {when} gebucht,\n'
                    f'{fmt_day(day)}, {r["slot"]} ({slot_time(r["slot"])} Uhr).\n\n'
                    f'Kommt etwas dazwischen? Storniere den Slot, damit andere ihn nutzen können:\n'
                    f'{url}\n'
                    + _footer(r['user_id'], 'reminder', 'Erinnerungen'))
            _queue(con, f'reminder:{r["id"]}', r['email'],
                   f'Waschküche: {when} {r["slot"]} ({slot_time(r["slot"])})', body, expires_at=start)
        if _push_to(r, 'reminder_push'):
            _queue_push(con, f'reminder:{r["id"]}', r['user_id'], f'Waschküche: {when} {r["slot"]}',
                        f'{fmt_day(day)}, {slot_time(r["slot"])} Uhr. Kommt etwas dazwischen? '
                        f'Storniere den Slot, damit andere ihn nutzen können.',
                        url, f'reminder:{r["id"]}', start)


def queue_password_reset(con, user):
    """Queue a reset link, unless one was queued lately (anyone can ask, so this stops mail floods)."""
    now = slots.now()
    if con.execute('SELECT 1 FROM notifications WHERE dedupe_key LIKE ? AND created_at > ?',
                   (f'reset:{user["id"]}:%', (now - RESET_INTERVAL).isoformat(timespec='seconds'))).fetchone():
        return
    body = (f'Hallo {user["display_name"]}\n\n'
            f'Für dein Konto „{user["username"]}“ wurde ein neues Passwort angefordert.\n'
            f'Über diesen Link setzt du es (gültig für 1 Stunde):\n\n'
            f'{link("/reset-password/" + reset_token(user))}\n\n'
            f'Warst du das nicht? Dann ignoriere diese Mail, dein Passwort bleibt unverändert.\n')
    _queue(con, f'reset:{user["id"]}:{now.isoformat(timespec="seconds")}', user['email'],
           'Waschküche: Passwort zurücksetzen', body, expires_at=now + RESET_VALID)


def queue_email_verification(con, user):
    """Ask the user to confirm their address. Until then it gets no mails: with a typo, reminders
    and reset links (= the account) would go to a stranger."""
    if not mail.is_configured() or not user['email']:
        return
    now = slots.now()
    if con.execute('SELECT 1 FROM notifications WHERE dedupe_key LIKE ? AND to_addr = ? AND created_at > ?',
                   (f'verify:{user["id"]}:%', user['email'],
                    (now - RESET_INTERVAL).isoformat(timespec='seconds'))).fetchone():
        return
    body = (f'Hallo {user["display_name"]}\n\n'
            f'Bitte bestätige, dass diese Adresse zu deinem Konto „{user["username"]}“ gehört:\n\n'
            f'{link("/verify-email/" + verify_token(user["id"], user["email"]))}\n\n'
            f'Erst danach bekommst du Erinnerungen und kannst dein Passwort per Mail zurücksetzen.\n'
            f'Der Link gilt 7 Tage. Hast du dich nicht angemeldet? Dann ignoriere diese Mail.\n')
    _queue(con, f'verify:{user["id"]}:{now.isoformat(timespec="seconds")}:{user["email"]}', user['email'],
           'Waschküche: E-Mail-Adresse bestätigen', body, expires_at=now + VERIFY_VALID)


# --- Sending ---------------------------------------------------------------------------------

def _push_ttl(n):
    """Seconds the push service may hold the message for a device that is offline."""
    if n['expires_at'] is None:
        return PUSH_TTL.total_seconds()
    return (datetime.fromisoformat(n['expires_at']) - slots.now()).total_seconds()


def send_pending():
    """Send queued mails and push messages. Returns (sent, failed)."""
    con = get_db()
    now = slots.now().isoformat(timespec='seconds')
    rows = con.execute("""
        SELECT n.*, s.endpoint, s.p256dh, s.auth FROM notifications n
        LEFT JOIN push_subscriptions s ON s.id = n.push_subscription_id
        WHERE n.sent_at IS NULL AND n.attempts < ? AND (n.expires_at IS NULL OR n.expires_at > ?)
        ORDER BY n.id
    """, (MAX_ATTEMPTS, now)).fetchall()
    mail_on, push_on = mail.is_configured(), push.is_configured()
    sent = failed = 0
    gone = set()  # subscriptions deleted in this run; their other rows were fetched before
    for n in rows:
        is_push = n['push_subscription_id'] is not None
        if not (push_on if is_push else mail_on):  # switched off since it was queued: keep it for later
            continue
        if n['push_subscription_id'] in gone:
            continue
        try:
            if is_push:
                push.deliver(n, n['body'], _push_ttl(n))
            else:
                mail.deliver(n['to_addr'], n['subject'], n['body'])
        except push.Gone:
            # The browser dropped the subscription; forget the device (and its queued messages)
            failed += 1
            gone.add(n['push_subscription_id'])
            current_app.logger.info('Push %s to %s: subscription gone, removed', n['id'], n['to_addr'])
            with con:
                con.execute('DELETE FROM push_subscriptions WHERE id = ?', (n['push_subscription_id'],))
        except Exception as e:  # any SMTP/push/network error: keep it queued and retry next run
            failed += 1
            current_app.logger.warning('%s %s to %s failed: %s', 'Push' if is_push else 'Mail', n['id'],
                                       n['to_addr'], e)
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
    if not (mail.is_configured() or push.is_configured()):
        return 0, 0
    con = get_db()
    with con:
        queue_due_reminders(con)
        con.execute('DELETE FROM notifications WHERE created_at < ?',
                    ((slots.now() - KEEP_SENT).isoformat(timespec='seconds'),))
    return send_pending()
