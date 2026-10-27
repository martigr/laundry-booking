"""HTTP security headers and /.well-known/security.txt."""
from datetime import timedelta

from flask import Response, current_app, request

from . import slots

PROJECT_URL = 'https://github.com/martigr/laundry-booking'

# Everything comes from this origin. Inline styles are allowed because the templates use a few
# style="" attributes; scripts are external files only, so injected markup can't run code.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def add_headers(response):
    h = response.headers
    h.setdefault('Content-Security-Policy', CSP)
    h.setdefault('X-Content-Type-Options', 'nosniff')
    h.setdefault('X-Frame-Options', 'DENY')  # for browsers without frame-ancestors
    # Reset and unsubscribe links carry their token in the path; don't hand it to other sites
    h.setdefault('Referrer-Policy', 'no-referrer')
    h.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    h.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=()')
    # A private building's app: keep every page, feed and file out of search results
    h.setdefault('X-Robots-Tag', 'noindex, nofollow')
    if request.is_secure:  # behind a proxy only with TRUSTED_PROXIES set, see README
        h.setdefault('Strict-Transport-Security', 'max-age=31536000')
    if request.endpoint != 'static':
        # Pages show who booked what; keep them out of shared and back-button caches
        h.setdefault('Cache-Control', 'no-store')
    return response


def security_txt():
    """RFC 9116: where to report vulnerabilities. SECURITY_CONTACT is the operator of this instance."""
    cfg = current_app.config
    contacts = []
    if cfg['SECURITY_CONTACT']:
        contact = cfg['SECURITY_CONTACT']
        contacts.append(contact if ':' in contact else f'mailto:{contact}')
    contacts.append(f'{PROJECT_URL}/security/advisories/new')
    # The file must expire within a year; it is generated, so it never goes stale
    expires = slots.now().date() + timedelta(days=180)
    lines = [f'Contact: {c}' for c in contacts] + [
        f'Expires: {expires.isoformat()}T00:00:00Z',
        'Preferred-Languages: de, en',
        f'Canonical: {cfg["BASE_URL"].rstrip("/")}/.well-known/security.txt',
        f'Policy: {PROJECT_URL}/blob/main/SECURITY.md',
    ]
    return Response('\n'.join(lines) + '\n', mimetype='text/plain')


def init_app(app):
    app.after_request(add_headers)
    app.add_url_rule('/.well-known/security.txt', 'security_txt', security_txt)
