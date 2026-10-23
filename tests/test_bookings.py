
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
        create_user('carl', 'secret1234', 'Carl')
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


def test_cancel_confirmation_only_for_others_bookings(client, login):
    t = login('bob')
    book(client, t, '2026-10-17', 'Abend')
    assert 'data-confirm' not in client.get('/?week=2026-10-17').get_data(as_text=True)
    login('alice')
    page = client.get('/?week=2026-10-17').get_data(as_text=True)
    assert 'data-confirm="Die Buchung von Bob (Sa 17. Okt. · Abend) wird storniert."' in page
    assert 'id="confirm-dialog"' in page


def book_day(client, token, day):
    return client.post('/book-day', data={'day': day, 'csrf_token': token, 'next': '/?week=' + day})


def test_book_whole_day(client, reservations, login):
    t = login('bob')
    r = book_day(client, t, '2026-10-16')
    assert r.headers['Location'] == '/?week=2026-10-16'
    assert reservations() == [('2026-10-16', 'Vormittag', 'bob'), ('2026-10-16', 'Nachmittag', 'bob'),
                              ('2026-10-16', 'Abend', 'bob')]
    assert 'Gebucht: Fr 16. Okt. · ganzer Tag' in client.get('/').get_data(as_text=True)


def test_book_whole_day_skips_taken_and_past_slots(client, reservations, login):
    t = login('alice')
    book(client, t, '2026-10-17', 'Nachmittag')
    t = login('bob')
    book(client, t, '2026-10-17', 'Abend')       # own booking stays as it is
    book_day(client, t, '2026-10-17')
    assert reservations() == [('2026-10-17', 'Nachmittag', 'alice'), ('2026-10-17', 'Abend', 'bob'),
                              ('2026-10-17', 'Vormittag', 'bob')]
    assert 'Nachmittag war schon vergeben' in client.get('/').get_data(as_text=True)

    book_day(client, t, '2026-10-15')            # today: Vormittag is over, Nachmittag is running
    assert reservations()[3:] == [('2026-10-15', 'Nachmittag', 'bob'), ('2026-10-15', 'Abend', 'bob')]


def test_book_whole_day_rules(client, reservations, login):
    t = login('bob')
    book_day(client, t, '2026-10-14')            # over
    book_day(client, t, '2026-12-31')            # beyond the horizon
    assert reservations() == []
    assert book_day(client, t, 'kaputt').status_code == 400
    assert client.post('/book-day', data={'csrf_token': t}).status_code == 400


def test_whole_day_button_only_with_two_free_slots(client, login):
    t = login('bob')
    page = client.get('/').get_data(as_text=True)
    assert '2026-10-16 Ganzer Tag buchen' in page
    assert '2026-10-15 Alle freien buchen' in page   # Nachmittag (running) and Abend are free
    assert '2026-10-14 Alle freien' not in page and '2026-10-14 Ganzer Tag' not in page
    book(client, t, '2026-10-15', 'Abend')
    assert '2026-10-15 Alle freien buchen' not in client.get('/').get_data(as_text=True)


def test_adjacent_bookings_are_one_block(client, login):
    t = login('bob')
    book_day(client, t, '2026-10-16')
    book(client, t, '2026-10-17', 'Vormittag')
    book(client, t, '2026-10-17', 'Abend')
    book(client, t, '2026-10-18', 'Nachmittag')
    book(client, t, '2026-10-18', 'Abend')
    page = client.get('/').get_data(as_text=True)
    assert 'Ganzer Tag · 07:00 – 22:00' in page
    assert 'Vormittag · 07:00 – 12:00' in page
    assert 'Abend · 17:00 – 22:00' in page
    assert 'Nachmittag – Abend · 12:00 – 22:00' in page


def test_running_card_shows_whole_block(client, login):
    t = login('bob')
    book(client, t, '2026-10-15', 'Nachmittag')
    book(client, t, '2026-10-15', 'Abend')
    assert 'Nachmittag – Abend · bis 22:00' in client.get('/').get_data(as_text=True)


def test_cancel_block(app, client, query, reservations, login):
    t = login('bob')
    book_day(client, t, '2026-10-16')
    ids = [r[0] for r in query('SELECT id FROM reservations ORDER BY id')]

    with app.app_context():
        from laundry.users import create_user
        create_user('carl', 'secret1234', 'Carl')
    t = login('carl')
    assert client.post('/cancel', data={'id': ids, 'csrf_token': t}).status_code == 403
    assert client.post('/cancel', data={'id': ids + [999], 'csrf_token': t}).status_code == 404
    assert client.post('/cancel', data={'id': 'kaputt', 'csrf_token': t}).status_code == 400
    assert client.post('/cancel', data={'csrf_token': t}).status_code == 400
    assert len(reservations()) == 3

    t = login('bob')
    client.post('/cancel', data={'id': ids[1:], 'csrf_token': t})
    assert reservations() == [('2026-10-16', 'Vormittag', 'bob')]
    assert 'Storniert: Fr 16. Okt. · Nachmittag, Abend' in client.get('/').get_data(as_text=True)

    t = login('alice')  # admins may cancel any block
    client.post('/cancel', data={'id': ids[:1], 'csrf_token': t})
    assert reservations() == []


