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
from .slots import SLOT_NAMES, fmt_day, parse_day, slot_bounds, slot_time

REMINDER_CHOICES = [
    (0, 'Keine Erinnerung'),
    (30, '30 Minuten vorher'),
    (60, '1 Stunde vorher'),
    (120, '2 Stunden vorher'),
    (1440, '1 Tag vorher'),
]
FREED_WINDOW = timedelta(days=7)   # only announce freed slots that start within this window
FREED_HOLD = timedelta(minutes=2)  # freed slots of a day cancelled within this time go out as one message
FREED_MIN_BOOKED = timedelta(minutes=10)  # cancelling a booking younger than this announces nothing
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

def _queue(con, dedupe_key, to_addr, subject, body, expires_at=None, push_subscription_id=None,
           slot_names=None, send_after=None):
    con.execute(
        'INSERT OR IGNORE INTO notifications (dedupe_key, to_addr, subject, body, created_at, expires_at, '
        'push_subscription_id, slots, send_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (dedupe_key, to_addr, subject, body, slots.now().isoformat(timespec='seconds'),
         expires_at.isoformat(timespec='seconds') if expires_at else None, push_subscription_id,
         slot_names, send_after.isoformat(timespec='seconds') if send_after else None))


def _push_payload(title, body, url, tag):
    return json.dumps({'title': title, 'body': body, 'url': url, 'tag': tag}, ensure_ascii=False)


def _queue_push(con, dedupe_key, user_id, title, body, url, tag, expires_at):
    """One row per device of the user, so each is retried on its own. `to_addr` is only the push
    service's host (for the logs); the endpoint URL stays in push_subscriptions."""
    payload = _push_payload(title, body, url, tag)
    for s in con.execute('SELECT id, endpoint FROM push_subscriptions WHERE user_id = ?', (user_id,)).fetchall():
        _queue(con, f'{dedupe_key}:push:{s["id"]}', push.service_host(s['endpoint']), title, payload,
               expires_at, s['id'])


def _when(day, now):
    return 'heute' if day == now.date() else ('morgen' if day == now.date() + timedelta(days=1) else fmt_day(day))


