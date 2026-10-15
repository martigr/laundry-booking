"""Login, session handling, CSRF protection and access decorators."""
import functools
import secrets

from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .users import get_user, get_user_by_username

bp = Blueprint('auth', __name__)


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
    if user_id and g.user is None:
        session.clear()  # user was deleted


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


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        user = get_user_by_username(username)
        if user and check_password_hash(user['password'], request.form.get('password', '')):
            session.clear()  # new session id content, against session fixation
            session['user_id'] = user['id']
            session.permanent = bool(request.form.get('remember'))
            return redirect(url_for('bookings.index'))
        return render_template('login.html', error='Benutzername oder Passwort falsch.', username=username)

    if g.user:
        return redirect(url_for('bookings.index'))
    return render_template('login.html')


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


def init_app(app):
    app.before_request(check_csrf)
    app.before_request(load_user)
    app.jinja_env.globals['csrf_token'] = csrf_token
