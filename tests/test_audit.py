import sqlite3
import tomllib
from datetime import timedelta
from pathlib import Path

from conftest import NOW, PASSWORD

import laundry
from laundry import create_app, slots
from laundry.db import get_db
from laundry.notifications import reset_token, verify_token
from laundry.users import get_user


def entries(query):
    """Audit log as (username, action, detail), oldest first."""
    return query('SELECT username, action, detail FROM audit_log ORDER BY id')


def test_logins_are_logged(client, login, csrf, query):
    client.post('/login', data={'username': 'Bob', 'password': 'wrong', 'csrf_token': csrf()})
    client.post('/login', data={'username': 'nobody', 'password': 'wrong', 'csrf_token': csrf()})
    t = login('bob')
    client.post('/logout', data={'csrf_token': t})
    client.post('/logout', data={'csrf_token': csrf()})  # not logged in: nothing to log
    assert entries(query) == [
        ('bob', 'login_failed', 'falsches Passwort'),
        ('nobody', 'login_failed', 'unbekannter Benutzername'),
        ('bob', 'login', ''),
        ('bob', 'logout', ''),
    ]
    # Failed logins don't name a user; they only record what was typed
    assert query("SELECT user_id, ip FROM audit_log WHERE action = 'login_failed'") == [(None, '127.0.0.1')] * 2
    assert query("SELECT user_id FROM audit_log WHERE action = 'login'") == [(2,)]


def test_blocked_login_is_logged_once_per_window(client, csrf, query, monkeypatch):
    for _ in range(10):
        client.post('/login', data={'username': 'bob', 'password': 'wrong', 'csrf_token': csrf()})
    for _ in range(3):
        r = client.post('/login', data={'username': 'bob', 'password': PASSWORD, 'csrf_token': csrf()})
        assert r.status_code == 429
    assert query("SELECT COUNT(*) FROM audit_log WHERE action = 'login_blocked'") == [(1,)]
    monkeypatch.setattr(slots, 'now', lambda: NOW + timedelta(minutes=1))
    client.post('/login', data={'username': 'bob', 'password': PASSWORD, 'csrf_token': csrf()})
    assert query("SELECT COUNT(*) FROM audit_log WHERE action = 'login_blocked'") == [(1,)]


def test_bookings_are_logged(client, login, query):
    t = login('bob')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})  # taken: not logged
    client.post('/book-day', data={'day': '2026-10-17', 'csrf_token': t})
    rid = query("SELECT id FROM reservations WHERE day = '2026-10-16'")[0][0]
    client.post('/note', data={'id': rid, 'note': '60°', 'csrf_token': t})
    client.post('/note', data={'id': rid, 'note': '', 'csrf_token': t})
    client.post(f'/cancel/{rid}', data={'csrf_token': t})
    assert entries(query)[1:] == [
        ('bob', 'book', 'Fr 16. Okt. · Abend'),
        ('bob', 'book', 'Sa 17. Okt. · ganzer Tag'),
        ('bob', 'note', 'Fr 16. Okt. · Abend: «60°»'),
        ('bob', 'note', 'Fr 16. Okt. · Abend: gelöscht'),
        ('bob', 'cancel', 'Fr 16. Okt. · Abend'),
    ]


def test_cancelling_someone_elses_booking_names_the_owner(client, login, query):
    t = login('bob')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    rid = query('SELECT id FROM reservations')[0][0]
    t = login('alice')
    client.post(f'/cancel/{rid}', data={'csrf_token': t})
    assert entries(query)[-1] == ('alice', 'cancel', 'Fr 16. Okt. · Abend (Buchung von Bob)')


def test_rejected_cancel_is_not_logged(client, login, query):
    t = login('alice')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    rid = query('SELECT id FROM reservations')[0][0]
    t = login('bob')
    assert client.post(f'/cancel/{rid}', data={'csrf_token': t}).status_code == 403
    assert client.post('/cancel/999', data={'csrf_token': t}).status_code == 404
    assert [e[1] for e in entries(query)] == ['login', 'book', 'login']


