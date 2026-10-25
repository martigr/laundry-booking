import smtplib
import sqlite3
from datetime import datetime

import pytest

from laundry import create_app, mail, notifications, slots
from laundry.db import get_db

REAL_DELIVER = mail.deliver  # captured before the mailbox fixture replaces it
NOW = datetime(2026, 10, 15, 14, 20)


def set_prefs(client, login, username, email, minutes=0, freed=False, verified=True):
    t = login(username)
    data = {'action': 'notifications', 'email': email, 'reminder_minutes': str(minutes), 'reminder_mail': '1',
            'csrf_token': t}
    if freed:
        data['notify_freed'] = '1'
    client.post('/profile', data=data)
    if verified:
        verify(client.application, username)
    return t


def verify(app, username):
    """Mark the address as confirmed and drop the verification mail, as if the link was used."""
    with app.app_context(), get_db() as con:
        con.execute('UPDATE users SET email_verified = 1 WHERE username = ?', (username,))
        con.execute("DELETE FROM notifications WHERE dedupe_key LIKE 'verify:%'")


def run(app):
    with app.app_context():
        return notifications.run_once()


def at(monkeypatch, *args):
    monkeypatch.setattr(slots, 'now', lambda: datetime(*args))


def link_in(body, prefix):
    return next(word for word in body.split() if word.startswith('https://wk.test' + prefix))[len('https://wk.test'):]


def test_profile_settings(client, login, query):
    set_prefs(client, login, 'bob', ' bob@example.ch ', 60, freed=True)
    assert query("SELECT email, reminder_minutes, notify_freed FROM users WHERE username = 'bob'") == \
        [('bob@example.ch', 60, 1)]

    t = set_prefs(client, login, 'bob', 'kaputt', 30)
    assert query("SELECT email, reminder_minutes FROM users WHERE username = 'bob'") == [('bob@example.ch', 60)]
    r = client.post('/profile', data={'action': 'notifications', 'email': '', 'reminder_minutes': '7',
                                      'csrf_token': t})
    assert r.status_code == 400


def test_reminder_is_sent_once_at_the_right_time(app, client, login, mailbox, monkeypatch):
    t = set_prefs(client, login, 'bob', 'bob@example.ch', 60)
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Abend', 'csrf_token': t})  # starts 17:00

    run(app)                              # 14:20: too early
    assert mailbox == []

    at(monkeypatch, 2026, 10, 15, 16, 5)  # within the hour before
    assert run(app) == (1, 0)
    to, subject, body = mailbox[0]
    assert to == 'bob@example.ch'
    assert subject == 'Waschküche: heute Abend (17:00 – 22:00)'
    assert 'https://wk.test/unsubscribe/' in body

    at(monkeypatch, 2026, 10, 15, 16, 30)
    run(app)                              # no second mail
    assert len(mailbox) == 1

    at(monkeypatch, 2026, 10, 15, 17, 30)
    run(app)
    assert len(mailbox) == 1


def test_reminder_needs_email_and_choice(app, client, login, mailbox, monkeypatch):
    t = set_prefs(client, login, 'bob', '', 60)
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Abend', 'csrf_token': t})
    at(monkeypatch, 2026, 10, 15, 16, 5)
    run(app)
    assert mailbox == []


def test_day_before_reminder(app, client, login, mailbox, monkeypatch):
    t = set_prefs(client, login, 'bob', 'bob@example.ch', 1440)
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Vormittag', 'csrf_token': t})
    at(monkeypatch, 2026, 10, 15, 7, 30)
    run(app)
    at(monkeypatch, 2026, 10, 15, 8, 0)
    run(app)
    assert [m[1] for m in mailbox] == ['Waschküche: morgen Vormittag (07:00 – 12:00)']


def test_back_to_back_slots_get_one_reminder(app, client, login, mailbox, monkeypatch):
    t = set_prefs(client, login, 'bob', 'bob@example.ch', 60)
    client.post('/book-day', data={'day': '2026-10-16', 'csrf_token': t})
    for hour in (6, 11, 16):              # within the hour before each slot
        at(monkeypatch, 2026, 10, 16, hour, 30)
        run(app)
    assert len(mailbox) == 1
    to, subject, body = mailbox[0]
    assert subject == 'Waschküche: heute Vormittag + Nachmittag + Abend (07:00 – 22:00)'
    assert 'Vormittag + Nachmittag + Abend (07:00 – 22:00 Uhr)' in body


