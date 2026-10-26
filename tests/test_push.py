import base64
import json
import sqlite3

import pytest
import pywebpush
import requests
from conftest import MAIL_CONFIG, NOW, REAL_PUSH_DELIVER, VAPID_KEY
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from test_notifications import after_hold, at, booked_earlier, run, verify

from laundry import create_app, notifications, push, slots
from laundry.db import get_db

ENDPOINT = 'https://fcm.googleapis.com/fcm/send/device-1'


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def browser_keys():
    """p256dh and auth as a browser would send them."""
    key = ec.generate_private_key(ec.SECP256R1()).public_key()
    raw = key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64(raw), b64(b'0123456789abcdef')


def subscribe(client, token, endpoint=ENDPOINT):
    p256dh, auth = browser_keys()
    return client.post('/push/subscribe', data={'endpoint': endpoint, 'p256dh': p256dh, 'auth': auth,
                                                'csrf_token': token})


def save_prefs(client, token, minutes=0, reminder_mail=False, reminder_push=False, freed_mail=False,
               freed_push=False, email=''):
    data = {'action': 'notifications', 'email': email, 'reminder_minutes': str(minutes), 'csrf_token': token}
    for name, on in [('reminder_mail', reminder_mail), ('reminder_push', reminder_push),
                     ('notify_freed', freed_mail), ('freed_push', freed_push)]:
        if on:
            data[name] = '1'
    return client.post('/profile', data=data)


def audit_entries(query, *actions):
    marks = ','.join('?' * len(actions))
    return query(f'SELECT username, action, detail FROM audit_log WHERE action IN ({marks}) ORDER BY id', *actions)


# --- Subscribing devices -------------------------------------------------------------------------

def test_subscribe_and_unsubscribe(client, login, pushbox, query):
    t = login('bob')
    page = client.get('/profile').get_data(as_text=True)
    assert 'id="push-device"' in page and 'push.js' in page and 'per Push' in page
    assert f'data-key="{push_public_key(client)}"' in page

    assert subscribe(client, t).status_code == 204
    assert query('SELECT u.username, s.endpoint FROM push_subscriptions s JOIN users u ON u.id = s.user_id') == \
        [('bob', ENDPOINT)]
    assert f'data-endpoints=\'["{ENDPOINT}"]\'' in client.get('/profile').get_data(as_text=True)

    r = client.post('/push/unsubscribe', data={'endpoint': ENDPOINT, 'csrf_token': t})
    assert r.status_code == 204
    assert query('SELECT COUNT(*) FROM push_subscriptions') == [(0,)]
    client.post('/push/unsubscribe', data={'endpoint': ENDPOINT, 'csrf_token': t})  # already gone: no entry
    assert audit_entries(query, 'push_enabled', 'push_disabled') == [
        ('bob', 'push_enabled', 'fcm.googleapis.com'),
        ('bob', 'push_disabled', 'fcm.googleapis.com'),
    ]


def push_public_key(client):
    with client.application.app_context():
        return push.public_key()


@pytest.mark.parametrize('endpoint', [
    '',
    'http://fcm.googleapis.com/fcm/send/x',          # not https
    'https://evil.example/fcm/send/x',               # not a push service
    'https://fcm.googleapis.com.evil.example/x',     # only looks like one
    'https://fcm.googleapis.com:8443/x',             # other port
    'https://user@fcm.googleapis.com/x',             # credentials
    'https://127.0.0.1/x',
    'https://[::1/x',                                # not parseable
    'https://fcm.googleapis.com/' + 'x' * 1024,      # too long
])
def test_subscribe_rejects_other_endpoints(client, login, pushbox, query, endpoint):
    assert subscribe(client, login('bob'), endpoint).status_code == 400
    assert query('SELECT COUNT(*) FROM push_subscriptions') == [(0,)]


def test_subscribe_rejects_bad_keys_csrf_and_anonymous(client, login, csrf, pushbox, query):
    p256dh, auth = browser_keys()
    t = login('bob')
    for fields in [{'p256dh': p256dh, 'auth': b64(b'short')},
                   {'p256dh': b64(b'\x04' + b'x' * 10), 'auth': auth},
                   {'p256dh': 'not base64!', 'auth': auth},
                   {'p256dh': p256dh}]:
        r = client.post('/push/subscribe', data={'endpoint': ENDPOINT, 'csrf_token': t, **fields})
        assert r.status_code == 400
    assert client.post('/push/subscribe', data={'endpoint': ENDPOINT, 'p256dh': p256dh,
                                                'auth': auth}).status_code == 400  # no CSRF token
    assert client.post('/push/unsubscribe', data={'endpoint': '', 'csrf_token': t}).status_code == 400

    client.post('/logout', data={'csrf_token': t})
    r = client.post('/push/subscribe', data={'endpoint': ENDPOINT, 'p256dh': p256dh, 'auth': auth,
                                             'csrf_token': csrf()})
    assert r.status_code == 302 and '/login' in r.location
    assert query('SELECT COUNT(*) FROM push_subscriptions') == [(0,)]