def _first_upper(text):
    return text[:1].upper() + text[1:]  # not str.capitalize(), which turns "Okt." into "okt."


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

    `freed` is a list of (day, slot, booked_at); booked_at is None for bookings made before it was
    stored. Subscribers get one message per day and channel (mail, each push device), also when the
    slots are cancelled one after another: the message waits FREED_HOLD, and slots freed meanwhile join
    it (_queue_freed()). A booking cancelled within FREED_MIN_BOOKED announces nothing: the slot was
    free a moment ago, so nobody missed it (a wrong click, the wrong day).
    """
    if not (mail.is_configured() or push.is_configured()):
        return
    now = slots.now()
    by_day = {}
    for day, slot, booked_at in freed:
        if booked_at is not None and now - booked_at < FREED_MIN_BOOKED:
            continue
        start = slot_bounds(day, slot)[0]
        if now < start <= now + FREED_WINDOW:
            by_day.setdefault(day, []).append(slot)
    if not by_day:
        return
    users = con.execute("""
        SELECT id, display_name, email, email_verified, notify_freed, freed_push FROM users
        WHERE (notify_freed = 1 OR freed_push = 1) AND id != ?
    """, (cancelled_by,)).fetchall()
    for day, names in sorted(by_day.items()):
        for u in users:
            if _mail_to(u, 'notify_freed'):
                _queue_freed(con, u, day, names, 'mail', u['email'])
            if _push_to(u, 'freed_push'):
                for s in con.execute('SELECT id, endpoint FROM push_subscriptions WHERE user_id = ?',
                                     (u['id'],)).fetchall():
                    _queue_freed(con, u, day, names, f'push:{s["id"]}', push.service_host(s['endpoint']), s['id'])


def _freed_text(user, day, names, tag=None):
    """Subject and body of "slots are free again": a mail, or with `tag` a push payload.
    `names` are in SLOT_NAMES order."""
    verb = 'ist' if len(names) == 1 else 'sind'
    url = link('/?week=' + day.isoformat())
    if tag is None:
        lines = ''.join(f'- {slot} ({slot_time(slot)} Uhr)\n' for slot in names)
        subject = f'Waschküche: {fmt_day(day)} {" + ".join(names)} {verb} frei'
        body = (f'Hallo {user["display_name"]}\n\n'
                f'Am {fmt_day(day)} {verb} wieder frei:\n{lines}\n'
                f'Jetzt buchen: {url}\n\n'
                f'Wer zuerst bucht, wäscht.\n'
                + _footer(user['id'], 'freed', '„Slot frei geworden“'))
        return subject, body
    # Short title: the phone shows the app's name anyway and cuts long titles off
    subject = f'{_first_upper(_when(day, slots.now()))} {" + ".join(names)} {verb} frei'
    return subject, _push_payload(subject, f'{fmt_day(day)}, {_spans(day, names)} Uhr. Wer zuerst bucht, wäscht.',
                                  url, tag)


def _queue_freed(con, user, day, names, channel, to_addr, push_subscription_id=None):
    """Queue "slots are free again" for one recipient (mail or one push device) and day, or add the
    slots to the message for that day that is still waiting."""
    now = slots.now()
    prefix = f'freed:{day.isoformat()}:{user["id"]}:{channel}:'
    waiting = con.execute('SELECT id, slots FROM notifications WHERE dedupe_key LIKE ? AND sent_at IS NULL',
                          (prefix + '%',)).fetchone()
    if waiting:
        names = set(names) | set(waiting['slots'].split('+'))
    names = [n for n in SLOT_NAMES if n in names]
    tag = None if push_subscription_id is None else prefix + now.isoformat(timespec='seconds')
    subject, body = _freed_text(user, day, names, tag)
    expires_at = slot_bounds(day, names[-1])[0]  # worth sending until the last of them starts
    send_after = now + FREED_HOLD
    if waiting:
        con.execute('UPDATE notifications SET subject = ?, body = ?, slots = ?, expires_at = ?, send_after = ? '
                    'WHERE id = ?', (subject, body, '+'.join(names), expires_at.isoformat(timespec='seconds'),
                                     send_after.isoformat(timespec='seconds'), waiting['id']))
    else:
        _queue(con, prefix + now.isoformat(timespec='seconds'), to_addr, subject, body, expires_at,
               push_subscription_id, '+'.join(names), send_after)


def drop_rebooked_freed(con):
    """Due freed-slot messages: leave out the slots that are booked again (a cancellation by mistake
    booked back, or someone was quicker), and drop the message when none is left. Checked when the
    message is due, not on booking, so a slot booked and cancelled again during FREED_HOLD stays in it."""
    now = slots.now()
    rows = con.execute('SELECT id, dedupe_key, body, slots, push_subscription_id FROM notifications '
                       'WHERE slots IS NOT NULL AND sent_at IS NULL AND send_after <= ?',
                       (now.isoformat(timespec='seconds'),)).fetchall()
    for n in rows:
        _, day, user_id = n['dedupe_key'].split(':')[:3]  # 'freed:<day>:<user id>:...'
        booked = {r['slot'] for r in con.execute('SELECT slot FROM reservations WHERE day = ?', (day,))}
        names = n['slots'].split('+')
        free = [name for name in names if name not in booked]
        if free == names:
            continue
        user = con.execute('SELECT id, display_name FROM users WHERE id = ?', (int(user_id),)).fetchone()
        if not free or user is None:
            con.execute('DELETE FROM notifications WHERE id = ?', (n['id'],))
            continue
        day = parse_day(day)
        tag = None if n['push_subscription_id'] is None else json.loads(n['body'])['tag']
        subject, body = _freed_text(user, day, free, tag)
        con.execute('UPDATE notifications SET subject = ?, body = ?, slots = ?, expires_at = ? WHERE id = ?',
                    (subject, body, '+'.join(free), slot_bounds(day, free[-1])[0].isoformat(timespec='seconds'),
                     n['id']))


def queue_due_reminders(con):
    """Queue reminders for bookings that start within each user's chosen lead time.

    A user is reminded of their own bookings and, with `reminder_household`, of their housemates' too.
    Back-to-back slots of these on one day (e.g. a whole day) are one block: one reminder (per channel)
    before the block starts, none for the slots that only continue it.
    """
    now = slots.now()
    longest = max(m for m, _ in REMINDER_CHOICES)
    bookings = con.execute("""
        SELECT r.id, r.day, r.slot, r.user_id, u.display_name, u.household_id
        FROM reservations r JOIN users u ON u.id = r.user_id
        WHERE r.day BETWEEN ? AND ?
    """, (now.date().isoformat(), (now + timedelta(minutes=longest)).date().isoformat())).fetchall()
    if not bookings:
        return
    users = con.execute("""
        SELECT id, display_name, email, email_verified, household_id, reminder_minutes, reminder_mail,
               reminder_push, reminder_household
        FROM users WHERE reminder_minutes > 0 AND (reminder_mail = 1 OR reminder_push = 1)
    """).fetchall()
    for u in users:
        household = u['household_id'] if u['reminder_household'] else None
        # All of the user's bookings on these days are in here, so a block's neighbours are too
        ours = {(b['day'], b['slot']): b for b in bookings
                if b['user_id'] == u['id'] or (household is not None and b['household_id'] == household)}
        for (day, slot), b in ours.items():
            day = parse_day(day)
            start = slot_bounds(day, slot)[0]
            if (b['day'], _previous_slot(day, slot)) in ours:
                continue  # continues a block, whose first slot gets the reminder
            if not start - timedelta(minutes=u['reminder_minutes']) <= now < start:
                continue
            block = [b]
            while (key := (b['day'], _next_slot(day, block[-1]['slot']))) in ours:
                block.append(ours[key])
            _queue_reminder(con, u, day, block, start)


def _queue_reminder(con, user, day, block, start):
    """One reminder (mail and/or push) of a block of bookings to `user`; some may be housemates'."""
    # A reminder of one's own booking keeps the key it had before housemates' bookings were reminded of
    first = block[0]
    key = f'reminder:{first["id"]}' + ('' if first['user_id'] == user['id'] else f':{user["id"]}')
    names = ' + '.join(b['slot'] for b in block)
    hours = f'{slot_time(block[0]["slot"]).split(" – ")[0]} – {slot_time(block[-1]["slot"]).split(" – ")[1]}'
    when = _when(day, slots.now())
    url = link('/?week=' + day.isoformat())
    by = list(dict.fromkeys('dir' if b['user_id'] == user['id'] else b['display_name'] for b in block))
    by = None if by == ['dir'] else (by[0] if len(by) == 1 else ', '.join(by[:-1]) + ' und ' + by[-1])
    if _mail_to(user, 'reminder_mail'):
        booked = (f'Du hast die Waschküche {when} gebucht,\n{fmt_day(day)}, {names} ({hours} Uhr).' if by is None
                  else f'Die Waschküche ist {when} für euch gebucht,\n{fmt_day(day)}, {names} ({hours} Uhr),\n'
                       f'gebucht von {by}.')
        body = (f'Hallo {user["display_name"]}\n\n'
                f'Erinnerung: {booked}\n\n'
                f'Kommt etwas dazwischen? Storniere den Slot, damit andere ihn nutzen können:\n'
                f'{url}\n'
                + _footer(user['id'], 'reminder', 'Erinnerungen'))
        _queue(con, key, user['email'], f'Waschküche: {when} {names} ({hours})', body, expires_at=start)
    if _push_to(user, 'reminder_push'):
        _queue_push(con, key, user['id'], f'Erinnerung: {when} {names}',
                    f'{fmt_day(day)}, {hours} Uhr' + (f', gebucht von {by}' if by else '')
                    + '. Kommt etwas dazwischen? Storniere den Slot, damit andere ihn nutzen können.',
                    url, key, start)


