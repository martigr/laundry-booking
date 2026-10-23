import re

from laundry.db import set_setting


def test_help_page(client, login):
    login('bob')
    html = client.get('/help').get_data(as_text=True)
    assert '<h1>Hilfe</h1>' in html
    assert 'href="/help" class="active">Hilfe</a>' in html
    assert '<strong>Vormittag</strong> (07:00 – 12:00), <strong>Nachmittag</strong> (12:00 – 17:00) und ' in html
    assert 'bis 4 Wochen im Voraus' in html
    assert 'Auf der Anmeldeseite unter «Passwort vergessen?»' in html  # mail is configured in the tests
    assert 'Als Admin' not in html
    assert 'href="/privacy">Datenschutz</a> (Link ganz unten' in html
    assert '<a href="#notiz">Eine Notiz zur Buchung</a>' in html
    assert '«Deine nächsten Buchungen» unter der Buchung' in html


def test_help_link_in_navigation(client, login):
    login('bob')
    assert 'href="/help" class="">Hilfe</a>' in client.get('/').get_data(as_text=True)


def test_help_screenshots_exist(client, login):
    login('bob')
    images = re.findall(r'<img src="([^"]+)"', client.get('/help').get_data(as_text=True))
    assert len(images) == 7
    for src in images:
        r = client.get(src)
        assert r.status_code == 200 and r.mimetype == 'image/png'
        r.close()


def test_help_follows_settings_and_role(app, client, login):
    with app.app_context():
        set_setting('horizon_weeks', 1)
    app.config['SMTP_HOST'] = ''
    login('alice')
    html = client.get('/help').get_data(as_text=True)
    assert 'bis 1 Woche im Voraus' in html
    assert 'Als Admin kannst du auch fremde Buchungen stornieren' in html
    assert 'Ein Admin setzt dir ein neues Passwort.' in html
    assert 'Anmeldeseite' not in html
