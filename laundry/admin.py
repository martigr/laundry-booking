"""Minimal admin area: users and the booking horizon."""
import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import admin_required, keep_session
from .db import get_db, get_setting, set_setting
from .notifications import queue_email_verification
from .users import MAX_NAME_LENGTH, MIN_PASSWORD_LENGTH, UserError, create_user, get_user, set_password

bp = Blueprint('admin', __name__, url_prefix='/admin')


def render_index(**extra):
    users = get_db().execute('SELECT * FROM users ORDER BY display_name COLLATE NOCASE').fetchall()
    return render_template('admin.html', users=users, horizon_weeks=get_setting('horizon_weeks'),
                           min_pw=MIN_PASSWORD_LENGTH, max_name=MAX_NAME_LENGTH, **extra)


@bp.route('')
@admin_required
def index():
    return render_index()


@bp.route('/users', methods=['POST'])
@admin_required
def create():
    try:
        uid = create_user(request.form.get('username'), request.form.get('password'),
                          request.form.get('display_name'), bool(request.form.get('is_admin')),
                          request.form.get('email'))
        with get_db() as con:
            queue_email_verification(con, get_user(uid))
        flash(f'Benutzer „{request.form.get("display_name") or request.form.get("username")}“ angelegt.', 'ok')
    except UserError as e:
        flash(str(e), 'warn')
    return redirect(url_for('admin.index'))


@bp.route('/users/<int:uid>/reset', methods=['POST'])
@admin_required
def reset_password(uid):
    user = get_user(uid) or abort(404)
    new_password = secrets.token_urlsafe(9)  # 12 characters
    set_password(uid, new_password)
    if uid == g.user['id']:
        keep_session()
    # Shown in this response only: a flash message would sit readable in the session cookie
    return render_index(new_password={'name': user['display_name'], 'password': new_password})


@bp.route('/users/<int:uid>/delete', methods=['POST'])
@admin_required
def delete(uid):
    if uid == g.user['id']:
        flash('Du kannst dich nicht selbst löschen.', 'warn')
        return redirect(url_for('admin.index'))
    user = get_user(uid) or abort(404)
    with get_db() as con:
        con.execute('DELETE FROM users WHERE id = ?', (uid,))  # bookings go via ON DELETE CASCADE
    flash(f'Benutzer „{user["display_name"]}“ gelöscht.', 'ok')
    return redirect(url_for('admin.index'))


@bp.route('/settings', methods=['POST'])
@admin_required
def settings():
    try:
        weeks = int(request.form.get('horizon_weeks', ''))
    except ValueError:
        weeks = 0
    if not 1 <= weeks <= 52:
        flash('Bitte eine Zahl zwischen 1 und 52 Wochen angeben.', 'warn')
    else:
        set_setting('horizon_weeks', weeks)
        flash('Einstellungen gespeichert.', 'ok')
    return redirect(url_for('admin.index'))
