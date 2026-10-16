"""SQLite access: one connection per request, schema setup and settings."""
import os
import sqlite3

from flask import current_app, g
from werkzeug.security import generate_password_hash

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        display_name TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        slot TEXT NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        UNIQUE (day, slot)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
]

DEFAULT_SETTINGS = {'horizon_weeks': '4'}


def connect():
    con = sqlite3.connect(current_app.config['DATABASE'], timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    return con


def get_db():
    """The request's connection. Use `with get_db() as con:` for writes (commits on success)."""
    if 'db' not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    con = g.pop('db', None)
    if con is not None:
        con.close()


def ensure_writable(path):
    """Fail early with a clear message if SQLite can't write the database.

    Typical cause: a Docker volume or host folder owned by a different UID than the app.
    SQLite needs write access to the file and to its directory (journal/WAL files).
    """
    directory = os.path.dirname(os.path.abspath(path))
    # No UIDs on Windows; there os.access only checks the read-only attribute
    uid = os.getuid() if hasattr(os, 'getuid') else None
    as_uid = f' as uid {uid}' if uid is not None else ''
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f'Cannot create the database directory {directory}{as_uid}: {e.strerror}') from None
    for p in [directory, path, path + '-wal', path + '-shm']:
        if os.path.exists(p) and not os.access(p, os.W_OK):
            if uid is None:
                raise RuntimeError(f'{p} is not writable. SQLite needs write access to the database file '
                                   f'and its directory.')
            raise RuntimeError(
                f'{p} is not writable for uid {uid} (owned by uid {os.stat(p).st_uid}). '
                f'SQLite needs write access to the database file and its directory. '
                f'Fix the owner, e.g. chown -R {uid}:{os.getgid()} <folder>; in Docker see "Volumes and permissions" '
                f'in the README.')


def init_db():
    """Create the schema and, on an empty database, the first admin from the config."""
    con = connect()
    con.isolation_level = None  # explicit transaction below
    try:
        con.execute('PRAGMA journal_mode = WAL')  # stored in the file, better for parallel workers
        # IMMEDIATE takes the write lock, so parallel Gunicorn workers run this one after another
        con.execute('BEGIN IMMEDIATE')
        for statement in SCHEMA:
            con.execute(statement)

        cfg = current_app.config
        user_count = con.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        if user_count == 0 and cfg['ADMIN_USERNAME'] and cfg['ADMIN_PASSWORD']:
            con.execute(
                'INSERT INTO users (username, password, display_name, is_admin) VALUES (?, ?, ?, 1)',
                (cfg['ADMIN_USERNAME'], generate_password_hash(cfg['ADMIN_PASSWORD']), cfg['ADMIN_USERNAME']))
            current_app.logger.info('Created admin user %s from ADMIN_USERNAME', cfg['ADMIN_USERNAME'])
        elif user_count and not con.execute('SELECT 1 FROM users WHERE is_admin = 1').fetchone():
            current_app.logger.warning('No admin user yet — run: flask --app laundry make-admin <username>')
        con.execute('COMMIT')
    except Exception:
        if con.in_transaction:
            con.execute('ROLLBACK')
        raise
    finally:
        con.close()


def get_setting(key):
    row = get_db().execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else DEFAULT_SETTINGS[key]


def set_setting(key, value):
    with get_db() as con:
        con.execute('INSERT INTO settings (key, value) VALUES (?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, str(value)))


def init_app(app):
    app.teardown_appcontext(close_db)
