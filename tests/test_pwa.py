import json
import struct
from pathlib import Path

import pytest

from laundry import __version__

STATIC = Path(__file__).resolve().parent.parent / 'laundry' / 'static'


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    return struct.unpack('>II', data[16:24])


# --- Manifest and icons --------------------------------------------------------------------------

def test_manifest(client):
    r = client.get('/static/manifest.webmanifest')
    assert r.status_code == 200
    assert r.mimetype == 'application/manifest+json'
    m = json.loads(r.get_data(as_text=True))
    assert m['name'] == 'Waschküche'
    assert m['display'] == 'standalone'
    # Relative to the manifest in /static/, so the app also works under a path prefix
    assert m['start_url'] == m['scope'] == '../'
    assert {i['purpose'] for i in m['icons']} == {'any', 'maskable'}
    for icon in m['icons']:
        w, h = (int(n) for n in icon['sizes'].split('x'))
        assert png_size(STATIC / icon['src']) == (w, h)


def test_apple_touch_icon_is_opaque():
    """iOS rounds the corners itself and shows transparent parts black."""
    path = STATIC / 'icons' / 'apple-touch-icon.png'
    assert png_size(path) == (180, 180)
    assert path.read_bytes()[25] == 2  # PNG colour type 2: RGB without alpha


@pytest.mark.parametrize('url', ['/login', '/', '/privacy', '/offline'])
def test_pages_link_manifest_and_icon(client, login, url):
    if url == '/':
        login('bob')
    html = client.get(url).get_data(as_text=True)
    assert '<link rel="manifest" href="/static/manifest.webmanifest">' in html
    assert '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">' in html
    assert '<meta name="theme-color"' in html
    assert 'src="/static/pwa.js" data-sw="/sw.js"' in html


# --- Service worker ------------------------------------------------------------------------------

def test_service_worker(client):
    r = client.get('/sw.js')  # public: it is also registered on the login page
    assert r.status_code == 200
    assert r.mimetype == 'text/javascript'
    assert r.headers['Cache-Control'] == 'no-cache'
    js = r.get_data(as_text=True)
    assert f'const CACHE = "waschkueche-offline-{__version__}";' in js
    assert 'const OFFLINE_URL = "/offline";' in js
    assert '"/static/style.css"' in js and '"/static/offline.js"' in js


def test_service_worker_under_a_path_prefix(app):
    client = app.test_client()
    js = client.get('/sw.js', base_url='http://localhost/wk').get_data(as_text=True)
    assert 'const OFFLINE_URL = "/wk/offline";' in js
    assert '"/wk/static/style.css"' in js


def test_service_worker_only_answers_get(client, csrf):
    assert client.post('/sw.js', data={'csrf_token': csrf()}).status_code == 405


def test_registration_script_exists():
    js = (STATIC / 'pwa.js').read_text()
    assert 'serviceWorker' in js and 'dataset.sw' in js


# --- Offline page --------------------------------------------------------------------------------

def test_offline_page_is_public(client):
    r = client.get('/offline')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Keine Verbindung' in html
    assert '/static/offline.js' in html
    assert '<script>' not in html


def test_offline_page_has_nothing_personal(client, login):
    """The worker caches it with the visitor's cookies; it must look the same for everybody."""
    login('bob')
    html = client.get('/offline').get_data(as_text=True)
    assert 'Bob' not in html
    assert 'Abmelden' not in html
    assert 'csrf_token' not in html


def test_offline_page_keeps_flash_messages(app, client, login):
    """The worker fetches it in the background; that must not eat a message meant for the next page."""
    login('bob')
    with client.session_transaction() as s:
        s['_flashes'] = [('ok', 'Gebucht.')]
    client.get('/offline')
    assert 'Gebucht.' in client.get('/').get_data(as_text=True)


def test_offline_page_only_answers_get(client, csrf):
    assert client.post('/offline', data={'csrf_token': csrf()}).status_code == 405
