"""User records, shared by the admin area, the CLI and the tests."""
import re
import sqlite3

from werkzeug.security import generate_password_hash

from .db import MIN_PASSWORD_LENGTH, get_db

MAX_PASSWORD_LENGTH = 128  # long enough for passphrases, short enough not to hash megabytes
MAX_NAME_LENGTH = 40
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class UserError(ValueError):
    """Invalid input; the message is German and shown to the user."""


def clean_email(email):
    """Normalised address, None for empty input; raises UserError if it doesn't look like one."""
    email = (email or '').strip()
    if not email:
        return None
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise UserError(f'„{email}“ ist keine gültige E-Mail-Adresse.')
    return email


def check_password(password):
    """Raise UserError unless the password has an allowed length."""
    if not MIN_PASSWORD_LENGTH <= len(password or '') <= MAX_PASSWORD_LENGTH:
        raise UserError(f'Das Passwort muss {MIN_PASSWORD_LENGTH}–{MAX_PASSWORD_LENGTH} Zeichen lang sein.')


def create_user(username, password, display_name=None, is_admin=False, email=None):
    email = clean_email(email)
    username = (username or '').strip().lower()
    display_name = (display_name or '').strip() or username
    if not username or len(username) > MAX_NAME_LENGTH or len(display_name) > MAX_NAME_LENGTH:
        raise UserError(f'Benutzername und Anzeigename müssen 1–{MAX_NAME_LENGTH} Zeichen lang sein.')
    check_password(password)
    try:
        with get_db() as con:
            cur = con.execute(
                'INSERT INTO users (username, password, display_name, is_admin, email) VALUES (?, ?, ?, ?, ?)',
                (username, generate_password_hash(password), display_name, int(is_admin), email))
    except sqlite3.IntegrityError:
        raise UserError(f'Der Benutzername „{username}“ ist schon vergeben.') from None
    return cur.lastrowid


def get_user(user_id):
    return get_db().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()


def get_user_by_username(username):
    return get_db().execute('SELECT * FROM users WHERE username = ?',
                            ((username or '').strip().lower(),)).fetchone()


def set_password(user_id, password):
    """Set a new password and end all sessions of the user (see auth.load_user)."""
    check_password(password)
    with get_db() as con:
        con.execute('UPDATE users SET password = ?, session_version = session_version + 1 WHERE id = ?',
                    (generate_password_hash(password), user_id))