def test_separate_blocks_get_separate_reminders(app, client, login, mailbox, monkeypatch):
    a = login('alice')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Vormittag', 'csrf_token': a})
    t = set_prefs(client, login, 'bob', 'bob@example.ch', 60)
    for slot in ('Nachmittag', 'Abend'):
        client.post('/book', data={'day': '2026-10-16', 'slot': slot, 'csrf_token': t})
    client.post('/book', data={'day': '2026-10-17', 'slot': 'Vormittag', 'csrf_token': t})
    for args in ((16, 11, 30), (16, 16, 30), (17, 6, 30)):
        at(monkeypatch, 2026, 10, *args)
        run(app)
    assert [m[1] for m in mailbox] == ['Waschküche: heute Nachmittag + Abend (12:00 – 22:00)',
                                       'Waschküche: heute Vormittag (07:00 – 12:00)']


def test_freed_slot_notifies_subscribers(app, client, login, mailbox, query):
    set_prefs(client, login, 'alice', 'alice@example.ch', freed=True)
    t = set_prefs(client, login, 'bob', 'bob@example.ch', freed=True)
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    client.post('/book', data={'day': '2026-11-05', 'slot': 'Abend', 'csrf_token': t})  # beyond 7 days
    for rid, in query('SELECT id FROM reservations'):
        client.post(f'/cancel/{rid}', data={'csrf_token': t})

    run(app)
    assert [(m[0], m[1]) for m in mailbox] == [('alice@example.ch', 'Waschküche: Fr 16. Okt. Abend ist frei')]
    assert 'https://wk.test/?week=2026-10-16' in mailbox[0][2]


def test_cancelling_a_whole_day_sends_one_mail(app, client, login, mailbox, query):
    set_prefs(client, login, 'alice', 'alice@example.ch', freed=True)
    t = login('bob')
    client.post('/book-day', data={'day': '2026-10-16', 'csrf_token': t})
    ids = [rid for rid, in query('SELECT id FROM reservations')]
    assert len(ids) == 3
    client.post('/cancel', data={'id': ids, 'csrf_token': t})

    run(app)
    assert len(mailbox) == 1
    to, subject, body = mailbox[0]
    assert subject == 'Waschküche: Fr 16. Okt. Vormittag + Nachmittag + Abend sind frei'
    assert '- Vormittag (07:00 – 12:00 Uhr)' in body and '- Abend (17:00 – 22:00 Uhr)' in body


def test_unsubscribe_link(app, client, login, mailbox, query):
    set_prefs(client, login, 'alice', 'alice@example.ch', 60, freed=True)
    t = login('bob')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    client.post(f'/cancel/{query("SELECT id FROM reservations")[0][0]}', data={'csrf_token': t})
    run(app)
    path = link_in(mailbox[0][2], '/unsubscribe/')

    other = app.test_client()  # works without being logged in
    assert 'Ja, abbestellen' in other.get(path).get_data(as_text=True)
    assert query("SELECT notify_freed FROM users WHERE username = 'alice'") == [(1,)]  # GET changes nothing
    with other.session_transaction() as s:
        s['csrf'] = 'x'
    assert 'Abbestellt' in other.post(path, data={'csrf_token': 'x'}).get_data(as_text=True)
    assert query("SELECT notify_freed, reminder_minutes FROM users WHERE username = 'alice'") == [(0, 60)]
    assert other.get('/unsubscribe/forged').status_code == 404


def test_password_reset(app, client, csrf, login, mailbox, monkeypatch):
    set_prefs(client, login, 'bob', 'Bob@Example.ch')
    client.post('/logout', data={'csrf_token': csrf()})

    for identifier in ['bob@example.ch', 'nobody']:
        r = client.post('/forgot-password', data={'identifier': identifier, 'csrf_token': csrf()},
                        follow_redirects=True)
        assert 'Falls zu diesem Konto' in r.get_data(as_text=True)
    run(app)
    assert [m[0] for m in mailbox] == ['Bob@Example.ch']
    path = link_in(mailbox[0][2], '/reset-password/')

    assert client.get(path).status_code == 200
    client.post(path, data={'password': 'brandnew12', 'csrf_token': csrf()})
    login('bob', 'brandnew12')
    assert client.get('/').status_code == 200
    assert client.get(path).status_code == 400  # single use


def test_password_reset_link_expires(app, client, csrf, login, mailbox, monkeypatch):
    set_prefs(client, login, 'bob', 'bob@example.ch')
    client.post('/forgot-password', data={'identifier': 'bob', 'csrf_token': csrf()})
    run(app)
    path = link_in(mailbox[0][2], '/reset-password/')

    import itsdangerous.timed
    real = itsdangerous.timed.TimestampSigner.get_timestamp
    monkeypatch.setattr(itsdangerous.timed.TimestampSigner, 'get_timestamp', lambda self: real(self) + 2 * 3600)
    assert client.get(path).status_code == 400


