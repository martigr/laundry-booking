"""Own display name, mail settings, calendar link and password; links from mails (confirm address, unsubscribe)."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from . import ical, mail
from .auth import keep_session, login_required
from .db import get_db
from .notifications import REMINDER_CHOICES, queue_email_verification, read_unsubscribe_token, read_verify_token
from .users import MAX_NAME_LENGTH, MIN_PASSWORD_LENGTH, UserError, clean_email, get_user, set_password

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
        token = g.user['calendar_token']
        return render_template('profile.html', min_pw=MIN_PASSWORD_LENGTH, max_name=MAX_NAME_LENGTH,
                               reminder_choices=REMINDER_CHOICES,
                               feed_url=ical.feed_url(token) if token else None,
                               webcal_url=ical.webcal_url(token) if token else None)

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
        changed = email != g.user['email']
        with get_db() as con:
            con.execute('UPDATE users SET email = ?, reminder_minutes = ?, notify_freed = ? WHERE id = ?',
                        (email, minutes, int(bool(request.form.get('notify_freed'))), g.user['id']))
            if changed:
                con.execute('UPDATE users SET email_verified = 0 WHERE id = ?', (g.user['id'],))
                queue_email_verification(con, get_user(g.user['id']))
        if changed and email and mail.is_configured():
            flash('Benachrichtigungen gespeichert. Wir haben dir eine Mail geschickt: Bitte bestätige '
                  'deine Adresse über den Link darin, erst dann bekommst du Mails.', 'ok')
        else:
            flash('Benachrichtigungen gespeichert.', 'ok')
    elif action == 'verify_email':
        if g.user['email'] and not g.user['email_verified']:
            with get_db() as con:
                queue_email_verification(con, g.user)
            flash(f'Bestätigungsmail an {g.user["email"]} verschickt.', 'ok')
    elif action == 'calendar':
        # Creates the link, or replaces it: the old URL stops working
        renewed = bool(g.user['calendar_token'])
        with get_db() as con:
            con.execute('UPDATE users SET calendar_token = ? WHERE id = ?', (ical.new_token(), g.user['id']))
        flash('Neuer Kalender-Link erstellt. Der alte funktioniert nicht mehr; abonniere den neuen.'
              if renewed else 'Kalender-Link erstellt.', 'ok')
    elif action == 'calendar_delete':
        with get_db() as con:
            con.execute('UPDATE users SET calendar_token = NULL WHERE id = ?', (g.user['id'],))
        flash('Kalender-Link gelöscht.', 'ok')
    elif action == 'password':
        if not check_password_hash(g.user['password'], request.form.get('old_password', '')):
            flash('Das aktuelle Passwort ist falsch.', 'warn')
        elif request.form.get('new_password', '') != request.form.get('new_password2', ''):
            flash('Die neuen Passwörter stimmen nicht überein.', 'warn')
        else:
            try:
                set_password(g.user['id'], request.form.get('new_password', ''))
                keep_session()
                flash('Passwort geändert. Auf anderen Geräten bist du jetzt abgemeldet.', 'ok')
            except UserError as e:
                flash(str(e), 'warn')
    return redirect(url_for('profile.index'))


@bp.route('/verify-email/<token>', methods=['GET', 'POST'])
def confirm_email(token):
    """Link in the verification mail. GET only shows a button, so a link scanner at a wrong
    recipient's mail provider can't confirm the address."""
    data = read_verify_token(token)
    user = get_user(data[0]) if data else None
    if user is None or user['email'] != data[1]:  # also when the address changed since
        return render_template('confirm_email.html', invalid=True), 400
    done = bool(user['email_verified'])
    if request.method == 'POST' and not done:
        with get_db() as con:
            con.execute('UPDATE users SET email_verified = 1 WHERE id = ? AND email = ?', (user['id'], data[1]))
        done = True
    return render_template('confirm_email.html', email=data[1], done=done)


@bp.route('/unsubscribe/<token>', methods=['GET', 'POST'])
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
