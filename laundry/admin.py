"""Minimal admin area: users, households, settings and the audit log."""
import secrets
import sqlite3
from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import audit
from .auth import admin_required, keep_session
from .db import get_db, get_setting, set_setting
from .notifications import queue_email_verification
from .users import MAX_NAME_LENGTH, MIN_PASSWORD_LENGTH, UserError, create_user, get_user, set_password

bp = Blueprint('admin', __name__, url_prefix='/admin')

# Settings form: key -> (min, max, label for messages and the audit log, unit)
SETTINGS = {
    'horizon_weeks': (1, 52, 'Buchbar im Voraus', 'Wochen'),
    'audit_days': (1, 3650, 'Audit-Log aufbewahren', 'Tage'),
}
AUDIT_PAGE_SIZE = 50


def render_index(**extra):
    users = get_db().execute('SELECT * FROM users ORDER BY display_name COLLATE NOCASE').fetchall()
    households = get_db().execute("""
        SELECT h.id, h.name, COUNT(u.id) AS members
        FROM households h LEFT JOIN users u ON u.household_id = h.id
        GROUP BY h.id ORDER BY h.name
    """).fetchall()
    return render_template('admin.html', users=users, households=households,
                           horizon_weeks=get_setting('horizon_weeks'), audit_days=get_setting('audit_days'),
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
        user = get_user(uid)
        with get_db() as con:
            queue_email_verification(con, user)
            audit.record(con, 'user_created', user['username'] + (' (Admin)' if user['is_admin'] else ''))
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
    with get_db() as con:
        audit.record(con, 'user_password_reset', user['username'])
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
        audit.record(con, 'user_deleted', user['username'])
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
        audit.record(con, 'household_assigned', f'{user["username"]} → {household["name"] if household else "keiner"}')
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
            audit.record(con, 'household_created', name)
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
        audit.record(con, 'household_deleted', household['name'])
    flash(f'Haushalt „{household["name"]}“ gelöscht. Die Benutzer und ihre Buchungen bleiben.', 'ok')
    return redirect(url_for('admin.index'))


@bp.route('/settings', methods=['POST'])
@admin_required
def settings():
    """Save the settings in the form; a field that is missing stays as it is."""
    values = {}
    for key, (low, high, label, unit) in SETTINGS.items():
        if key not in request.form:
            continue
        try:
            value = int(request.form[key])
        except ValueError:
            value = None
        if value is None or not low <= value <= high:
            flash(f'{label}: bitte eine Zahl zwischen {low} und {high} {unit} angeben.', 'warn')
            return redirect(url_for('admin.index'))
        values[key] = value
    changes = [f'{SETTINGS[k][2]}: {get_setting(k)} → {v} {SETTINGS[k][3]}'
               for k, v in values.items() if str(v) != get_setting(k)]
    for key, value in values.items():
        set_setting(key, value)
    if changes:
        with get_db() as con:
            audit.record(con, 'settings', '; '.join(changes))
    flash('Einstellungen gespeichert.', 'ok')
    return redirect(url_for('admin.index'))


@bp.route('/audit')
@admin_required
def audit_log():
    kind = request.args.get('kind', '')
    if kind and kind not in audit.KINDS:
        abort(400)
    page = request.args.get('page', 1, type=int)
    if page < 1:
        abort(400)
    actions = [a for a, (_, k) in audit.ACTIONS.items() if k == kind] if kind else list(audit.ACTIONS)
    where = f'WHERE a.action IN ({", ".join("?" * len(actions))})'
    total = get_db().execute(f'SELECT COUNT(*) FROM audit_log a {where}', actions).fetchone()[0]
    rows = get_db().execute(f"""
        SELECT a.*, u.display_name FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
        {where} ORDER BY a.id DESC LIMIT ? OFFSET ?
    """, (*actions, AUDIT_PAGE_SIZE, (page - 1) * AUDIT_PAGE_SIZE)).fetchall()
    entries = []
    for r in rows:
        at = datetime.fromisoformat(r['at'])
        label, entry_kind = audit.ACTIONS.get(r['action'], (r['action'], ''))
        entries.append({'at': f'{at:%d.%m.%Y %H:%M:%S}', 'label': label, 'kind': entry_kind,
                        'failed': r['action'] in ('login_failed', 'login_blocked'),
                        'who': r['display_name'], 'username': r['username'],
                        'deleted': r['user_id'] is not None and r['display_name'] is None,
                        'detail': r['detail'], 'ip': r['ip']})
    return render_template('audit.html', entries=entries, kinds=audit.KINDS, kind=kind, page=page,
                           pages=max(1, -(-total // AUDIT_PAGE_SIZE)), total=total,
                           audit_days=get_setting('audit_days'))