def test_cancel_block_skips_past_slots(client, query, reservations, login):
    t = login('bob')
    with client.application.app_context():
        from laundry.db import get_db
        with get_db() as con:
            con.executemany('INSERT INTO reservations (day, slot, user_id) VALUES (?, ?, 2)',
                            [('2026-10-15', 'Vormittag'), ('2026-10-15', 'Nachmittag')])
    ids = [r[0] for r in query('SELECT id FROM reservations ORDER BY id')]
    client.post('/cancel', data={'id': ids, 'csrf_token': t})
    assert reservations() == [('2026-10-15', 'Vormittag', 'bob')]
    client.post('/cancel', data={'id': ids[:1], 'csrf_token': t})
    assert reservations() == [('2026-10-15', 'Vormittag', 'bob')]


def household(app, *usernames):
    """Put the users into one new household."""
    from laundry.db import get_db
    from laundry.users import create_user
    with app.app_context():
        for name in usernames:
            if not get_db().execute('SELECT 1 FROM users WHERE username = ?', (name,)).fetchone():
                create_user(name, 'secret1234', name.capitalize())
        with get_db() as con:
            hid = con.execute("INSERT INTO households (name) VALUES ('WG')").lastrowid
            con.executemany('UPDATE users SET household_id = ? WHERE username = ?', [(hid, n) for n in usernames])


def test_housemates_cancel_each_others_bookings(app, client, query, reservations, login):
    household(app, 'bob', 'carol')
    t = login('carol')
    book(client, t, '2026-10-17', 'Abend')
    book(client, t, '2026-10-18', 'Abend')
    first, second = [r[0] for r in query('SELECT id FROM reservations ORDER BY id')]

    t = login('bob')
    resp = client.post(f'/cancel/{first}', data={'csrf_token': t}, follow_redirects=True)
    assert 'Storniert: Sa 17. Okt. · Abend' in resp.get_data(as_text=True)
    client.post('/cancel', data={'id': [second], 'csrf_token': t})
    assert reservations() == []


def test_household_does_not_extend_to_others(app, client, query, reservations, login):
    household(app, 'bob', 'carol')
    t = login('bob')
    book(client, t, '2026-10-17', 'Abend')
    bobs = query('SELECT id FROM reservations')[0][0]
    with app.app_context():
        from laundry.users import create_user
        create_user('dave', 'secret1234', 'Dave')
    t = login('dave')
    assert client.post(f'/cancel/{bobs}', data={'csrf_token': t}).status_code == 403

    # A block with one booking from outside the household is refused as a whole
    t = login('alice')
    book(client, t, '2026-10-18', 'Abend')
    alices = query("SELECT id FROM reservations WHERE day = '2026-10-18'")[0][0]
    t = login('carol')
    assert client.post('/cancel', data={'id': [bobs, alices], 'csrf_token': t}).status_code == 403
    assert len(reservations()) == 2


def test_housemate_bookings_look_like_own(app, client, login):
    household(app, 'bob', 'carol')
    t = login('carol')
    book(client, t, '2026-10-17', 'Abend')
    book(client, t, '2026-10-17', 'Nachmittag')
    login('bob')
    page = client.get('/?week=2026-10-17').get_data(as_text=True)
    assert 'class="slot mine cancelable"' in page
    assert 'Carol' in page and 'data-confirm' not in page  # no "Fremde Buchung" dialog
    assert 'Euer Haushalt' in page
    # Upcoming bookings of the household, with who booked them
    assert 'Eure nächsten Buchungen' in page
    assert 'Sa 17. Okt. · Carol</strong><span>Nachmittag – Abend · 12:00 – 22:00</span>' in page
    assert 'Euer Haushalt' in client.get('/month?month=2026-10').get_data(as_text=True)


def test_without_household_nothing_changes(client, login):
    t = login('bob')
    book(client, t, '2026-10-17', 'Abend')
    page = client.get('/?week=2026-10-17').get_data(as_text=True)
    assert 'Deine Buchung' in page and 'Deine nächsten Buchungen' in page
    assert 'Sa 17. Okt.</strong>' in page  # no name on own bookings


def test_book_whole_day_counts_housemate_slots_as_ours(app, client, reservations, login):
    household(app, 'bob', 'carol')
    t = login('carol')
    book(client, t, '2026-10-17', 'Abend')
    t = login('bob')
    book_day(client, t, '2026-10-17')
    text = client.get('/?week=2026-10-17').get_data(as_text=True)
    assert 'Gebucht: Sa 17. Okt. · Vormittag, Nachmittag' in text
    assert 'schon vergeben' not in text
    book_day(client, t, '2026-10-17')
    assert 'ist schon für euch gebucht' in client.get('/').get_data(as_text=True)
    assert len(reservations()) == 3


