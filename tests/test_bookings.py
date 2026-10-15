
def book(client, token, day, slot):
    return client.post('/book', data={'day': day, 'slot': slot, 'csrf_token': token, 'next': '/?week=' + day})


def test_book_and_conflict(client, reservations, login):
    t = login('bob')
    r = book(client, t, '2026-10-16', 'Abend')
    assert r.headers['Location'] == '/?week=2026-10-16'
    assert reservations() == [('2026-10-16', 'Abend', 'bob')]

    t = login('alice')
    book(client, t, '2026-10-16', 'Abend')
    assert reservations() == [('2026-10-16', 'Abend', 'bob')]


def test_book_rules(client, reservations, login):
    t = login('bob')
    book(client, t, '2026-10-15', 'Vormittag')   # already over
    book(client, t, '2026-12-31', 'Abend')       # beyond the 4 week horizon
    assert book(client, t, '2026-10-16', 'Nacht').status_code == 400
    assert book(client, t, 'kaputt', 'Abend').status_code == 400
    assert reservations() == []

    book(client, t, '2026-10-15', 'Nachmittag')  # running slot is still bookable
    assert reservations() == [('2026-10-15', 'Nachmittag', 'bob')]


def test_no_open_redirect(client, login):
    t = login('bob')
    for target in ['//evil.example', '/\\evil.example', 'https://evil.example']:
        r = client.post('/book', data={'day': '2026-10-16', 'slot': 'Abend', 'csrf_token': t, 'next': target})
        assert r.headers['Location'] == '/'


def test_cancel_permissions(app, client, query, reservations, login):
    t = login('bob')
    book(client, t, '2026-10-17', 'Abend')
    book(client, t, '2026-10-18', 'Abend')
    first, second = [r[0] for r in query('SELECT id FROM reservations ORDER BY id')]

    client.post(f'/cancel/{first}', data={'csrf_token': t})
    assert len(reservations()) == 1

    with app.app_context():
        from laundry.users import create_user
        create_user('carl', 'secret123', 'Carl')
    t = login('carl')
    assert client.post(f'/cancel/{second}', data={'csrf_token': t}).status_code == 403
    assert client.post('/cancel/999', data={'csrf_token': t}).status_code == 404

    t = login('alice')  # admins may cancel any booking
    client.post(f'/cancel/{second}', data={'csrf_token': t})
    assert reservations() == []


def test_past_booking_cannot_be_cancelled(client, query, reservations, login):
    t = login('bob')
    with client.application.app_context():
        from laundry.db import get_db
        with get_db() as con:
            con.execute("INSERT INTO reservations (day, slot, user_id) VALUES ('2026-10-14', 'Abend', 2)")
    rid = query('SELECT id FROM reservations')[0][0]
    client.post(f'/cancel/{rid}', data={'csrf_token': t})
    assert reservations() == [('2026-10-14', 'Abend', 'bob')]


def test_week_and_month_views(client, login):
    t = login('bob')
    book(client, t, '2026-10-15', 'Nachmittag')
    page = client.get('/').get_data(as_text=True)
    assert '12. – 18. Oktober' in page
    assert 'Läuft gerade' in page
    assert 'Nächster freier Slot' in page
    assert '26. Oktober – 1. November' in client.get('/?week=2026-10-28').get_data(as_text=True)
    assert 'Oktober 2026' in client.get('/month?month=2026-10').get_data(as_text=True)
    assert client.get('/month?month=kaputt').status_code == 200
    assert client.get('/?week=kaputt').status_code == 200


def test_display_name_is_escaped(client, login):
    t = login('bob')
    client.post('/profile', data={'action': 'name', 'display_name': "<b>x</b>');alert(1);//", 'csrf_token': t})
    book(client, t, '2026-10-16', 'Abend')
    login('alice')
    assert '<b>x</b>' not in client.get('/?week=2026-10-16').get_data(as_text=True)
    assert "');alert(1)" not in client.get('/admin').get_data(as_text=True)
