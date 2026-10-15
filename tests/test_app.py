import sqlite3
from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

import app as laundry

NOW = datetime(2026, 10, 15, 14, 20)  # Thursday afternoon


@pytest.fixture
def client(tmp_path, monkeypatch):
    laundry.app.config.update(DATABASE=str(tmp_path / 'test.db'), TESTING=True)
    monkeypatch.setattr(laundry, 'now', lambda: NOW)
    laundry.init_db()
    with laundry.db() as con:
        for username, name, is_admin in [('alice', 'Alice', 1), ('bob', 'Bob', 0)]:
            con.execute("INSERT INTO users (username, password, display_name, is_admin) VALUES (?, ?, ?, ?)",
                        (username, generate_password_hash('secret123'), name, is_admin))
    return laundry.app.test_client()


def token(client):
    client.get('/login')
    with client.session_transaction() as s:
        return s['csrf']


def login(client, username):
    client.post('/login', data={'username': username, 'password': 'secret123', 'csrf_token': token(client)})
    return token(client)


def reservations():
    with laundry.db() as con:
        return [tuple(r) for r in con.execute(
            "SELECT r.day, r.slot, u.username FROM reservations r JOIN users u ON u.id = r.user_id ORDER BY r.id")]


def test_login(client):
    t = token(client)
    r = client.post('/login', data={'username': 'bob', 'password': 'wrong', 'csrf_token': t})
    assert 'Benutzername oder Passwort falsch' in r.get_data(as_text=True)
    r = client.post('/login', data={'username': 'bob', 'password': 'secret123', 'csrf_token': t})
    assert r.status_code == 302
    assert 'Wochenkalender' in client.get('/').get_data(as_text=True)


def test_requires_login(client):
    assert client.get('/').headers['Location'].endswith('/login')


def test_post_without_csrf_rejected(client):
    login(client, 'bob')
    r = client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend'})
    assert r.status_code == 400
    assert reservations() == []


def test_book_and_conflict(client):
    t = login(client, 'bob')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    assert reservations() == [('2026-10-16', 'Abend', 'bob')]

    t = login(client, 'alice')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    assert reservations() == [('2026-10-16', 'Abend', 'bob')]


def test_book_rules(client):
    t = login(client, 'bob')
    # already over / outside horizon / unknown slot
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Vormittag', 'csrf_token': t})
    client.post('/book', data={'day': '2026-12-31', 'slot': 'Abend', 'csrf_token': t})
    assert client.post('/book', data={'day': '2026-10-16', 'slot': 'Nacht', 'csrf_token': t}).status_code == 400
    assert reservations() == []
    # the running slot can still be booked
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Nachmittag', 'csrf_token': t})
    assert reservations() == [('2026-10-15', 'Nachmittag', 'bob')]


def test_cancel_permissions(client):
    t = login(client, 'bob')
    client.post('/book', data={'day': '2026-10-17', 'slot': 'Abend', 'csrf_token': t})
    client.post('/book', data={'day': '2026-10-18', 'slot': 'Abend', 'csrf_token': t})
    with laundry.db() as con:
        ids = [r[0] for r in con.execute("SELECT id FROM reservations ORDER BY id")]

    client.post(f'/cancel/{ids[0]}', data={'csrf_token': t})
    assert len(reservations()) == 1

    with laundry.db() as con:
        con.execute("INSERT INTO users (username, password, display_name) VALUES ('carl', ?, 'Carl')",
                    (generate_password_hash('secret123'),))
    t = login(client, 'carl')
    assert client.post(f'/cancel/{ids[1]}', data={'csrf_token': t}).status_code == 403

    t = login(client, 'alice')  # admin
    client.post(f'/cancel/{ids[1]}', data={'csrf_token': t})
    assert reservations() == []


def test_week_and_month_views(client):
    t = login(client, 'bob')
    client.post('/book', data={'day': '2026-10-15', 'slot': 'Nachmittag', 'csrf_token': t})
    page = client.get('/').get_data(as_text=True)
    assert '12. – 18. Oktober' in page
    assert 'Läuft gerade' in page
    assert 'Nächster freier Slot' in page
    page = client.get('/month?month=2026-10').get_data(as_text=True)
    assert 'Oktober 2026' in page
    assert client.get('/month?month=kaputt').status_code == 200


def test_display_name_is_escaped(client):
    t = login(client, 'bob')
    client.post('/profile', data={'action': 'name', 'display_name': "<b>x</b>');alert(1);//", 'csrf_token': t})
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    login(client, 'alice')
    page = client.get('/?week=2026-10-16').get_data(as_text=True)
    assert '<b>x</b>' not in page
    page = client.get('/admin').get_data(as_text=True)
    assert "');alert(1)" not in page


def test_admin(client):
    t = login(client, 'bob')
    assert client.get('/admin').status_code == 403
    assert client.post('/admin/users', data={'username': 'x', 'password': 'secret123', 'csrf_token': t}).status_code == 403

    t = login(client, 'alice')
    client.post('/admin/users', data={'username': 'Carl', 'display_name': 'Carl', 'password': 'secret123',
                                      'csrf_token': t})
    assert 'Carl' in client.get('/admin').get_data(as_text=True)
    with laundry.db() as con:
        carl = con.execute("SELECT id FROM users WHERE username = 'carl'").fetchone()['id']
    client.post(f'/admin/users/{carl}/delete', data={'csrf_token': t})
    with laundry.db() as con:
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2

    client.post('/admin/settings', data={'horizon_weeks': '1', 'csrf_token': t})
    assert laundry.get_setting('horizon_weeks') == '1'
    client.post('/book', data={'day': '2026-10-30', 'slot': 'Abend', 'csrf_token': t})
    assert reservations() == []


def test_migration_from_apartments(tmp_path):
    path = tmp_path / 'old.db'
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE apartments (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                            password TEXT NOT NULL, apartment_id INTEGER NOT NULL);
        CREATE TABLE reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, slot TEXT,
                                   apartment_id INTEGER NOT NULL);
        INSERT INTO apartments (name) VALUES ('Whg 31.1'), ('Whg 31.2'), ('Whg 31.3');
        INSERT INTO users (username, password, apartment_id) VALUES ('alice', 'h1', 1), ('bob', 'h2', 2);
        INSERT INTO reservations (day, slot, apartment_id) VALUES
            ('2026-10-01', 'Abend', 1), ('2026-10-02', 'Abend', 2), ('2026-10-03', 'Abend', 3);
    """)
    con.commit()
    con.close()

    laundry.app.config['DATABASE'] = str(path)
    laundry.init_db()
    laundry.init_db()  # idempotent

    with laundry.db() as con:
        users = [tuple(r) for r in con.execute("SELECT username, password, display_name FROM users ORDER BY id")]
        assert users == [('alice', 'h1', 'Whg 31.1'), ('bob', 'h2', 'Whg 31.2')]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert 'apartments' not in tables
    # apartment 31.3 had no user, so its booking is dropped
    assert reservations() == [('2026-10-01', 'Abend', 'alice'), ('2026-10-02', 'Abend', 'bob')]
