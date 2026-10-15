
def test_admin_requires_admin(client, login):
    t = login('bob')
    assert client.get('/admin').status_code == 403
    r = client.post('/admin/users', data={'username': 'x', 'password': 'secret123', 'csrf_token': t})
    assert r.status_code == 403


def test_create_and_delete_user(client, query, login):
    t = login('alice')
    client.post('/admin/users', data={'username': 'Carl', 'display_name': 'Carl', 'password': 'secret123',
                                      'csrf_token': t})
    client.post('/admin/users', data={'username': 'carl', 'password': 'secret123', 'csrf_token': t})
    client.post('/admin/users', data={'username': 'dora', 'password': 'short', 'csrf_token': t})
    assert query('SELECT username FROM users ORDER BY id') == [('alice',), ('bob',), ('carl',)]

    carl = query("SELECT id FROM users WHERE username = 'carl'")[0][0]
    t = login('carl')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    t = login('alice')
    client.post(f'/admin/users/{carl}/delete', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM users') == [(2,)]
    assert query('SELECT COUNT(*) FROM reservations') == [(0,)]


def test_cannot_delete_self(client, query, login):
    t = login('alice')
    client.post('/admin/users/1/delete', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM users') == [(2,)]


def test_reset_password(client, login):
    t = login('alice')
    page = client.post('/admin/users/2/reset', data={'csrf_token': t}, follow_redirects=True).get_data(as_text=True)
    new_password = page.split('Neues Passwort für Bob: ')[1].split('<')[0]
    client.post('/logout', data={'csrf_token': t})
    login('bob', new_password)
    assert client.get('/').status_code == 200


def test_horizon_setting(client, reservations, login):
    t = login('alice')
    client.post('/admin/settings', data={'horizon_weeks': '0', 'csrf_token': t})
    client.post('/admin/settings', data={'horizon_weeks': '1', 'csrf_token': t})
    client.post('/book', data={'day': '2026-10-30', 'slot': 'Abend', 'csrf_token': t})
    client.post('/book', data={'day': '2026-10-22', 'slot': 'Abend', 'csrf_token': t})
    assert reservations() == [('2026-10-22', 'Abend', 'alice')]
