import os

import pytest

from laundry import create_app


def test_create_user_and_make_admin(app, query):
    runner = app.test_cli_runner()
    r = runner.invoke(args=['create-user', 'Carl', '--name', 'Carl'], input='secret123\nsecret123\n')
    assert r.exit_code == 0, r.output
    assert runner.invoke(args=['create-user', 'carl'], input='secret123\nsecret123\n').exit_code == 1

    assert runner.invoke(args=['make-admin', 'carl']).exit_code == 0
    assert runner.invoke(args=['make-admin', 'nobody']).exit_code == 1
    assert query("SELECT is_admin FROM users WHERE username = 'carl'") == [(1,)]


def test_seed_demo_is_idempotent(app, query):
    runner = app.test_cli_runner()
    assert runner.invoke(args=['seed-demo']).exit_code == 0
    assert runner.invoke(args=['seed-demo']).exit_code == 0
    assert query('SELECT COUNT(*) FROM users') == [(3,)]  # alice, bob + sandra
    assert query('SELECT COUNT(*) FROM reservations') == [(7,)]


def test_first_admin_from_config(tmp_path):
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'new.db'),
                      'ADMIN_USERNAME': 'root', 'ADMIN_PASSWORD': 'secret123'})
    with app.app_context():
        from laundry.users import get_user_by_username
        assert get_user_by_username('root')['is_admin'] == 1


def test_unwritable_database_gives_clear_error(tmp_path, monkeypatch):
    # Simulates a Docker volume owned by another UID (os.access is always True for root)
    (tmp_path / 'laundry.db').touch()
    monkeypatch.setattr(os, 'access', lambda path, mode: not str(path).endswith('laundry.db'))
    with pytest.raises(RuntimeError, match='laundry.db is not writable for uid'):
        create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'laundry.db')})


def test_unwritable_database_without_uids(tmp_path, monkeypatch):
    # Windows has no os.getuid/os.getgid
    monkeypatch.delattr(os, 'getuid')
    monkeypatch.delattr(os, 'getgid')
    create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'laundry.db')})

    monkeypatch.setattr(os, 'access', lambda path, mode: not str(path).endswith('laundry.db'))
    with pytest.raises(RuntimeError, match=r'laundry.db is not writable\. SQLite'):
        create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'laundry.db')})