def _previous_slot(day, slot_name):
    """The slot that ends when this one starts, or None."""
    start = slot_bounds(day, slot_name)[0]
    return next((n for n in SLOT_NAMES if slot_bounds(day, n)[1] == start), None)


def _next_slot(day, slot_name):
    """The slot that starts when this one ends, or None."""
    end = slot_bounds(day, slot_name)[1]
    return next((n for n in SLOT_NAMES if slot_bounds(day, n)[0] == end), None)


def _spans(day, names):
    """Times of slots in order, adjacent ones joined: '07:00 – 17:00' rather than '07:00 – 12:00, 12:00 – 17:00'."""
    runs = []
    for name in names:
        if runs and _next_slot(day, runs[-1][-1]) == name:
            runs[-1].append(name)
        else:
            runs.append([name])
    return ', '.join(f'{slot_time(r[0]).split(" – ")[0]} – {slot_time(r[-1]).split(" – ")[1]}' for r in runs)


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
          AND (n.send_after IS NULL OR n.send_after <= ?)
        ORDER BY n.id
    """, (MAX_ATTEMPTS, now, now)).fetchall()
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
    """One pass of the notifier: queue due reminders, drop rebooked slots from freed-slot messages, send
    everything pending, prune old rows."""
    if not (mail.is_configured() or push.is_configured()):
        return 0, 0
    con = get_db()
    with con:
        queue_due_reminders(con)
        drop_rebooked_freed(con)
        con.execute('DELETE FROM notifications WHERE created_at < ?',
                    ((slots.now() - KEEP_SENT).isoformat(timespec='seconds'),))
    return send_pending()
