"""Web Push to the residents' browsers, configured with VAPID_PRIVATE_KEY (see README).

The browser subscribes at its push service (Google, Mozilla, Apple, Microsoft) and gives us an endpoint
URL and two keys (`push_subscriptions`). pywebpush encrypts the message for that browser and signs the
request with our VAPID key. Like mails, pushes go through the outbox in notifications.py; only the
notifier calls deliver().
"""
import base64
import binascii
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import current_app
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

# The endpoint comes from the browser, so it is user input: only the browsers' push services are
# accepted, otherwise the notifier could be made to POST to any (internal) address.
PUSH_HOSTS = ('fcm.googleapis.com', 'push.services.mozilla.com', 'push.apple.com', 'notify.windows.com')
MAX_ENDPOINT = 1024
TIMEOUT = 30


class PushError(Exception):
    """Sending failed; the message never contains the endpoint URL (it is a secret of the device)."""


class Gone(PushError):
    """The push service no longer knows the subscription (app removed, permission revoked)."""


def is_configured():
    return bool(current_app.config['VAPID_PRIVATE_KEY'])


def _b64decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def _b64encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def new_private_key():
    """A new VAPID key as base64url of the raw 32-byte private number (what VAPID_PRIVATE_KEY expects)."""
    key = ec.generate_private_key(ec.SECP256R1())
    return _b64encode(key.private_numbers().private_value.to_bytes(32, 'big'))


def _vapid():
    return Vapid.from_string(current_app.config['VAPID_PRIVATE_KEY'])


def public_key():
    """The browser needs this as `applicationServerKey` to subscribe."""
    raw = _vapid().public_key.public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)
    return _b64encode(raw)


def check_config():
    """Fail at startup instead of at the first push when the key can't be read."""
    if not is_configured():
        return
    try:
        public_key()
    except Exception:
        raise RuntimeError('VAPID_PRIVATE_KEY is not a valid key. Create one with: '
                           'flask --app laundry gen-vapid-key') from None


def subject():
    """Contact for the push services (VAPID "sub"): VAPID_SUBJECT, else MAIL_FROM, else BASE_URL."""
    cfg = current_app.config
    if cfg['VAPID_SUBJECT']:
        return cfg['VAPID_SUBJECT']
    return f'mailto:{cfg["MAIL_FROM"]}' if cfg['MAIL_FROM'] else cfg['BASE_URL']


def service_host(endpoint):
    return urlsplit(endpoint).hostname or ''


def valid_subscription(endpoint, p256dh, auth):
    """True for an https endpoint of a known push service with keys of the right size."""
    if not endpoint or len(endpoint) > MAX_ENDPOINT:
        return False
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ''
        port = url.port
        p256dh_raw, auth_raw = _b64decode(p256dh), _b64decode(auth)
    except (ValueError, binascii.Error):
        return False
    if url.scheme != 'https' or port not in (None, 443) or url.username or url.password:
        return False
    if not any(host == h or host.endswith('.' + h) for h in PUSH_HOSTS):
        return False
    # p256dh: the browser's P-256 public key, uncompressed; auth: 16 random bytes
    return len(p256dh_raw) == 65 and p256dh_raw[0] == 4 and len(auth_raw) == 16


def deliver(subscription, payload, ttl):
    """Send one encrypted message. Raises Gone for 404/410 and PushError for other failures."""
    try:
        webpush({'endpoint': subscription['endpoint'],
                 'keys': {'p256dh': subscription['p256dh'], 'auth': subscription['auth']}},
                data=payload, vapid_private_key=_vapid(), vapid_claims={'sub': subject()},
                ttl=max(int(ttl), 0), timeout=TIMEOUT)
    except WebPushException as e:
        if e.status_code in (404, 410):
            raise Gone(f'push service answered {e.status_code}') from None
        raise PushError(f'push service answered {e.status_code or "nothing"}') from None
    except requests.RequestException as e:
        raise PushError(type(e).__name__) from None
