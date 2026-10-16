"""Own display name and password."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from .auth import login_required
from .db import get_db
from .users import MAX_NAME_LENGTH, MIN_PASSWORD_LENGTH, UserError, set_password

bp = Blueprint('profile', __name__, url_prefix='/profile')


@bp.route('', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'GET':
        return render_template('profile.html', min_pw=MIN_PASSWORD_LENGTH, max_name=MAX_NAME_LENGTH)

    action = request.form.get('action')
    if action == 'name':
        name = request.form.get('display_name', '').strip()
        if not 1 <= len(name) <= MAX_NAME_LENGTH:
            flash(f'Der Anzeigename muss 1–{MAX_NAME_LENGTH} Zeichen lang sein.', 'warn')
        else:
            with get_db() as con:
                con.execute('UPDATE users SET display_name = ? WHERE id = ?', (name, g.user['id']))
            flash('Anzeigename gespeichert.', 'ok')
    elif action == 'password':
        if not check_password_hash(g.user['password'], request.form.get('old_password', '')):
            flash('Das aktuelle Passwort ist falsch.', 'warn')
        elif request.form.get('new_password', '') != request.form.get('new_password2', ''):
            flash('Die neuen Passwörter stimmen nicht überein.', 'warn')
        else:
            try:
                set_password(g.user['id'], request.form.get('new_password', ''))
                flash('Passwort geändert.', 'ok')
            except UserError as e:
                flash(str(e), 'warn')
    return redirect(url_for('profile.index'))
