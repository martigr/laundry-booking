"""Home screen app: the service worker (/sw.js) and the offline page it shows (/offline).

Manifest and icons are static files (static/manifest.webmanifest, static/icons/), linked in base.html.
"""
import mimetypes

from flask import Blueprint, Response, render_template

from . import __version__

# Older Pythons and slim images without /etc/mime.types don't know the type, browsers expect it
mimetypes.add_type('application/manifest+json', '.webmanifest')

bp = Blueprint('pwa', __name__)


@bp.route('/sw.js')
def service_worker():
    # At the root, so its scope covers the whole app (a worker only controls pages below its own URL).
    # The cache name changes with every release, so the browser installs the new worker and
    # re-caches the offline page and style.css.
    js = render_template('sw.js', cache_name=f'waschkueche-offline-{__version__}')
    # no-cache instead of no-store: browsers may keep it but must ask before using it
    return Response(js, mimetype='text/javascript', headers={'Cache-Control': 'no-cache'})


@bp.route('/offline')
def offline():
    return render_template('offline.html')
