import pytest

import laundry
from laundry import create_app
from laundry.privacy import contact_link


@pytest.fixture
def configured(app):
    app.config.update(PRIVACY_CONTROLLER='Hausverein Musterstrasse 12', PRIVACY_CONTACT='verwaltung@example.ch')
    return app


def test_privacy_page_is_public(client, configured):
    r = client.get('/privacy')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert '<h1>Datenschutz</h1>' in html
    assert 'Hausverein Musterstrasse 12' in html
    assert '<a href="mailto:verwaltung@example.ch">verwaltung@example.ch</a>' in html
    assert 'Audit-Log: 90 Tage' in html
    assert 'Version' not in html
    assert 'Abmelden' not in html


def test_privacy_page_logged_in_uses_normal_layout(client, configured, login):
    login('bob')
    html = client.get('/privacy').get_data(as_text=True)
    assert '<h1>Datenschutz</h1>' in html
    assert 'Abmelden' in html
    assert f'Version {laundry.__version__}' in html


def test_privacy_page_shows_audit_retention_setting(client, app, login):
    csrf = login('alice')
    client.post('/admin/settings', data={'audit_days': '30', 'csrf_token': csrf})
    assert 'Audit-Log: 30 Tage' in client.get('/privacy').get_data(as_text=True)


def test_privacy_page_without_contact(client):
    html = client.get('/privacy').get_data(as_text=True)
    assert 'noch keine Kontaktangaben hinterlegt' in html


def test_privacy_contact_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVACY_CONTROLLER', 'Ruth Muster')
    monkeypatch.setenv('PRIVACY_CONTACT', 'mailto:ruth@example.ch')
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(tmp_path / 'env.db')})
    html = app.test_client().get('/privacy').get_data(as_text=True)
    assert 'Ruth Muster' in html
    assert '<a href="mailto:ruth@example.ch">ruth@example.ch</a>' in html


def test_contact_link():
    assert contact_link('a@example.ch') == 'mailto:a@example.ch'
    assert contact_link('mailto:a@example.ch') == 'mailto:a@example.ch'
    assert contact_link('https://example.ch/kontakt') == 'https://example.ch/kontakt'
    assert contact_link('Briefkasten EG') is None
    assert contact_link('javascript:alert(1)') is None
    assert contact_link(None) is None


def test_contact_is_escaped(client, app):
    app.config.update(PRIVACY_CONTROLLER='<b>X</b>', PRIVACY_CONTACT='javascript:alert(1)')
    html = client.get('/privacy').get_data(as_text=True)
    assert '<b>X</b>' not in html
    assert 'href="javascript:' not in html


@pytest.mark.parametrize('path', ['/login', '/forgot-password', '/reset-password/invalid', '/privacy'])
def test_privacy_link_without_version_when_logged_out(client, path):
    html = client.get(path, follow_redirects=True).get_data(as_text=True)
    assert 'href="/privacy"' in html
    assert 'Version' not in html


def test_privacy_link_in_footer_when_logged_in(client, login):
    login('bob')
    for path in ['/', '/month', '/profile']:
        assert 'href="/privacy"' in client.get(path).get_data(as_text=True)