@pytest.mark.parametrize('endpoint', [
    'https://fcm.googleapis.com/fcm/send/abc',
    'https://updates.push.services.mozilla.com/wpush/v2/abc',
    'https://web.push.apple.com/abc',
    'https://wns2-par02p.notify.windows.com/w/?token=abc',
])
def test_known_push_services(endpoint):
    assert push.valid_subscription(endpoint, *browser_keys())


def test_without_push_config(client, login, query):
    t = login('bob')
    page = client.get('/profile').get_data(as_text=True)
    assert 'push-device' not in page and 'push.js' not in page and 'per Push' not in page
    assert subscribe(client, t).status_code == 404

    # Push choices made earlier survive saving the form, which has no push boxes now
    with client.application.app_context(), get_db() as con:
        con.execute("UPDATE users SET reminder_push = 1, freed_push = 1 WHERE username = 'bob'")
    save_prefs(client, t, minutes=60, reminder_mail=True)
    assert query("SELECT reminder_minutes, reminder_mail, reminder_push, notify_freed, freed_push FROM users "
                 "WHERE username = 'bob'") == [(60, 1, 1, 0, 1)]


def test_a_device_belongs_to_whoever_subscribed_it_last(app, client, login, pushbox, monkeypatch, query):
    t = login('bob')
    save_prefs(client, t, minutes=60, reminder_push=True)
    subscribe(client, t)
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Abend', 'csrf_token': t})
    at(monkeypatch, 2026, 10, 15, 16, 5)
    with app.app_context(), get_db() as con:
        notifications.queue_due_reminders(con)
    assert query('SELECT COUNT(*) FROM notifications') == [(1,)]

    subscribe(client, login('alice'))  # same browser, other account
    assert query('SELECT u.username FROM push_subscriptions s JOIN users u ON u.id = s.user_id') == [('alice',)]
    assert query('SELECT COUNT(*) FROM notifications') == [(0,)]  # bob's reminder no longer goes there


def test_at_most_ten_devices_per_user(client, login, pushbox, query):
    t = login('bob')
    for i in range(12):
        subscribe(client, t, f'{ENDPOINT}-{i}')
    assert [e for e, in query('SELECT endpoint FROM push_subscriptions ORDER BY id')] == \
        [f'{ENDPOINT}-{i}' for i in range(2, 12)]


# --- Settings ----------------------------------------------------------------------------------

def test_channels_are_saved_and_logged(client, login, pushbox, query):
    t = login('bob')
    save_prefs(client, t, minutes=30, reminder_mail=True, reminder_push=True, freed_push=True)
    assert query("SELECT reminder_minutes, reminder_mail, reminder_push, notify_freed, freed_push FROM users "
                 "WHERE username = 'bob'") == [(30, 1, 1, 0, 1)]
    save_prefs(client, t)
    assert query("SELECT reminder_minutes, reminder_mail, reminder_push, notify_freed, freed_push FROM users "
                 "WHERE username = 'bob'") == [(0, 0, 0, 0, 0)]
    assert [d for _, _, d in audit_entries(query, 'notifications')] == [
        'Erinnerung: 30 Minuten vorher (Mail + Push), freie Slots: Push',
        'Erinnerung: Keine Erinnerung, freie Slots: aus',
    ]


# --- Sending -----------------------------------------------------------------------------------

