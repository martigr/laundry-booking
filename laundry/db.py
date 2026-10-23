"""SQLite access: one connection per request, schema setup and settings."""
import os
import sqlite3
import time

from flask import current_app, g
from werkzeug.security import generate_password_hash

# Shortest allowed password: for users.check_password() and the first admin in init_db()
MIN_PASSWORD_LENGTH = 10

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        display_name TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        email TEXT,
        reminder_minutes INTEGER NOT NULL DEFAULT 0,
        notify_freed INTEGER NOT NULL DEFAULT 0,
        email_verified INTEGER NOT NULL DEFAULT 0,
        session_version INTEGER NOT NULL DEFAULT 0
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
    # Outbox: mails are queued here and sent by `flask send-notifications`
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedupe_key TEXT UNIQUE NOT NULL,
        to_addr TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        sent_at TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT
    )
    """,
    # Several accounts can belong to one household (users.household_id); they share the calendar feed
    """
    CREATE TABLE IF NOT EXISTS households (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL COLLATE NOCASE
    )
    """,
    # Failed logins of the last minutes, for auth.login_blocked(); key is 'user:<name>' or 'ip:<addr>'
    """
    CREATE TABLE IF NOT EXISTS login_failures (
        key TEXT NOT NULL,
        at TEXT NOT NULL
    )
    """,
    'CREATE INDEX IF NOT EXISTS login_failures_key ON login_failures (key, at)',
    # Audit log (audit.record()); user_id has no foreign key, so entries outlive deleted users
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        user_id INTEGER,
        username TEXT NOT NULL,
        action TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        ip TEXT
    )
    """,
    'CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log (at)',
]

# Columns added after the first release: (table, column, definition). Applied by init_db()
# to databases created before they existed.
ADDED_COLUMNS = [
    ('users', 'email', 'TEXT'),
    ('users', 'reminder_minutes', 'INTEGER NOT NULL DEFAULT 0'),
    ('users', 'notify_freed', 'INTEGER NOT NULL DEFAULT 0'),
    ('users', 'session_version', 'INTEGER NOT NULL DEFAULT 0'),
    ('users', 'email_verified', 'INTEGER NOT NULL DEFAULT 0'),
    ('users', 'calendar_token', 'TEXT'),  # secret of the calendar feed URL, NULL = no feed
    ('users', 'household_id', 'INTEGER REFERENCES households(id) ON DELETE SET NULL'),  # NULL = on their own
]

# audit_days: how long audit log entries are kept
DEFAULT_SETTINGS = {'horizon_weeks': '4', 'audit_days': '90'}


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


def enable_wal(con, timeout=30):
    """Switch the database file to WAL mode (stored in the file, better for parallel workers).

    SQLite doesn't wait for a busy database when changing the journal mode, it fails at once with
    "database is locked". On a new database, a second Gunicorn worker gets there while the first one
    is still in init_db()'s transaction, so retry until that one is done.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            if con.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
                con.execute('PRAGMA journal_mode = WAL')
            return
        except sqlite3.OperationalError as e:
            if 'locked' not in str(e) or time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def init_db():
    """Create the schema and, on an empty database, the first admin from the config."""
    con = connect()
    con.isolation_level = None  # explicit transaction below
    try:
        enable_wal(con)
        # IMMEDIATE takes the write lock, so parallel Gunicorn workers run this one after another
        con.execute('BEGIN IMMEDIATE')
        for statement in SCHEMA:
            con.execute(statement)
        added = []
        for table, column, definition in ADDED_COLUMNS:
            if column not in [r['name'] for r in con.execute(f'PRAGMA table_info({table})')]:
                con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
                added.append(column)
        if 'email_verified' in added:
            # Addresses from before verification existed keep getting mails
            con.execute("UPDATE users SET email_verified = 1 WHERE email IS NOT NULL AND email != ''")
        # After ADDED_COLUMNS, since older databases only get the column there
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_calendar_token ON users (calendar_token)')

        cfg = current_app.config
        user_count = con.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        if user_count == 0 and cfg['ADMIN_USERNAME'] and cfg['ADMIN_PASSWORD']:
            if len(cfg['ADMIN_PASSWORD']) < MIN_PASSWORD_LENGTH:
                raise RuntimeError(f'ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters long.')
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
