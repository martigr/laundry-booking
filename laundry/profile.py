"""Own display name and password."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from .auth import login_required
from .db import get_db
from .notifications import REMINDER_CHOICES, read_unsubscribe_token
from .users import MAX_NAME_LENGTH, MIN_PASSWORD_LENGTH, UserError, clean_email, set_password

bp = Blueprint('profile', __name__)

# Unsubscribe link kinds: (column to switch off, label for the confirmation page)
UNSUBSCRIBE = {
    'reminder': ('reminder_minutes', 'Erinnerungen vor deinen Buchungen'),
    'freed': ('notify_freed', 'Mails, wenn ein Slot frei wird'),
}


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'GET':
        return render_template('profile.html', min_pw=MIN_PASSWORD_LENGTH, max_name=MAX_NAME_LENGTH,
                               reminder_choices=REMINDER_CHOICES)

    action = request.form.get('action')
    if action == 'name':
        name = request.form.get('display_name', '').strip()
        if not 1 <= len(name) <= MAX_NAME_LENGTH:
            flash(f'Der Anzeigename muss 1–{MAX_NAME_LENGTH} Zeichen lang sein.', 'warn')
        else:
            with get_db() as con:
                con.execute('UPDATE users SET display_name = ? WHERE id = ?', (name, g.user['id']))
            flash('Anzeigename gespeichert.', 'ok')
    elif action == 'notifications':
        try:
            email = clean_email(request.form.get('email'))
            minutes = int(request.form.get('reminder_minutes', 0))
        except UserError as e:
            flash(str(e), 'warn')
            return redirect(url_for('profile.index'))
        except ValueError:
            abort(400)
        if minutes not in dict(REMINDER_CHOICES):
            abort(400)
        with get_db() as con:
            con.execute('UPDATE users SET email = ?, reminder_minutes = ?, notify_freed = ? WHERE id = ?',
                        (email, minutes, int(bool(request.form.get('notify_freed'))), g.user['id']))
        flash('Benachrichtigungen gespeichert.', 'ok')
    elif action == 'password':
        if not check_password_hash(g.user['password'], request.form.get('old_password', '')):
            flash('Das aktuelle Passwort ist falsch.', 'warn')
        else:
            try:
                set_password(g.user['id'], request.form.get('new_password', ''))
                flash('Passwort geändert.', 'ok')
            except UserError as e:
                flash(str(e), 'warn')
    return redirect(url_for('profile.index'))


@bp.route('/abbestellen/<token>', methods=['GET', 'POST'])
def unsubscribe(token):
    """Link in every notification mail. GET only shows a button, so link scanners change nothing."""
    data = read_unsubscribe_token(token)
    if data is None or data[1] not in UNSUBSCRIBE:
        abort(404)
    user_id, kind = data
    column, label = UNSUBSCRIBE[kind]
    done = False
    if request.method == 'POST':
        with get_db() as con:
            con.execute(f'UPDATE users SET {column} = 0 WHERE id = ?', (user_id,))
        done = True
    return render_template('unsubscribe.html', label=label, done=done)
