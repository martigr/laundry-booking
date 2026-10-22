"""Minimal admin area: users, households and the booking horizon."""
import secrets
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import admin_required, keep_session
from .db import get_db, get_setting, set_setting
from .notifications import queue_email_verification
from .users import MAX_NAME_LENGTH, MIN_PASSWORD_LENGTH, UserError, create_user, get_user, set_password

bp = Blueprint('admin', __name__, url_prefix='/admin')


def render_index(**extra):
    users = get_db().execute('SELECT * FROM users ORDER BY display_name COLLATE NOCASE').fetchall()
    households = get_db().execute("""
        SELECT h.id, h.name, COUNT(u.id) AS members
        FROM households h LEFT JOIN users u ON u.household_id = h.id
        GROUP BY h.id ORDER BY h.name
    """).fetchall()
    return render_template('admin.html', users=users, households=households,
                           horizon_weeks=get_setting('horizon_weeks'),
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


@bp.route('/users/<int:uid>/household', methods=['POST'])
@admin_required
def set_household(uid):
    user = get_user(uid) or abort(404)
    household_id = request.form.get('household_id', '')
    if household_id:
        if not household_id.isdigit():
            abort(400)
        household = get_db().execute('SELECT * FROM households WHERE id = ?', (int(household_id),)).fetchone()
        if household is None:
            abort(400)
    else:
        household = None
    with get_db() as con:
        con.execute('UPDATE users SET household_id = ? WHERE id = ?', (household and household['id'], uid))
    flash(f'{user["display_name"]} gehört jetzt zum Haushalt „{household["name"]}“.' if household
          else f'{user["display_name"]} gehört keinem Haushalt mehr an.', 'ok')
    return redirect(url_for('admin.index'))


@bp.route('/households', methods=['POST'])
@admin_required
def create_household():
    name = request.form.get('name', '').strip()
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        flash(f'Der Name des Haushalts muss 1–{MAX_NAME_LENGTH} Zeichen lang sein.', 'warn')
        return redirect(url_for('admin.index'))
    try:
        with get_db() as con:
            con.execute('INSERT INTO households (name) VALUES (?)', (name,))
    except sqlite3.IntegrityError:
        flash(f'Den Haushalt „{name}“ gibt es schon.', 'warn')
    else:
        flash(f'Haushalt „{name}“ angelegt.', 'ok')
    return redirect(url_for('admin.index'))


@bp.route('/households/<int:hid>/delete', methods=['POST'])
@admin_required
def delete_household(hid):
    household = get_db().execute('SELECT * FROM households WHERE id = ?', (hid,)).fetchone() or abort(404)
    with get_db() as con:
        con.execute('DELETE FROM households WHERE id = ?', (hid,))  # members: household_id = NULL
    flash(f'Haushalt „{household["name"]}“ gelöscht. Die Benutzer und ihre Buchungen bleiben.', 'ok')
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
