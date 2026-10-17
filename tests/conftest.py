import secrets
from datetime import datetime

import pytest

from laundry import create_app, mail, slots
from laundry.db import get_db
from laundry.users import create_user

# Fixed clock for all tests: Thursday afternoon
NOW = datetime(2026, 10, 15, 14, 20)
PASSWORD = 'secret1234'


MAIL_CONFIG = {'SMTP_HOST': 'smtp.test', 'MAIL_FROM': 'waschkueche@example.ch', 'BASE_URL': 'https://wk.test'}


@pytest.fixture
def mailbox(monkeypatch):
    """Mails 'sent' during the test as (to, subject, body); nothing reaches a real server."""
    sent = []
    monkeypatch.setattr(mail, 'deliver', lambda to, subject, body: sent.append((to, subject, body)))
    return sent


@pytest.fixture
def app(tmp_path, monkeypatch, mailbox):
    monkeypatch.setattr(slots, 'now', lambda: NOW)
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(tmp_path / 'test.db'), **MAIL_CONFIG})
    with app.app_context():
        create_user('alice', PASSWORD, 'Alice', is_admin=True)
        create_user('bob', PASSWORD, 'Bob')
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def query(app):
    """Run a read query against the test database."""
    def run(sql, *params):
        with app.app_context():
            return [tuple(r) for r in get_db().execute(sql, params).fetchall()]
    return run


@pytest.fixture
def reservations(query):
    return lambda: query('SELECT r.day, r.slot, u.username FROM reservations r '
                         'JOIN users u ON u.id = r.user_id ORDER BY r.id')


@pytest.fixture
def csrf(client):
    """csrf() -> the CSRF token of the client's session (or of another client)."""
    def get(c=client):
        with c.session_transaction() as s:
            return s.setdefault('csrf', secrets.token_urlsafe(16))
    return get


@pytest.fixture
def login(client, csrf):
    """login('bob') logs the client in and returns a fresh CSRF token."""
    def run(username, password=PASSWORD, c=client):
        c.post('/login', data={'username': username, 'password': password, 'csrf_token': csrf(c)})
        return csrf(c)
    return run