def test_profile_changes_are_logged(client, login, query):
    t = login('bob')
    client.post('/profile', data={'action': 'name', 'display_name': 'Bobby', 'csrf_token': t})
    client.post('/profile', data={'action': 'name', 'display_name': '', 'csrf_token': t})  # invalid
    client.post('/profile', data={'action': 'notifications', 'email': 'bob@example.ch', 'reminder_minutes': '60',
                                  'notify_freed': '1', 'csrf_token': t})
    client.post('/profile', data={'action': 'calendar', 'csrf_token': t})
    client.post('/profile', data={'action': 'calendar', 'csrf_token': t})
    client.post('/profile', data={'action': 'calendar_delete', 'csrf_token': t})
    client.post('/profile', data={'action': 'password', 'old_password': 'wrong', 'new_password': 'x' * 12,
                                  'new_password2': 'x' * 12, 'csrf_token': t})
    client.post('/profile', data={'action': 'password', 'old_password': PASSWORD, 'new_password': 'x' * 12,
                                  'new_password2': 'x' * 12, 'csrf_token': t})
    assert entries(query)[1:] == [
        ('bob', 'display_name', 'Bob → Bobby'),
        ('bob', 'notifications', 'Erinnerung: 1 Stunde vorher, freie Slots: ja, neue Adresse: bob@example.ch'),
        ('bob', 'calendar_link', ''),
        ('bob', 'calendar_link', 'ersetzt den alten'),
        ('bob', 'calendar_link_deleted', ''),
        ('bob', 'password_changed', ''),
    ]


def test_admin_actions_are_logged(client, login, query):
    t = login('alice')
    client.post('/admin/users', data={'username': 'Carl', 'password': PASSWORD, 'csrf_token': t})
    client.post('/admin/households', data={'name': 'WG', 'csrf_token': t})
    hid = query('SELECT id FROM households')[0][0]
    client.post('/admin/users/2/household', data={'household_id': str(hid), 'csrf_token': t})
    client.post('/admin/users/2/household', data={'household_id': '', 'csrf_token': t})
    client.post(f'/admin/households/{hid}/delete', data={'csrf_token': t})
    client.post('/admin/users/2/reset', data={'csrf_token': t})
    carl = query("SELECT id FROM users WHERE username = 'carl'")[0][0]
    client.post(f'/admin/users/{carl}/delete', data={'csrf_token': t})
    client.post('/admin/settings', data={'horizon_weeks': '6', 'audit_days': '30', 'csrf_token': t})
    client.post('/admin/settings', data={'horizon_weeks': '6', 'audit_days': '30', 'csrf_token': t})  # unchanged
    assert entries(query)[1:] == [
        ('alice', 'user_created', 'carl'),
        ('alice', 'household_created', 'WG'),
        ('alice', 'household_assigned', 'bob → WG'),
        ('alice', 'household_assigned', 'bob → keiner'),
        ('alice', 'household_deleted', 'WG'),
        ('alice', 'user_password_reset', 'bob'),
        ('alice', 'user_deleted', 'carl'),
        ('alice', 'settings', 'Buchbar im Voraus: 4 → 6 Wochen; Audit-Log aufbewahren: 90 → 30 Tage'),
    ]


def test_password_reset_link_and_email_confirmation_are_logged(app, client, query, mailbox):
    with app.app_context():
        with get_db() as con:
            con.execute("UPDATE users SET email = 'bob@example.ch' WHERE username = 'bob'")
        token = verify_token(2, 'bob@example.ch')
        reset = reset_token(get_user(2))
    with client.session_transaction() as s:
        s['csrf'] = 'x'
    client.post(f'/verify-email/{token}', data={'csrf_token': 'x'})
    client.post(f'/reset-password/{reset}', data={'password': 'y' * 12, 'csrf_token': 'x'})
    assert entries(query) == [('bob', 'email_confirmed', 'bob@example.ch'), ('bob', 'password_reset_link', '')]


def test_audit_page(client, login, csrf):
    client.post('/login', data={'username': '<b>evil</b>', 'password': 'x', 'csrf_token': csrf()})
    t = login('alice')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    page = client.get('/admin/audit').get_data(as_text=True)
    assert 'Anmeldung fehlgeschlagen' in page and 'Gebucht' in page and 'Fr 16. Okt. · Abend' in page
    assert '&lt;b&gt;evil&lt;/b&gt;' in page and '<b>evil' not in page
    assert '15.10.2026 14:20:00' in page and '127.0.0.1' in page
    logins = client.get('/admin/audit?kind=login').get_data(as_text=True)
    assert 'Anmeldung fehlgeschlagen' in logins and 'Gebucht' not in logins
    assert 'Keine Einträge' in client.get('/admin/audit?kind=admin').get_data(as_text=True)
    assert 'Keine Einträge' in client.get('/admin/audit?page=5').get_data(as_text=True)
    assert client.get('/admin/audit?kind=nope').status_code == 400
    assert client.get('/admin/audit?page=0').status_code == 400


