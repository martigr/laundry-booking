"""User records, shared by the admin area, the CLI and the tests."""
import sqlite3

from werkzeug.security import generate_password_hash

from .db import get_db

MIN_PASSWORD_LENGTH = 6
MAX_NAME_LENGTH = 40


class UserError(ValueError):
    """Invalid input; the message is German and shown to the user."""


def create_user(username, password, display_name=None, is_admin=False):
    username = (username or '').strip().lower()
    display_name = (display_name or '').strip() or username
    if not username or len(username) > MAX_NAME_LENGTH or len(display_name) > MAX_NAME_LENGTH:
        raise UserError(f'Benutzername und Anzeigename müssen 1–{MAX_NAME_LENGTH} Zeichen lang sein.')
    if len(password or '') < MIN_PASSWORD_LENGTH:
        raise UserError(f'Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.')
    try:
        with get_db() as con:
            cur = con.execute(
                'INSERT INTO users (username, password, display_name, is_admin) VALUES (?, ?, ?, ?)',
                (username, generate_password_hash(password), display_name, int(is_admin)))
    except sqlite3.IntegrityError:
        raise UserError(f'Der Benutzername „{username}“ ist schon vergeben.') from None
    return cur.lastrowid


def get_user(user_id):
    return get_db().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()


def get_user_by_username(username):
    return get_db().execute('SELECT * FROM users WHERE username = ?',
                            ((username or '').strip().lower(),)).fetchone()


def set_password(user_id, password):
    if len(password or '') < MIN_PASSWORD_LENGTH:
        raise UserError(f'Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.')
    with get_db() as con:
        con.execute('UPDATE users SET password = ? WHERE id = ?', (generate_password_hash(password), user_id))