def test_reminder_by_push_only(app, client, login, mailbox, pushbox, monkeypatch):
    t = login('bob')
    save_prefs(client, t, minutes=60, reminder_push=True, email='bob@example.ch')
    verify(app, 'bob')
    subscribe(client, t)
    subscribe(client, t, ENDPOINT + '-tablet')
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Abend', 'csrf_token': t})  # starts 17:00

    run(app)
    assert pushbox == []
    at(monkeypatch, 2026, 10, 15, 16, 5)
    assert run(app) == (2, 0)
    assert mailbox == []
    assert sorted(e for e, _, _ in pushbox) == [ENDPOINT, ENDPOINT + '-tablet']
    endpoint, payload, ttl = pushbox[0]
    assert payload['title'] == 'Erinnerung: heute Abend'
    assert payload['body'].startswith('Do 15. Okt., 17:00 – 22:00 Uhr.')
    assert payload['url'] == 'https://wk.test/?week=2026-10-15'
    assert ttl == 55 * 60  # until the slot starts

    at(monkeypatch, 2026, 10, 15, 16, 30)
    run(app)
    assert len(pushbox) == 2  # not twice


def test_back_to_back_slots_get_one_push(app, client, login, mailbox, pushbox, monkeypatch):
    t = login('bob')
    save_prefs(client, t, minutes=60, reminder_push=True)
    subscribe(client, t)
    client.post('/book-day', data={'day': '2026-10-16', 'csrf_token': t})
    for hour in (6, 11, 16):              # within the hour before each slot
        at(monkeypatch, 2026, 10, 16, hour, 30)
        run(app)
    assert [p['title'] for _, p, _ in pushbox] == ['Erinnerung: heute Vormittag + Nachmittag + Abend']
    assert pushbox[0][1]['body'].startswith('Fr 16. Okt., 07:00 – 22:00 Uhr.')


def test_reminder_by_mail_and_push(app, client, login, mailbox, pushbox, monkeypatch):
    t = login('bob')
    save_prefs(client, t, minutes=60, reminder_mail=True, reminder_push=True, email='bob@example.ch')
    verify(app, 'bob')
    subscribe(client, t)
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Abend', 'csrf_token': t})
    at(monkeypatch, 2026, 10, 15, 16, 5)
    assert run(app) == (2, 0)
    assert [m[1] for m in mailbox] == ['Waschküche: heute Abend (17:00 – 22:00)']
    assert [p['title'] for _, p, _ in pushbox] == ['Erinnerung: heute Abend']


def test_push_needs_no_email(app, client, login, mailbox, pushbox, monkeypatch):
    """Push works without an address and even without mail settings."""
    app.config['SMTP_HOST'] = None
    t = login('bob')
    save_prefs(client, t, minutes=60, reminder_mail=True, reminder_push=True)
    subscribe(client, t)
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Abend', 'csrf_token': t})
    at(monkeypatch, 2026, 10, 15, 16, 5)
    assert run(app) == (1, 0)
    assert mailbox == [] and len(pushbox) == 1


def test_freed_slot_by_push(app, client, login, mailbox, pushbox, monkeypatch, query):
    ta = login('alice')
    save_prefs(client, ta, freed_push=True)
    subscribe(client, ta)
    t = login('bob')
    save_prefs(client, t, freed_push=True)
    subscribe(client, t, ENDPOINT + '-bob')
    with booked_earlier(monkeypatch):
        client.post('/book-day', data={'day': '2026-10-16', 'csrf_token': t})
    client.post('/cancel', data={'id': [rid for rid, in query('SELECT id FROM reservations')], 'csrf_token': t})

    after_hold(monkeypatch)
    run(app)
    assert mailbox == []
    assert [(e, p['title']) for e, p, _ in pushbox] == \
        [(ENDPOINT, 'Morgen Vormittag + Nachmittag + Abend sind frei')]  # not to bob
    assert pushbox[0][1]['body'] == 'Fr 16. Okt., 07:00 – 22:00 Uhr. Wer zuerst bucht, wäscht.'
    assert pushbox[0][1]['url'] == 'https://wk.test/?week=2026-10-16'