def test_failed_mail_is_retried(app, client, csrf, login, mailbox, monkeypatch, query):
    set_prefs(client, login, 'bob', 'bob@example.ch')
    client.post('/forgot-password', data={'identifier': 'bob', 'csrf_token': csrf()})

    def broken(*args):
        raise OSError('connection refused')
    monkeypatch.setattr(mail, 'deliver', broken)
    assert run(app) == (0, 1)
    assert query('SELECT attempts, last_error, sent_at FROM notifications') == [(1, 'connection refused', None)]

    monkeypatch.setattr(mail, 'deliver', lambda *a: mailbox.append(a))
    assert run(app) == (1, 0)


def test_without_mail_config_nothing_is_queued(tmp_path, monkeypatch):
    monkeypatch.setattr(slots, 'now', lambda: NOW)
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(tmp_path / 'x.db')})
    client = app.test_client()
    assert client.get('/forgot-password').status_code == 404
    assert 'Passwort vergessen' not in client.get('/login').get_data(as_text=True)
    with app.app_context():
        assert notifications.run_once() == (0, 0)
    assert app.test_cli_runner().invoke(args=['send-notifications']).exit_code == 0


def test_admin_creates_user_with_email(client, login, query):
    t = login('alice')
    client.post('/admin/users', data={'username': 'carl', 'password': 'secret1234', 'email': 'carl@example.ch',
                                      'csrf_token': t})
    client.post('/admin/users', data={'username': 'dora', 'password': 'secret1234', 'email': 'no-at-sign',
                                      'csrf_token': t})
    assert query("SELECT username, email FROM users WHERE username IN ('carl', 'dora')") == \
        [('carl', 'carl@example.ch')]


