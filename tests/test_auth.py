import pytest

from laundry import create_app


def test_login_and_logout(client, csrf):
    t = csrf()
    r = client.post('/login', data={'username': 'bob', 'password': 'wrong', 'csrf_token': t})
    assert 'Benutzername oder Passwort falsch' in r.get_data(as_text=True)

    r = client.post('/login', data={'username': ' Bob ', 'password': 'secret1234', 'csrf_token': t})
    assert r.status_code == 302
    assert 'Wochenkalender' in client.get('/').get_data(as_text=True)

    client.post('/logout', data={'csrf_token': csrf()})
    assert client.get('/').headers['Location'].endswith('/login')


def test_pages_require_login(client):
    for url in ['/', '/month', '/profile', '/admin']:
        assert client.get(url).headers['Location'].endswith('/login')


def test_post_without_csrf_rejected(client, reservations, login):
    login('bob')
    assert client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend'}).status_code == 400
    assert client.post('/logout').status_code == 400
    assert reservations() == []


def test_deleted_user_is_logged_out(client, app, login, query):
    bob = app.test_client()
    login('bob', c=bob)
    assert bob.get('/').status_code == 200

    t = login('alice')
    bob_id = query("SELECT id FROM users WHERE username = 'bob'")[0][0]
    client.post(f'/admin/users/{bob_id}/delete', data={'csrf_token': t})
    assert bob.get('/').headers['Location'].endswith('/login')


def test_secret_key_required_in_production(tmp_path, monkeypatch):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        create_app({'DATABASE': str(tmp_path / 'x.db')})


def test_profile(client, query, login, csrf):
    t = login('bob')
    client.post('/profile', data={'action': 'name', 'display_name': 'Bob 2. OG', 'csrf_token': t})
    client.post('/profile', data={'action': 'password', 'old_password': 'wrong',
                                  'new_password': 'newsecret1', 'new_password2': 'newsecret1', 'csrf_token': t})
    client.post('/profile', data={'action': 'password', 'old_password': 'secret1234',
                                  'new_password': 'short', 'new_password2': 'short', 'csrf_token': t})
    client.post('/profile', data={'action': 'password', 'old_password': 'secret1234',
                                  'new_password': 'newsecret1', 'new_password2': 'newsecreT1', 'csrf_token': t})
    assert 'Die neuen Passwörter stimmen nicht überein.' in client.get('/profile').get_data(as_text=True)
    client.post('/profile', data={'action': 'password', 'old_password': 'secret1234',
                                  'new_password': 'newsecret1', 'csrf_token': t})  # repetition missing
    assert query("SELECT display_name FROM users WHERE username = 'bob'") == [('Bob 2. OG',)]
    t = login('bob', 'secret1234')  # old password still valid

    assert 'name="new_password2"' in client.get('/profile').get_data(as_text=True)
    client.post('/profile', data={'action': 'password', 'old_password': 'secret1234',
                                  'new_password': 'newsecret1', 'new_password2': 'newsecret1', 'csrf_token': t})
    client.post('/logout', data={'csrf_token': csrf()})
    login('bob', 'newsecret1')
    assert client.get('/').status_code == 200