def test_slots_cancelled_one_by_one_give_one_message(app, client, login, mailbox, pushbox, monkeypatch, query):
    ta = login('alice')
    save_prefs(client, ta, freed_mail=True, freed_push=True, email='alice@example.ch')
    verify(app, 'alice')
    subscribe(client, ta)
    t = login('bob')
    with booked_earlier(monkeypatch):
        client.post('/book-day', data={'day': '2026-10-20', 'csrf_token': t})
    ids = [rid for rid, in query('SELECT id FROM reservations ORDER BY id')]
    for minute, rid in zip((20, 21, 22), (ids[2], ids[0], ids[1]), strict=True):  # each within the hold
        at(monkeypatch, 2026, 10, 15, 14, minute)
        client.post(f'/cancel/{rid}', data={'csrf_token': t})
        assert run(app) == (0, 0)  # still waiting for more
    at(monkeypatch, 2026, 10, 15, 14, 24)
    assert run(app) == (2, 0)
    assert [p['title'] for _, p, _ in pushbox] == ['Di 20. Okt. Vormittag + Nachmittag + Abend sind frei']
    assert pushbox[0][1]['body'] == 'Di 20. Okt., 07:00 – 22:00 Uhr. Wer zuerst bucht, wäscht.'
    assert [m[1] for m in mailbox] == ['Waschküche: Di 20. Okt. Vormittag + Nachmittag + Abend sind frei']
    assert '- Vormittag (07:00 – 12:00 Uhr)\n- Nachmittag' in mailbox[0][2]

    # A slot freed after the message went out gets its own message
    client.post('/book', data={'day': '2026-10-20', 'slot': 'Abend', 'csrf_token': t})
    at(monkeypatch, 2026, 10, 15, 14, 34)
    client.post(f'/cancel/{query("SELECT MAX(id) FROM reservations")[0][0]}', data={'csrf_token': t})
    at(monkeypatch, 2026, 10, 15, 14, 37)
    assert run(app) == (2, 0)
    assert pushbox[-1][1]['title'] == 'Di 20. Okt. Abend ist frei'
    assert pushbox[-1][1]['tag'] != pushbox[0][1]['tag']  # doesn't replace the first one on the phone


def test_slot_booked_again_is_left_out_of_the_push(app, client, login, pushbox, monkeypatch, query):
    ta = login('alice')
    save_prefs(client, ta, freed_push=True)
    subscribe(client, ta)
    t = login('bob')
    with booked_earlier(monkeypatch):
        client.post('/book-day', data={'day': '2026-10-16', 'csrf_token': t})
    client.post('/cancel', data={'id': [rid for rid, in query('SELECT id FROM reservations')], 'csrf_token': t})
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    after_hold(monkeypatch)
    assert run(app) == (1, 0)
    (_, payload, _), = pushbox
    assert payload['title'] == 'Morgen Vormittag + Nachmittag sind frei'
    assert payload['body'] == 'Fr 16. Okt., 07:00 – 17:00 Uhr. Wer zuerst bucht, wäscht.'
    assert payload['tag'] == query('SELECT dedupe_key FROM notifications')[0][0]  # kept


def test_freed_title_is_capitalized_only_at_the_start(app, client, login, pushbox, monkeypatch, query):
    ta = login('alice')
    save_prefs(client, ta, freed_push=True)
    subscribe(client, ta)
    t = login('bob')
    with booked_earlier(monkeypatch):
        for day in ('2026-10-15', '2026-10-16', '2026-10-21'):
            client.post('/book', data={'day': day, 'slot': 'Abend', 'csrf_token': t})
    for rid, in query('SELECT id FROM reservations'):
        client.post(f'/cancel/{rid}', data={'csrf_token': t})
    after_hold(monkeypatch)
    run(app)
    assert sorted(p['title'] for _, p, _ in pushbox) == \
        ['Heute Abend ist frei', 'Mi 21. Okt. Abend ist frei', 'Morgen Abend ist frei']


