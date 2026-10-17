"""Login, session handling, CSRF protection, login throttling and access decorators."""
import functools
import secrets
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import mail, notifications, slots
from .db import get_db
from .users import MIN_PASSWORD_LENGTH, UserError, get_user, get_user_by_username, set_password

bp = Blueprint('auth', __name__)

# Login throttling: failed attempts within LOGIN_WINDOW, per username and per client address
LOGIN_WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_USER = 10
MAX_FAILURES_PER_IP = 20
# Checked for unknown usernames, so a failed login takes as long whether the account exists or not
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def check_csrf():
    if request.method == 'POST':
        token = request.form.get('csrf_token', '')
        if not token or not secrets.compare_digest(token, session.get('csrf', '')):
            abort(400, 'Ungültiges Formular-Token – bitte Seite neu laden.')


def load_user():
    user_id = session.get('user_id')
    g.user = get_user(user_id) if user_id else None
    # User deleted, or password changed since this session started (set_password bumps the version)
    if user_id and (g.user is None or session.get('sv', 0) != g.user['session_version']):
        session.clear()
        g.user = None


def keep_session():
    """The current user changed their own password: keep this session, the others have ended."""
    session['sv'] = get_user(g.user['id'])['session_version']


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('auth.login'))
        return view(**kwargs)
    return wrapped_view


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped_view(**kwargs):
        if not g.user['is_admin']:
            abort(403)
        return view(**kwargs)
    return wrapped_view


def _failure_keys(username):
    return [f'user:{username}', f'ip:{request.remote_addr}']


def login_blocked(username):
    """True if this username or this client had too many failed logins lately."""
    since = (slots.now() - LOGIN_WINDOW).isoformat(timespec='seconds')
    counts = [get_db().execute('SELECT COUNT(*) FROM login_failures WHERE key = ? AND at > ?',
                               (key, since)).fetchone()[0] for key in _failure_keys(username)]
    return counts[0] >= MAX_FAILURES_PER_USER or counts[1] >= MAX_FAILURES_PER_IP


def record_login_failure(username):
    now = slots.now()
    with get_db() as con:
        con.execute('DELETE FROM login_failures WHERE at <= ?', ((now - LOGIN_WINDOW).isoformat(timespec='seconds'),))
        con.executemany('INSERT INTO login_failures (key, at) VALUES (?, ?)',
                        [(key, now.isoformat(timespec='seconds')) for key in _failure_keys(username)])


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        name = username.strip().lower()
        # %r keeps line breaks in usernames from forging log lines
        if login_blocked(name):
            current_app.logger.warning('Login blocked (too many failures) for %r from %s', name, request.remote_addr)
            return render_template('login.html', username=username,
                                   error='Zu viele Fehlversuche. Bitte in 15 Minuten nochmals versuchen.'), 429
        user = get_user_by_username(name)
        valid = check_password_hash(user['password'] if user else _DUMMY_HASH, request.form.get('password', ''))
        if user and valid:
            with get_db() as con:
                con.execute('DELETE FROM login_failures WHERE key = ?', (f'user:{name}',))
            session.clear()  # new session id content, against session fixation
            session['user_id'] = user['id']
            session['sv'] = user['session_version']
            session.permanent = bool(request.form.get('remember'))
            current_app.logger.info('Login of %r from %s', name, request.remote_addr)
            return redirect(url_for('bookings.index'))
        record_login_failure(name)
        current_app.logger.warning('Failed login for %r from %s', name, request.remote_addr)
        return render_template('login.html', error='Benutzername oder Passwort falsch.', username=username)

    if g.user:
        return redirect(url_for('bookings.index'))
    return render_template('login.html')


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/passwort-vergessen', methods=['GET', 'POST'])
def forgot_password():
    if not mail.is_configured():
        abort(404)
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip().lower()
        if identifier:
            with get_db() as con:
                users = con.execute(
                    'SELECT * FROM users WHERE (username = ? OR lower(email) = ?) '
                    "AND email IS NOT NULL AND email != ''", (identifier, identifier)).fetchall()
                for user in users:
                    notifications.queue_password_reset(con, user)
        # Same answer whether or not the account exists, so nobody can probe for accounts
        flash('Falls zu diesem Konto eine E-Mail-Adresse hinterlegt ist, kommt in den nächsten Minuten '
              'ein Link. Er gilt 1 Stunde.', 'ok')
        return redirect(url_for('auth.login'))
    return render_template('forgot_password.html')


@bp.route('/passwort-zuruecksetzen/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user_id = notifications.read_reset_token(token)
    if user_id is None:
        return render_template('reset_password.html', invalid=True), 400
    if request.method == 'POST':
        try:
            set_password(user_id, request.form.get('password', ''))
        except UserError as e:
            flash(str(e), 'warn')
        else:
            session.clear()
            flash('Passwort geändert. Du kannst dich jetzt anmelden.', 'ok')
            return redirect(url_for('auth.login'))
    return render_template('reset_password.html', min_pw=MIN_PASSWORD_LENGTH)


def init_app(app):
    app.before_request(check_csrf)
    app.before_request(load_user)
    app.jinja_env.globals['csrf_token'] = csrf_token
