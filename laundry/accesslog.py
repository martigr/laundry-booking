"""Gunicorn access log without the secret tokens of mail links and calendar feeds.

Used by the Dockerfile (`--logger-class laundry.accesslog.RedactingLogger`). Reset links are valid
for an hour, so whoever reads the log could otherwise take over the account.
"""
import re

from gunicorn.glogging import Logger

# Path segment after these prefixes is a token (see the routes in auth.py, profile.py and ical.py)
TOKEN_PATH = re.compile(r'(/(?:reset-password|unsubscribe|verify-email|calendar)/)[^/?#\s]+')


def redact(text):
    return TOKEN_PATH.sub(r'\1***', text)


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        # Every text atom: the request line, path and referer, but also headers and environ
        # variables ({raw_uri}e, ...) that a custom --access-logformat might use
        atoms = super().atoms(resp, req, environ, request_time)
        return {key: redact(value) if isinstance(value, str) else value for key, value in atoms.items()}
