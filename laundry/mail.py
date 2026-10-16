"""Sending mail over SMTP, configured with SMTP_* and MAIL_FROM (see README)."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from flask import current_app


def is_configured():
    cfg = current_app.config
    return bool(cfg['SMTP_HOST'] and cfg['MAIL_FROM'])


def deliver(to_addr, subject, body):
    """Send one plain-text mail. Raises on any SMTP or network error."""
    cfg = current_app.config
    msg = EmailMessage()
    msg['From'] = formataddr((cfg['MAIL_FROM_NAME'], cfg['MAIL_FROM']))
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=cfg['MAIL_FROM'].rpartition('@')[2])
    msg.set_content(body)

    context = ssl.create_default_context()
    if cfg['SMTP_SECURITY'] == 'ssl':
        smtp = smtplib.SMTP_SSL(cfg['SMTP_HOST'], cfg['SMTP_PORT'], context=context, timeout=30)
    else:
        smtp = smtplib.SMTP(cfg['SMTP_HOST'], cfg['SMTP_PORT'], timeout=30)
    with smtp:
        if cfg['SMTP_SECURITY'] == 'starttls':
            smtp.starttls(context=context)
        if cfg['SMTP_USER']:
            smtp.login(cfg['SMTP_USER'], cfg['SMTP_PASSWORD'] or '')
        smtp.send_message(msg)