def test_gone_subscription_is_removed(app, client, login, pushbox, monkeypatch, query):
    t = login('bob')
    save_prefs(client, t, freed_push=True)
    subscribe(client, t)
    t = login('alice')
    subscribe(client, t, ENDPOINT + '-alice')
    with booked_earlier(monkeypatch):
        client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
        client.post('/book', data={'day': '2026-10-17', 'slot': 'Abend', 'csrf_token': t})
    client.post(f'/cancel/{query("SELECT MIN(id) FROM reservations")[0][0]}', data={'csrf_token': t})  # bob gets it
    client.post(f'/cancel/{query("SELECT MAX(id) FROM reservations")[0][0]}', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM notifications') == [(2,)]  # two days, same device

    calls = []

    def gone(sub, payload, ttl):
        calls.append(sub['endpoint'])
        raise push.Gone('push service answered 410')
    monkeypatch.setattr(push, 'deliver', gone)
    after_hold(monkeypatch)
    assert run(app) == (0, 1)
    assert calls == [ENDPOINT]  # not tried again for the second message
    assert query('SELECT endpoint FROM push_subscriptions') == [(ENDPOINT + '-alice',)]  # alice's stays
    assert query('SELECT COUNT(*) FROM notifications') == [(0,)]


def test_failed_push_is_retried(app, client, login, pushbox, monkeypatch, query):
    ta = login('alice')
    save_prefs(client, ta, freed_push=True)
    subscribe(client, ta)
    tb = login('bob')
    with booked_earlier(monkeypatch):
        client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': tb})
    client.post(f'/cancel/{query("SELECT id FROM reservations")[0][0]}', data={'csrf_token': tb})

    def broken(sub, payload, ttl):
        raise push.PushError('push service answered 500')
    monkeypatch.setattr(push, 'deliver', broken)
    after_hold(monkeypatch)
    assert run(app) == (0, 1)
    assert query('SELECT to_addr, attempts, last_error, sent_at FROM notifications') == \
        [('fcm.googleapis.com', 1, 'push service answered 500', None)]

    monkeypatch.setattr(push, 'deliver', lambda sub, payload, ttl: pushbox.append(sub['endpoint']))
    assert run(app) == (1, 0)


def test_push_waits_while_switched_off(app, client, login, pushbox, monkeypatch, query):
    t = login('alice')
    save_prefs(client, t, freed_push=True)
    subscribe(client, t)
    tb = login('bob')
    with booked_earlier(monkeypatch):
        client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': tb})
    client.post(f'/cancel/{query("SELECT id FROM reservations")[0][0]}', data={'csrf_token': tb})
    app.config['VAPID_PRIVATE_KEY'] = None
    assert run(app) == (0, 0)
    assert query('SELECT attempts, sent_at FROM notifications') == [(0, None)]


def test_mail_unsubscribe_link_keeps_push(app, client, login, mailbox, pushbox, monkeypatch, query):
    t = login('bob')
    save_prefs(client, t, minutes=60, reminder_mail=True, reminder_push=True, email='bob@example.ch')
    verify(app, 'bob')
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Abend', 'csrf_token': t})
    at(monkeypatch, 2026, 10, 15, 16, 5)
    run(app)
    path = next(w for w in mailbox[0][2].split() if '/unsubscribe/' in w)[len('https://wk.test'):]
    other = app.test_client()
    with other.session_transaction() as s:
        s['csrf'] = 'x'
    assert 'Erinnerungen per Mail' in other.post(path, data={'csrf_token': 'x'}).get_data(as_text=True)
    assert query("SELECT reminder_minutes, reminder_mail, reminder_push FROM users WHERE username = 'bob'") == \
        [(60, 0, 1)]


# --- push.deliver and the key ------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.text = 'details'


@pytest.mark.parametrize('error, expected', [
    (pywebpush.WebPushException('x', response=FakeResponse(410)), 'push service answered 410'),
    (pywebpush.WebPushException('x', response=FakeResponse(404)), 'push service answered 404'),
    (pywebpush.WebPushException('x', response=FakeResponse(500)), 'push service answered 500'),
    (requests.ConnectionError(f'cannot reach {ENDPOINT}'), 'ConnectionError'),
])
def test_deliver_errors(app, pushbox, monkeypatch, error, expected):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(push, 'webpush', fail)
    gone = expected.endswith(('404', '410'))
    with app.app_context(), pytest.raises(push.Gone if gone else push.PushError) as info:
        REAL_PUSH_DELIVER({'endpoint': ENDPOINT, 'p256dh': 'p', 'auth': 'a'}, '{}', 60)
    assert str(info.value) == expected  # never the endpoint, it is the device's secret


def test_deliver_encrypts_and_signs(app, pushbox, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # curl mode writes the encrypted body to ./encrypted.data
    calls = []
    real_webpush = pywebpush.webpush
    monkeypatch.setattr(push, 'webpush', lambda *a, **kw: calls.append(real_webpush(*a, curl=True, **kw)))
    p256dh, auth = browser_keys()
    with app.app_context():
        REAL_PUSH_DELIVER({'endpoint': ENDPOINT, 'p256dh': p256dh, 'auth': auth}, '{"title": "Hallo"}', 3300.4)
    request = calls[0]
    assert request.startswith(f'curl -vX POST {ENDPOINT}')
    assert '"ttl: 3300"' in request.lower() and 'aes128gcm' in request
    assert 'vapid t=' in request.lower()  # signed with our key


def test_vapid_subject(app, pushbox):
    with app.app_context():
        assert push.subject() == 'mailto:waschkueche@example.ch'
        app.config['MAIL_FROM'] = None
        assert push.subject() == 'https://wk.test'
        app.config['VAPID_SUBJECT'] = 'mailto:verwaltung@example.ch'
        assert push.subject() == 'mailto:verwaltung@example.ch'


def test_invalid_key_stops_the_start(tmp_path):
    with pytest.raises(RuntimeError, match='VAPID_PRIVATE_KEY is not a valid key'):
        create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'x.db'), 'VAPID_PRIVATE_KEY': 'nonsense'})


