"""Public privacy notice (/privacy), reachable with and without login."""
from flask import current_app, render_template

from . import notifications
from .db import get_setting


def contact_link(contact):
    """Link for PRIVACY_CONTACT: an e-mail address becomes mailto:, a web or mailto: URL stays as it is."""
    if not contact:
        return None
    if contact.startswith(('mailto:', 'https://', 'http://')):
        return contact
    return f'mailto:{contact}' if '@' in contact and ':' not in contact else None


def privacy():
    contact = current_app.config['PRIVACY_CONTACT']
    return render_template('privacy.html', controller=current_app.config['PRIVACY_CONTROLLER'],
                           contact=contact.removeprefix('mailto:') if contact else None,
                           contact_href=contact_link(contact), audit_days=get_setting('audit_days'),
                           keep_mail_days=notifications.KEEP_SENT.days)


def init_app(app):
    app.add_url_rule('/privacy', 'privacy', privacy)