def test_audit_page_shows_deleted_users(client, login):
    login('bob')
    t = login('alice')
    client.post('/admin/users/2/delete', data={'csrf_token': t})
    page = client.get('/admin/audit').get_data(as_text=True)
    assert 'Benutzer gelöscht' in page
    assert 'bob <span class="hint">(gelöscht)</span>' in page  # bob's login stays in the log


def test_audit_page_paginates(app, client, login):
    t = login('alice')
    for _ in range(55):
        client.post('/admin/settings', data={'horizon_weeks': '5', 'csrf_token': t})
        client.post('/admin/settings', data={'horizon_weeks': '4', 'csrf_token': t})
    first = client.get('/admin/audit').get_data(as_text=True)
    assert 'Seite 1 von 3' in first and 'Ältere' in first
    last = client.get('/admin/audit?page=3').get_data(as_text=True)
    assert 'Seite 3 von 3' in last and 'Neuere' in last and 'Ältere' not in last


def test_audit_page_requires_admin(client, login):
    assert client.get('/admin/audit').status_code == 302
    login('bob')
    assert client.get('/admin/audit').status_code == 403


def test_old_entries_are_pruned(client, login, query, monkeypatch):
    t = login('alice')
    client.post('/admin/settings', data={'audit_days': '10', 'csrf_token': t})
    monkeypatch.setattr(slots, 'now', lambda: NOW + timedelta(days=10, minutes=1))
    client.post('/book', data={'day': '2026-10-26', 'slot': 'Abend', 'csrf_token': t})
    assert [e[1] for e in entries(query)] == ['book']


def test_retention_setting_is_validated(client, login, query):
    t = login('alice')
    for value in ['0', '3651', 'x', '']:
        r = client.post('/admin/settings', data={'audit_days': value, 'csrf_token': t}, follow_redirects=True)
        assert 'zwischen 1 und 3650 Tage' in r.get_data(as_text=True)
    # An invalid field saves nothing, also not the valid one next to it
    client.post('/admin/settings', data={'horizon_weeks': '8', 'audit_days': '0', 'csrf_token': t})
    assert query('SELECT key FROM settings') == []
    assert 'value="90"' in client.get('/admin').get_data(as_text=True)


def test_cli_user_commands_are_logged(app, query):
    runner = app.test_cli_runner()
    runner.invoke(args=['create-user', 'Dora', '--password', PASSWORD])
    runner.invoke(args=['make-admin', 'dora'])
    assert entries(query) == [('Kommandozeile', 'user_created', 'dora'), ('Kommandozeile', 'user_admin', 'dora')]
    assert query('SELECT user_id, ip FROM audit_log') == [(None, None)] * 2


def test_audit_table_is_added_to_an_existing_database(tmp_path):
    path = tmp_path / 'old.db'
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                   password TEXT NOT NULL, display_name TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0)""")
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("INSERT INTO settings VALUES ('horizon_weeks', '6')")
    con.commit()
    con.close()
    create_app({'TESTING': True, 'DATABASE': str(path)})
    create_app({'TESTING': True, 'DATABASE': str(path)})  # idempotent
    con = sqlite3.connect(path)
    assert con.execute('SELECT COUNT(*) FROM audit_log').fetchone() == (0,)
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name = 'audit_log_at'").fetchone() == (1,)
    assert con.execute('SELECT * FROM settings').fetchall() == [('horizon_weeks', '6')]


def test_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).parent.parent / 'pyproject.toml').read_text())
    assert laundry.__version__ == pyproject['project']['version']


def test_version_in_footer(client, login):
    assert f'Version {laundry.__version__}' not in client.get('/login').get_data(as_text=True)
    login('bob')
    assert f'Version {laundry.__version__}' in client.get('/').get_data(as_text=True)
