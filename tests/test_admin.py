
def test_admin_requires_admin(client, login):
    t = login('bob')
    assert client.get('/admin').status_code == 403
    r = client.post('/admin/users', data={'username': 'x', 'password': 'secret1234', 'csrf_token': t})
    assert r.status_code == 403


def test_create_and_delete_user(client, query, login):
    t = login('alice')
    client.post('/admin/users', data={'username': 'Carl', 'display_name': 'Carl', 'password': 'secret1234',
                                      'csrf_token': t})
    client.post('/admin/users', data={'username': 'carl', 'password': 'secret1234', 'csrf_token': t})
    client.post('/admin/users', data={'username': 'dora', 'password': 'short', 'csrf_token': t})
    assert query('SELECT username FROM users ORDER BY id') == [('alice',), ('bob',), ('carl',)]

    carl = query("SELECT id FROM users WHERE username = 'carl'")[0][0]
    t = login('carl')
    client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t})
    t = login('alice')
    client.post(f'/admin/users/{carl}/delete', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM users') == [(2,)]
    assert query('SELECT COUNT(*) FROM reservations') == [(0,)]


def test_delete_and_reset_ask_for_confirmation(client, login):
    login('alice')
    page = client.get('/admin').get_data(as_text=True)
    assert 'data-confirm-title="Benutzer löschen?"' in page
    assert 'data-confirm="Bob und alle Buchungen werden endgültig gelöscht.' in page
    assert 'data-confirm-title="Passwort zurücksetzen?"' in page
    assert 'onsubmit=' not in page


def test_cannot_delete_self(client, query, login):
    t = login('alice')
    client.post('/admin/users/1/delete', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM users') == [(2,)]


def test_reset_password(client, login):
    t = login('alice')
    r = client.post('/admin/users/2/reset', data={'csrf_token': t})
    assert r.status_code == 200  # shown in this response, not via a redirect and the session
    new_password = r.get_data(as_text=True).split('Neues Passwort für Bob: <code>')[1].split('<')[0]
    assert new_password not in str(client.get_cookie('session').value)
    assert new_password not in client.get('/admin').get_data(as_text=True)  # only once
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


def test_households(client, query, login):
    t = login('alice')
    client.post('/admin/households', data={'name': ' Müller 2. OG ', 'csrf_token': t})
    resp = client.post('/admin/households', data={'name': 'müller 2. og', 'csrf_token': t}, follow_redirects=True)
    assert 'gibt es schon' in resp.get_data(as_text=True)
    client.post('/admin/households', data={'name': '', 'csrf_token': t})
    client.post('/admin/households', data={'name': 'x' * 41, 'csrf_token': t})
    assert query('SELECT name FROM households') == [('Müller 2. OG',)]
    hid = query('SELECT id FROM households')[0][0]

    resp = client.post('/admin/users/2/household', data={'household_id': str(hid), 'csrf_token': t},
                       follow_redirects=True)
    assert 'Bob gehört jetzt zum Haushalt „Müller 2. OG“.' in resp.get_data(as_text=True)
    assert query("SELECT household_id FROM users WHERE username = 'bob'") == [(hid,)]
    page = client.get('/admin').get_data(as_text=True)
    assert f'<option value="{hid}" selected>Müller 2. OG</option>' in page
    assert '1 Mitglied' in page
    assert 'data-confirm-title="Haushalt löschen?"' in page
    assert 'name="display_name" placeholder="z. B. Anna Müller"' in page  # a person, not a household

    client.post('/admin/users/2/household', data={'household_id': '', 'csrf_token': t})
    assert query("SELECT household_id FROM users WHERE username = 'bob'") == [(None,)]


def test_household_errors(client, query, login):
    t = login('alice')
    client.post('/admin/households', data={'name': 'WG', 'csrf_token': t})
    hid = query('SELECT id FROM households')[0][0]
    assert client.post('/admin/users/2/household', data={'household_id': '999', 'csrf_token': t}).status_code == 400
    assert client.post('/admin/users/2/household', data={'household_id': 'x', 'csrf_token': t}).status_code == 400
    assert client.post('/admin/users/99/household', data={'household_id': str(hid), 'csrf_token': t}).status_code == 404
    assert client.post('/admin/households/999/delete', data={'csrf_token': t}).status_code == 404
    assert client.post('/admin/users/2/household', data={'household_id': str(hid)}).status_code == 400  # CSRF

    t = login('bob')
    assert client.post('/admin/households', data={'name': 'Meins', 'csrf_token': t}).status_code == 403
    assert client.post('/admin/users/2/household', data={'household_id': str(hid), 'csrf_token': t}).status_code == 403
    assert client.post(f'/admin/households/{hid}/delete', data={'csrf_token': t}).status_code == 403
    assert query('SELECT id, name FROM households') == [(hid, 'WG')]
    assert query('SELECT household_id FROM users') == [(None,), (None,)]