def test_columns_are_added_to_an_existing_database(tmp_path):
    path = tmp_path / 'old.db'
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                   password TEXT NOT NULL, display_name TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0)""")
    con.execute("INSERT INTO users (username, password, display_name) VALUES ('bob', 'x', 'Bob')")
    con.execute("""CREATE TABLE reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
                   slot TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                   UNIQUE (day, slot))""")
    con.execute("INSERT INTO reservations (day, slot, user_id) VALUES ('2026-10-16', 'Abend', 1)")
    con.commit()
    con.close()

    create_app({'TESTING': True, 'DATABASE': str(path)})
    create_app({'TESTING': True, 'DATABASE': str(path)})  # idempotent
    con = sqlite3.connect(path)
    assert con.execute('SELECT username, email, reminder_minutes, notify_freed, session_version, calendar_token, '
                       'household_id FROM users').fetchall() == [('bob', None, 0, 0, 0, None, None)]
    assert con.execute('SELECT day, note FROM reservations').fetchall() == [('2026-10-16', None)]
    assert con.execute('SELECT COUNT(*) FROM households').fetchone() == (0,)
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name = 'users_calendar_token'").fetchone() == (1,)
    assert con.execute('SELECT COUNT(*) FROM login_failures').fetchone() == (0,)


@pytest.mark.parametrize('security', ['starttls', 'ssl', 'none'])
def test_deliver_uses_smtp_settings(app, monkeypatch, security):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, **kwargs):
            calls.append(('connect', host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def starttls(self, **kwargs):
            calls.append(('starttls',))

        def login(self, user, password):
            calls.append(('login', user, password))

        def send_message(self, msg):
            calls.append(('send', msg['To'], msg['From'], msg['Subject']))

    monkeypatch.setattr(smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setattr(smtplib, 'SMTP_SSL', FakeSMTP)
    app.config.update(SMTP_HOST='mail.infomaniak.com', SMTP_PORT=587, SMTP_SECURITY=security,
                      SMTP_USER='waschkueche@example.ch', SMTP_PASSWORD='pw')
    with app.app_context():
        REAL_DELIVER('bob@example.ch', 'Betreff', 'Text')
    assert calls[0] == ('connect', 'mail.infomaniak.com', 587)
    assert (('starttls',) in calls) == (security == 'starttls')
    assert ('login', 'waschkueche@example.ch', 'pw') in calls
    assert calls[-1] == ('send', 'bob@example.ch', 'Waschküche <waschkueche@example.ch>', 'Betreff')


def test_new_address_must_be_confirmed(app, client, login, csrf, mailbox, monkeypatch, query):
    set_prefs(client, login, 'bob', 'bob@example.ch', minutes=60, verified=False)
    t = login('bob')
    assert 'Noch nicht bestätigt' in client.get('/profile').get_data(as_text=True)
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Abend', 'csrf_token': t})
    at(monkeypatch, 2026, 10, 15, 16, 30)
    run(app)
    assert [m[1] for m in mailbox] == ['Waschküche: E-Mail-Adresse bestätigen']  # no reminder yet

    path = link_in(mailbox[0][2], '/verify-email/')
    other = app.test_client()  # works without being logged in
    assert 'Ja, bestätigen' in other.get(path).get_data(as_text=True)
    assert query("SELECT email_verified FROM users WHERE username = 'bob'") == [(0,)]  # GET changes nothing
    r = other.post(path, data={'csrf_token': csrf(other)})
    assert 'Adresse bestätigt' in r.get_data(as_text=True)
    assert query("SELECT email_verified FROM users WHERE username = 'bob'") == [(1,)]
    assert 'Noch nicht bestätigt' not in client.get('/profile').get_data(as_text=True)

    # A changed address needs a new confirmation, and the old link no longer fits
    set_prefs(client, login, 'bob', 'bob@neu.example.ch', minutes=60, verified=False)
    assert query("SELECT email_verified FROM users WHERE username = 'bob'") == [(0,)]
    assert other.post(path, data={'csrf_token': csrf(other)}).status_code == 400
    # Saving other settings with the same address keeps the state
    set_prefs(client, login, 'bob', 'bob@neu.example.ch', minutes=30, verified=False)
    run(app)
    assert [m[0] for m in mailbox if 'bestätigen' in m[1]] == ['bob@example.ch', 'bob@neu.example.ch']


def test_reset_mail_only_to_confirmed_address(app, client, csrf, login, mailbox):
    set_prefs(client, login, 'bob', 'bob@example.ch', verified=False)
    client.post('/logout', data={'csrf_token': csrf()})
    client.post('/forgot-password', data={'identifier': 'bob', 'csrf_token': csrf()})
    run(app)
    assert [m[1] for m in mailbox] == ['Waschküche: E-Mail-Adresse bestätigen']


def test_verification_mail_can_be_resent(app, client, login, mailbox, monkeypatch):
    t = set_prefs(client, login, 'bob', 'bob@example.ch', verified=False)
    client.post('/profile', data={'action': 'verify_email', 'csrf_token': t})  # within 15 min: not again
    at(monkeypatch, 2026, 10, 15, 14, 40)
    r = client.post('/profile', data={'action': 'verify_email', 'csrf_token': t}, follow_redirects=True)
    assert 'Bestätigungsmail an bob@example.ch verschickt' in r.get_data(as_text=True)
    run(app)
    assert len(mailbox) == 2


def test_invalid_or_expired_verification_link(app, client, login, mailbox, monkeypatch):
    assert client.get('/verify-email/forged').status_code == 400
    set_prefs(client, login, 'bob', 'bob@example.ch', verified=False)
    run(app)
    path = link_in(mailbox[0][2], '/verify-email/')
    import itsdangerous.timed
    real = itsdangerous.timed.TimestampSigner.get_timestamp
    monkeypatch.setattr(itsdangerous.timed.TimestampSigner, 'get_timestamp', lambda self: real(self) + 8 * 86400)
    assert client.get(path).status_code == 400


def test_admin_created_address_must_be_confirmed(app, client, login, mailbox, query):
    t = login('alice')
    client.post('/admin/users', data={'username': 'carl', 'password': 'secret1234', 'email': 'carl@example.ch',
                                      'csrf_token': t})
    assert '(unbestätigt)' in client.get('/admin').get_data(as_text=True)
    run(app)
    assert [(m[0], m[1]) for m in mailbox] == [('carl@example.ch', 'Waschküche: E-Mail-Adresse bestätigen')]


def test_existing_addresses_stay_confirmed_after_upgrade(tmp_path):
    path = tmp_path / 'old.db'
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                   password TEXT NOT NULL, display_name TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
                   email TEXT, reminder_minutes INTEGER NOT NULL DEFAULT 0, notify_freed INTEGER NOT NULL DEFAULT 0)""")
    con.execute("INSERT INTO users (username, password, display_name, email) VALUES ('bob', 'x', 'Bob', 'b@x.ch')")
    con.execute("INSERT INTO users (username, password, display_name) VALUES ('dora', 'x', 'Dora')")
    con.commit()
    con.close()

    app = create_app({'TESTING': True, 'DATABASE': str(path)})
    with app.app_context(), get_db() as db:
        db.execute("UPDATE users SET email_verified = 0 WHERE username = 'bob'")  # must survive restarts
    create_app({'TESTING': True, 'DATABASE': str(path)})
    con = sqlite3.connect(path)
    assert con.execute('SELECT username, email_verified FROM users ORDER BY id').fetchall() == \
        [('bob', 0), ('dora', 0)]
