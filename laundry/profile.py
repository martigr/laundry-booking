"""Own display name, notification settings, push devices, calendar link and password; links from mails
(confirm address, unsubscribe)."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from . import audit, ical, mail, push, slots
from .auth import keep_session, login_required
from .db import get_db
from .notifications import REMINDER_CHOICES, queue_email_verification, read_unsubscribe_token, read_verify_token
from .users import MAX_NAME_LENGTH, MIN_PASSWORD_LENGTH, UserError, clean_email, get_user, set_password

bp = Blueprint('profile', __name__)

# Unsubscribe link kinds: (column to switch off, label for the confirmation page). Only the mail
# channel: push messages are switched off in the profile.
UNSUBSCRIBE = {
    'reminder': ('reminder_mail', 'Erinnerungen per Mail vor deinen Buchungen'),
    'freed': ('notify_freed', 'Mails, wenn ein Slot frei wird'),
}
MAX_PUSH_DEVICES = 10  # per user; subscribing another one drops the oldest


def channels(by_mail, by_push):
    """German summary for the audit log, e.g. 'Mail + Push'."""
    return ' + '.join(name for name, on in (('Mail', by_mail), ('Push', by_push)) if on) or 'aus'


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'GET':
        token = g.user['calendar_token']
        household = get_db().execute('SELECT name FROM households WHERE id = ?',
                                     (g.user['household_id'],)).fetchone()
        housemates = [r['display_name'] for r in get_db().execute(
            'SELECT display_name FROM users WHERE household_id = ? AND id != ? ORDER BY display_name COLLATE NOCASE',
            (g.user['household_id'], g.user['id']))]
        push_endpoints = [r['endpoint'] for r in get_db().execute(
            'SELECT endpoint FROM push_subscriptions WHERE user_id = ?', (g.user['id'],))]
        return render_template('profile.html', min_pw=MIN_PASSWORD_LENGTH, max_name=MAX_NAME_LENGTH,
                               reminder_choices=REMINDER_CHOICES, push_endpoints=push_endpoints,
                               push_key=push.public_key() if push.is_configured() else None,
                               household=household and household['name'], housemates=housemates,
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
                audit.record(con, 'display_name', f'{g.user["display_name"]} → {name}')
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
        reminder_mail = bool(request.form.get('reminder_mail'))
        notify_freed = bool(request.form.get('notify_freed'))
        if push.is_configured():
            reminder_push, freed_push = bool(request.form.get('reminder_push')), bool(request.form.get('freed_push'))
        else:  # the form has no push boxes then; keep the choice for when push comes back
            reminder_push, freed_push = bool(g.user['reminder_push']), bool(g.user['freed_push'])
        with get_db() as con:
            con.execute('UPDATE users SET email = ?, reminder_minutes = ?, reminder_mail = ?, reminder_push = ?, '
                        'notify_freed = ?, freed_push = ? WHERE id = ?',
                        (email, minutes, int(reminder_mail), int(reminder_push), int(notify_freed),
                         int(freed_push), g.user['id']))
            audit.record(con, 'notifications', f'Erinnerung: {dict(REMINDER_CHOICES)[minutes]}'
                         + (f' ({channels(reminder_mail, reminder_push)})' if minutes else '')
                         + f', freie Slots: {channels(notify_freed, freed_push)}'
                         + (f', neue Adresse: {email or "keine"}' if changed else ''))
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
            audit.record(con, 'calendar_link', 'ersetzt den alten' if renewed else '')
        flash('Neuer Kalender-Link erstellt. Der alte funktioniert nicht mehr; abonniere den neuen.'
              if renewed else 'Kalender-Link erstellt.', 'ok')
    elif action == 'calendar_delete':
        with get_db() as con:
            con.execute('UPDATE users SET calendar_token = NULL WHERE id = ?', (g.user['id'],))
            audit.record(con, 'calendar_link_deleted')
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
                with get_db() as con:
                    audit.record(con, 'password_changed')
                flash('Passwort geändert. Auf anderen Geräten bist du jetzt abgemeldet.', 'ok')
            except UserError as e:
                flash(str(e), 'warn')
    return redirect(url_for('profile.index'))


@bp.route('/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    """static/push.js sends the browser's subscription here (form fields, so the CSRF check applies).
    A device belongs to whoever subscribed it last."""
    if not push.is_configured():
        abort(404)
    endpoint, p256dh, auth = (request.form.get(k, '') for k in ('endpoint', 'p256dh', 'auth'))
    if not push.valid_subscription(endpoint, p256dh, auth):
        abort(400)
    with get_db() as con:
        # Also drops messages still queued for the device's previous owner
        con.execute('DELETE FROM push_subscriptions WHERE endpoint = ?', (endpoint,))
        con.execute('INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (g.user['id'], endpoint, p256dh, auth, slots.now().isoformat(timespec='seconds')))
        con.execute('DELETE FROM push_subscriptions WHERE user_id = ? AND id NOT IN '
                    '(SELECT id FROM push_subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT ?)',
                    (g.user['id'], g.user['id'], MAX_PUSH_DEVICES))
        audit.record(con, 'push_enabled', push.service_host(endpoint))
    return '', 204


@bp.route('/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    """The push button switched this device off. Works without push config too, to clean up."""
    endpoint = request.form.get('endpoint', '')
    if not endpoint:
        abort(400)
    with get_db() as con:
        if con.execute('DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?',
                       (g.user['id'], endpoint)).rowcount:
            audit.record(con, 'push_disabled', push.service_host(endpoint))
    return '', 204


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
            audit.record(con, 'email_confirmed', data[1], user=user)
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
            if user := get_user(user_id):
                audit.record(con, 'unsubscribe', label, user=user)
        done = True
    return render_template('unsubscribe.html', label=label, done=done)
