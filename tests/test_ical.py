from datetime import datetime

from laundry import ical, slots
from laundry.db import get_db


def book(app, username, *day_slots):
    with app.app_context():
        with get_db() as con:
            uid = con.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()[0]
            con.executemany('INSERT INTO reservations (day, slot, user_id) VALUES (?, ?, ?)',
                            [(d, s, uid) for d, s in day_slots])


def create_link(client, login, username='bob'):
    token = login(username)
    client.post('/profile', data={'action': 'calendar', 'csrf_token': token})
    return token


def calendar_token(query, username='bob'):
    return query('SELECT calendar_token FROM users WHERE username = ?', username)[0][0]


def unfold(text):
    return text.replace('\r\n ', '')


def events(text):
    return unfold(text).split('BEGIN:VEVENT')[1:]


def test_profile_creates_a_calendar_link(client, login, query):
    login('bob')
    assert 'Kalender-Link erstellen' in client.get('/profile').get_data(as_text=True)
    assert calendar_token(query) is None

    create_link(client, login)
    token = calendar_token(query)
    assert len(token) >= 40
    page = client.get('/profile').get_data(as_text=True)
    assert f'https://wk.test/calendar/{token}.ics' in page
    assert f'webcal://wk.test/calendar/{token}.ics' in page


def test_feed_contains_only_own_bookings(app, client, login, query):
    book(app, 'bob', ('2026-10-16', 'Vormittag'))
    book(app, 'alice', ('2026-10-16', 'Abend'))
    create_link(client, login)
    client.post('/logout', data={'csrf_token': login('bob')})

    resp = client.get(f'/calendar/{calendar_token(query)}.ics')  # no session needed
    assert resp.status_code == 200
    assert resp.mimetype == 'text/calendar'
    assert resp.headers['Cache-Control'] == 'no-store'
    text = resp.get_data(as_text=True)
    assert text.startswith('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n')
    assert text.endswith('END:VCALENDAR\r\n')
    assert 'X-WR-CALNAME:Waschküche' in text
    [event] = events(text)
    # 07:00–12:00 in Zurich is 05:00–10:00 UTC in summer time
    assert 'DTSTART:20261016T050000Z' in event
    assert 'DTEND:20261016T100000Z' in event
    assert 'SUMMARY:Waschküche: Vormittag' in event
    assert 'Alice' not in text


def test_feed_merges_adjacent_slots_and_converts_winter_time(app, client, login, query):
    book(app, 'bob', ('2026-11-02', 'Nachmittag'), ('2026-11-02', 'Abend'), ('2026-11-03', 'Abend'))
    create_link(client, login)
    text = client.get(f'/calendar/{calendar_token(query)}.ics').get_data(as_text=True)
    first, second = events(text)
    # Winter time: UTC+1
    assert 'DTSTART:20261102T110000Z' in first and 'DTEND:20261102T210000Z' in first
    assert 'SUMMARY:Waschküche: Nachmittag – Abend' in first
    assert 'DTSTART:20261103T160000Z' in second
    uids = [line for line in unfold(text).split('\r\n') if line.startswith('UID:')]
    assert len(set(uids)) == 2 and all(u.endswith('@wk.test') for u in uids)


def test_feed_keeps_four_weeks_of_past_bookings(app, client, login, query):
    book(app, 'bob', ('2026-09-16', 'Abend'), ('2026-09-18', 'Abend'), ('2026-10-15', 'Vormittag'))
    create_link(client, login)
    text = client.get(f'/calendar/{calendar_token(query)}.ics').get_data(as_text=True)
    starts = [line for line in unfold(text).split('\r\n') if line.startswith('DTSTART')]
    assert starts == ['DTSTART:20260918T150000Z', 'DTSTART:20261015T050000Z']


def test_cancelled_booking_leaves_the_feed(app, client, login, query):
    book(app, 'bob', ('2026-10-16', 'Abend'))
    token = create_link(client, login)
    url = f'/calendar/{calendar_token(query)}.ics'
    assert len(events(client.get(url).get_data(as_text=True))) == 1
    rid = query('SELECT id FROM reservations')[0][0]
    client.post(f'/cancel/{rid}', data={'csrf_token': token})
    assert events(client.get(url).get_data(as_text=True)) == []


def test_unknown_token_is_404(client, login, query):
    assert client.get('/calendar/nope.ics').status_code == 404
    create_link(client, login)
    assert client.get('/calendar/.ics').status_code == 404
    assert client.get(f'/calendar/{calendar_token(query)}').status_code == 404  # without .ics


def test_renewing_the_link_disables_the_old_one(client, login, query):
    token = create_link(client, login)
    old = calendar_token(query)
    resp = client.post('/profile', data={'action': 'calendar', 'csrf_token': token}, follow_redirects=True)
    assert 'Der alte funktioniert nicht mehr' in resp.get_data(as_text=True)
    new = calendar_token(query)
    assert new != old
    assert client.get(f'/calendar/{old}.ics').status_code == 404
    assert client.get(f'/calendar/{new}.ics').status_code == 200


def test_deleting_the_link(client, login, query):
    token = create_link(client, login)
    old = calendar_token(query)
    client.post('/profile', data={'action': 'calendar_delete', 'csrf_token': token})
    assert calendar_token(query) is None
    assert client.get(f'/calendar/{old}.ics').status_code == 404
    assert 'Kalender-Link erstellen' in client.get('/profile').get_data(as_text=True)


def test_calendar_actions_need_login_and_csrf(client, login, query):
    resp = client.post('/profile', data={'action': 'calendar'})
    assert resp.status_code == 400  # CSRF check runs first
    login('bob')
    assert client.post('/profile', data={'action': 'calendar'}).status_code == 400
    assert calendar_token(query) is None


def test_deleted_user_feed_is_gone(client, login, query):
    create_link(client, login)
    token = calendar_token(query)
    admin = login('alice')
    bob_id = query("SELECT id FROM users WHERE username = 'bob'")[0][0]
    client.post(f'/admin/users/{bob_id}/delete', data={'csrf_token': admin})
    assert client.get(f'/calendar/{token}.ics').status_code == 404


def test_escape_and_fold():
    assert ical.escape('a,b;c\\d\ne') == 'a\\,b\\;c\\\\d\\ne'
    line = 'DESCRIPTION:' + 'ü' * 60
    folded = ical.fold(line)
    parts = folded.split('\r\n ')
    assert ''.join(parts) == line
    assert all(len(p.encode()) <= 75 for p in parts[:1]) and all(len(p.encode()) <= 74 for p in parts[1:])
    assert ical.fold('short') == 'short'


def test_dtstamp_uses_the_app_clock(app, client, login, query, monkeypatch):
    monkeypatch.setattr(slots, 'now', lambda: datetime(2026, 12, 1, 9, 0))
    book(app, 'bob', ('2026-12-01', 'Abend'))
    create_link(client, login)
    text = client.get(f'/calendar/{calendar_token(query)}.ics').get_data(as_text=True)
    assert 'DTSTAMP:20261201T080000Z' in text


def test_access_log_hides_the_feed_token():
    from laundry.accesslog import redact
    assert redact('GET /calendar/SECRET-tok_en.ics HTTP/1.1') == 'GET /calendar/*** HTTP/1.1'