def set_note(client, token, ids, text, next_='/?week=2026-10-16'):
    return client.post('/note', data={'id': ids, 'note': text, 'csrf_token': token, 'next': next_})


def test_note_on_a_block(client, query, login):
    t = login('bob')
    book(client, t, '2026-10-16', 'Nachmittag')
    book(client, t, '2026-10-16', 'Abend')
    ids = [r[0] for r in query('SELECT id FROM reservations ORDER BY id')]
    page = client.get('/?week=2026-10-16').get_data(as_text=True)
    assert '+ Notiz für die anderen' in page
    assert 'maxlength="80"' in page

    r = set_note(client, t, ids, '  Hole die\r\nWäsche\x00 um 13 Uhr  ')
    assert r.headers['Location'] == '/?week=2026-10-16'
    assert query('SELECT note FROM reservations ORDER BY id') == [('Hole die Wäsche um 13 Uhr',)] * 2
    page = client.get('/?week=2026-10-16').get_data(as_text=True)
    assert 'Notiz gespeichert: Fr 16. Okt. · Nachmittag, Abend' in page
    assert '«Hole die Wäsche um 13 Uhr»' in page  # in the upcoming list
    assert 'value="Hole die Wäsche um 13 Uhr"' in page

    # Everyone sees it in the calendar and the month view
    login('alice')
    page = client.get('/?week=2026-10-16').get_data(as_text=True)
    assert page.count('<span class="s-note" title="Hole die Wäsche um 13 Uhr">') == 2
    assert 'Abend: Bob – Hole die Wäsche um 13 Uhr' in client.get('/month?month=2026-10').get_data(as_text=True)

    t = login('bob')
    r = set_note(client, t, ids, '   ', next_='/')
    assert query('SELECT note FROM reservations') == [(None,), (None,)]
    assert 'Notiz gelöscht: Fr 16. Okt. · Nachmittag, Abend' in client.get('/').get_data(as_text=True)


def test_block_shows_each_different_note_once(app, client, query, login):
    t = login('bob')
    book_day(client, t, '2026-10-16')
    ids = [r[0] for r in query('SELECT id FROM reservations ORDER BY id')]
    set_note(client, t, ids[:2], '40°')
    set_note(client, t, ids[2:], '60°')
    assert '«40° · 60°»' in client.get('/').get_data(as_text=True)


def test_note_rules(app, client, query, login):
    household(app, 'bob', 'carol')
    t = login('bob')
    book(client, t, '2026-10-17', 'Abend')
    rid = query('SELECT id FROM reservations')[0][0]
    assert client.post('/note', data={'note': 'x', 'csrf_token': t}).status_code == 400
    assert set_note(client, t, [rid], 'x' * 81).status_code == 400
    assert set_note(client, t, [rid], 'x' * 80).status_code == 302
    assert set_note(client, t, [rid, 999], 'y').status_code == 404
    assert query('SELECT note FROM reservations') == [('x' * 80,)]

    # Housemates may, anyone else (admins too) may not
    t = login('carol')
    set_note(client, t, [rid], 'von Carol')
    t = login('alice')
    assert set_note(client, t, [rid], 'von Alice').status_code == 403
    assert query('SELECT note FROM reservations') == [('von Carol',)]


def test_note_on_past_booking_is_refused(client, query, login):
    t = login('bob')
    with client.application.app_context():
        from laundry.db import get_db
        with get_db() as con:
            con.execute("INSERT INTO reservations (day, slot, user_id) VALUES ('2026-10-14', 'Abend', 2)")
    rid = query('SELECT id FROM reservations')[0][0]
    resp = client.post('/note', data={'id': rid, 'note': 'x', 'csrf_token': t}, follow_redirects=True)
    assert 'Vergangene Buchungen können nicht mehr geändert werden.' in resp.get_data(as_text=True)
    assert query('SELECT note FROM reservations') == [(None,)]


def test_note_is_escaped(client, query, login):
    t = login('bob')
    book(client, t, '2026-10-16', 'Abend')
    set_note(client, t, [query('SELECT id FROM reservations')[0][0]], '<b>x</b>"')
    login('alice')
    page = client.get('/?week=2026-10-16').get_data(as_text=True)
    assert '<b>x</b>' not in page
    assert 'title="&lt;b&gt;x&lt;/b&gt;&#34;"' in page


def test_running_card_shows_note(client, query, login):
    t = login('bob')
    book(client, t, '2026-10-15', 'Nachmittag')
    set_note(client, t, [query('SELECT id FROM reservations')[0][0]], 'Maschine ab 16 Uhr frei')
    login('alice')
    assert '<div class="note">«Maschine ab 16 Uhr frei»</div>' in client.get('/').get_data(as_text=True)