def test_gen_vapid_key(tmp_path, monkeypatch):
    monkeypatch.setattr(slots, 'now', lambda: NOW)
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'x.db')})
    out = app.test_cli_runner().invoke(args=['gen-vapid-key']).output.strip()
    assert out.startswith('VAPID_PRIVATE_KEY=')
    key = out.removeprefix('VAPID_PRIVATE_KEY=')
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'x.db'), 'VAPID_PRIVATE_KEY': key, **MAIL_CONFIG})
    with app.app_context():
        assert len(base64.urlsafe_b64decode(push.public_key() + '=')) == 65


def test_service_worker_shows_push_messages(client):
    js = client.get('/sw.js').get_data(as_text=True)
    assert "addEventListener('push'" in js and "addEventListener('notificationclick'" in js
    assert '"/static/icons/icon-192.png"' in js


def test_push_columns_are_added_to_an_existing_database(tmp_path):
    path = tmp_path / 'old.db'
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                   password TEXT NOT NULL, display_name TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
                   email TEXT, reminder_minutes INTEGER NOT NULL DEFAULT 0, notify_freed INTEGER NOT NULL DEFAULT 0)""")
    con.execute("INSERT INTO users (username, password, display_name, reminder_minutes) VALUES ('bob', 'x', 'Bob', 60)")
    con.execute("""CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT UNIQUE NOT NULL,
                   to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
                   expires_at TEXT, sent_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)""")
    con.execute("INSERT INTO notifications (dedupe_key, to_addr, subject, body, created_at) "
                "VALUES ('reset:1:x', 'b@x.ch', 'S', 'B', '2026-10-15T14:00:00')")
    con.commit()
    con.close()

    create_app({'TESTING': True, 'DATABASE': str(path)})
    create_app({'TESTING': True, 'DATABASE': str(path)})  # idempotent
    con = sqlite3.connect(path)
    # Mail reminders chosen before stay on
    assert con.execute('SELECT reminder_minutes, reminder_mail, reminder_push, freed_push FROM users').fetchall() == \
        [(60, 1, 0, 0)]
    assert con.execute('SELECT dedupe_key, push_subscription_id, slots, send_after FROM notifications').fetchall() == \
        [('reset:1:x', None, None, None)]
    assert con.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone() == (0,)


def test_deleting_a_user_removes_their_devices(client, login, pushbox, query):
    subscribe(client, login('bob'))
    t = login('alice')
    bob_id = query("SELECT id FROM users WHERE username = 'bob'")[0][0]
    client.post(f'/admin/users/{bob_id}/delete', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM push_subscriptions') == [(0,)]


def test_payload_is_json(app, client, login, pushbox, monkeypatch, query):
    """The service worker reads the body as JSON; umlauts stay readable."""
    ta = login('alice')
    save_prefs(client, ta, freed_push=True)
    subscribe(client, ta)
    t = login('bob')
    with booked_earlier(monkeypatch):
        client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    client.post(f'/cancel/{query("SELECT id FROM reservations")[0][0]}', data={'csrf_token': t})
    body, = query('SELECT body FROM notifications')[0]
    assert json.loads(body)['title'] == 'Morgen Abend ist frei'
    assert 'Fr 16. Okt., 17:00 – 22:00 Uhr. Wer zuerst bucht, wäscht.' in body  # umlauts stay readable


def test_help_and_privacy_mention_push_only_when_configured(app, client, login):
    login('bob')
    assert 'Push' not in client.get('/help').get_data(as_text=True)
    assert 'Push' not in client.get('/privacy').get_data(as_text=True)
    app.config['VAPID_PRIVATE_KEY'] = VAPID_KEY
    html = client.get('/help').get_data(as_text=True)
    assert '<a href="#benachrichtigungen">Benachrichtigungen</a>' in html
    assert 'per Mail, per Push-Mitteilung, beides oder gar nicht' in html
    assert 'Tippe im Profil auf <strong>Push aktivieren</strong>' in html
    assert '/static/help/notifications.png' in html
    page = client.get('/privacy').get_data(as_text=True)
    assert 'Push-Dienst deines Browsers' in page and 'Push-Geräte' in page
