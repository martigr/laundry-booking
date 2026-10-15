import os
import logging
import secrets
import sqlite3
import functools
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import click
from flask import Flask, render_template, request, redirect, abort, session, flash, g, url_for
from werkzeug.security import generate_password_hash, check_password_hash

# Basic logging
logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO').upper())

app = Flask(__name__)

# Configuration from environment
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or os.environ.get('FLASK_SECRET') or 'please-change-this-secret'
if app.config['SECRET_KEY'] == 'please-change-this-secret':
    logging.warning("Using default SECRET_KEY — set SECRET_KEY env var in production")

# SQLite DB path (can be set via env var)
app.config['DATABASE'] = os.environ.get('DATABASE') or os.path.join(os.getcwd(), 'laundry.db')

# Local time zone of the building; decides what "today" and "now" mean
TZ = ZoneInfo(os.environ.get('APP_TIMEZONE', 'Europe/Zurich'))

# Session cookie security settings (toggle via env vars)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# Bookable slots: (name, start, end)
SLOTS = [
    ("Vormittag", time(7, 0), time(12, 0)),
    ("Nachmittag", time(12, 0), time(17, 0)),
    ("Abend", time(17, 0), time(22, 0)),
]
SLOT_NAMES = [s[0] for s in SLOTS]

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WEEKDAYS_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]

DEFAULT_SETTINGS = {'horizon_weeks': '4'}
MIN_PASSWORD_LENGTH = 6


# --- Database ----------------------------------------------------------------

def db():
    # Use a per-call connection with WAL to improve concurrency under WSGI servers
    con = sqlite3.connect(app.config['DATABASE'], timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA journal_mode=WAL;')
    except Exception:
        logging.exception('Could not set WAL mode on SQLite')
    return con


def init_db():
    con = db()
    con.isolation_level = None  # explicit transaction below
    try:
        # IMMEDIATE takes the write lock, so parallel Gunicorn workers run this one after another
        con.execute("BEGIN IMMEDIATE")
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                slot TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id),
                UNIQUE (day, slot)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Bootstrap the first admin from env vars on an empty database
        admin_user = os.environ.get('ADMIN_USERNAME')
        admin_pw = os.environ.get('ADMIN_PASSWORD')
        user_count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0 and admin_user and admin_pw:
            con.execute(
                "INSERT INTO users (username, password, display_name, is_admin) VALUES (?, ?, ?, 1)",
                (admin_user, generate_password_hash(admin_pw), admin_user))
            logging.info('Created admin user %s from ADMIN_USERNAME', admin_user)
        elif user_count and not con.execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone():
            logging.warning("No admin user yet — run: flask --app app make-admin <username>")
        con.execute("COMMIT")
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def get_setting(key):
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else DEFAULT_SETTINGS[key]


def set_setting(key, value):
    with db() as con:
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


# --- Time helpers ---------------------------------------------------------------

def now():
    return datetime.now(TZ).replace(tzinfo=None)


def slot_bounds(day, slot_name):
    _, start, end = SLOTS[SLOT_NAMES.index(slot_name)]
    return datetime.combine(day, start), datetime.combine(day, end)


def parse_day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def fmt_day(d):
    return f"{WEEKDAYS[d.weekday()]} {d.day}. {MONTHS[d.month - 1][:3]}."


def last_bookable_day(today):
    return today + timedelta(weeks=int(get_setting('horizon_weeks')))


@app.template_filter('slot_time')
def slot_time(slot_name):
    _, start, end = SLOTS[SLOT_NAMES.index(slot_name)]
    return f"{start:%H:%M} – {end:%H:%M}"


# --- Auth & CSRF ------------------------------------------------------------------

def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


app.jinja_env.globals['csrf_token'] = csrf_token


@app.before_request
def check_csrf():
    if request.method == 'POST':
        token = request.form.get('csrf_token', '')
        if not token or not secrets.compare_digest(token, session.get('csrf', '')):
            abort(400, 'Ungültiges Formular-Token – bitte Seite neu laden.')


@app.before_request
def load_user():
    g.user = None
    user_id = session.get('user_id')
    if user_id:
        with db() as con:
            g.user = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if g.user is None:
            session.clear()


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('login'))
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


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        if user and check_password_hash(user['password'], password):
            session.clear()
            session['user_id'] = user['id']
            session.permanent = bool(request.form.get('remember'))
            return redirect(url_for('index'))
        return render_template('login.html', error='Benutzername oder Passwort falsch.', username=username)

    return render_template('login.html')


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))


# --- Calendar -----------------------------------------------------------------------

def load_reservations(start, end):
    with db() as con:
        rows = con.execute("""
            SELECT r.id, r.day, r.slot, r.user_id, u.display_name
            FROM reservations r JOIN users u ON u.id = r.user_id
            WHERE r.day BETWEEN ? AND ?
        """, (start.isoformat(), end.isoformat())).fetchall()
    return {(r['day'], r['slot']): r for r in rows}


def build_cell(day, slot_name, res, current, last_day):
    start, end = slot_bounds(day, slot_name)
    cell = {
        'day': day.isoformat(),
        'slot': slot_name,
        'time': slot_time(slot_name),
        'short': f"{start.hour}–{end.hour} Uhr",
        'past': end <= current,
        'running': start <= current < end,
        'res': res,
        'mine': bool(res) and res['user_id'] == g.user['id'],
    }
    cell['bookable'] = not res and not cell['past'] and day <= last_day
    cell['cancelable'] = bool(res) and not cell['past'] and (cell['mine'] or g.user['is_admin'])
    if res:
        cell['state'] = 'mine' if cell['mine'] else 'taken'
    elif cell['past']:
        cell['state'] = 'past'
    elif day > last_day:
        cell['state'] = 'locked'
    else:
        cell['state'] = 'free'
    return cell


def sidebar_info(current):
    """Who is in the laundry room right now, the next free slot and my upcoming bookings."""
    today = current.date()
    last_day = last_bookable_day(today)
    res = load_reservations(today, last_day)

    running = None
    next_free = None
    d = today
    while d <= last_day and next_free is None:
        for name in SLOT_NAMES:
            start, end = slot_bounds(d, name)
            if end <= current:
                continue
            r = res.get((d.isoformat(), name))
            if start <= current and r:
                running = {'res': r, 'end': end, 'mine': r['user_id'] == g.user['id'],
                           'progress': int((current - start) / (end - start) * 100)}
            if r is None:
                next_free = {'day': d, 'slot': name, 'label': fmt_day(d) if d != today else 'heute',
                             'week': d.isoformat()}
                break
        d += timedelta(days=1)

    with db() as con:
        upcoming = con.execute("""
            SELECT id, day, slot FROM reservations
            WHERE user_id = ? AND day >= ? ORDER BY day
        """, (g.user['id'], today.isoformat())).fetchall()
    upcoming = sorted(
        ({'id': r['id'], 'day': parse_day(r['day']), 'slot': r['slot']} for r in upcoming
         if slot_bounds(parse_day(r['day']), r['slot'])[1] > current),
        key=lambda u: (u['day'], SLOT_NAMES.index(u['slot'])))
    for u in upcoming:
        u['wd'] = WEEKDAYS[u['day'].weekday()]
        u['label'] = 'heute' if u['day'] == today else fmt_day(u['day'])

    return {'running': running, 'next_free': next_free, 'upcoming': upcoming[:6]}


@app.route("/")
@login_required
def index():
    current = now()
    today = current.date()
    ref = parse_day(request.args.get('week')) or today
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)
    last_day = last_bookable_day(today)

    res = load_reservations(monday, sunday)
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        days.append({
            'date': d,
            'wd': WEEKDAYS[i],
            'wd_long': WEEKDAYS_LONG[i],
            'is_today': d == today,
            'is_past': d < today,
            'cells': [build_cell(d, name, res.get((d.isoformat(), name)), current, last_day)
                      for name in SLOT_NAMES],
        })

    if monday.month == sunday.month:
        title = f"{monday.day}. – {sunday.day}. {MONTHS[monday.month - 1]}"
    else:
        title = f"{monday.day}. {MONTHS[monday.month - 1]} – {sunday.day}. {MONTHS[sunday.month - 1]}"

    return render_template(
        "index.html",
        days=days,
        title=title,
        kicker=f"KW {monday.isocalendar().week} · {sunday.year}",
        prev_week=(monday - timedelta(days=7)).isoformat(),
        next_week=(monday + timedelta(days=7)).isoformat(),
        this_week=monday.isoformat(),
        month_param=monday.strftime('%Y-%m'),
        **sidebar_info(current),
    )


@app.route("/month")
@login_required
def month():
    current = now()
    today = current.date()
    try:
        y, m = map(int, request.args.get('month', '').split('-'))
        first = date(y, m, 1)
    except ValueError:
        first = today.replace(day=1)
    last = first.replace(day=monthrange(first.year, first.month)[1])
    last_day = last_bookable_day(today)

    res = load_reservations(first, last)
    days = []
    for n in range(1, last.day + 1):
        d = first.replace(day=n)
        days.append({
            'date': d,
            'is_today': d == today,
            'is_past': d < today,
            'cells': [build_cell(d, name, res.get((d.isoformat(), name)), current, last_day)
                      for name in SLOT_NAMES],
        })

    return render_template(
        "month.html",
        days=days,
        leading=first.weekday(),
        title=f"{MONTHS[first.month - 1]} {first.year}",
        prev_month=(first - timedelta(days=1)).strftime('%Y-%m'),
        next_month=(last + timedelta(days=1)).strftime('%Y-%m'),
        this_month=today.strftime('%Y-%m'),
        week_param=first.isoformat() if first > today or last < today else today.isoformat(),
    )


def back_to_calendar():
    target = request.form.get('next', '')
    # Only allow local redirects
    if target.startswith('/') and not target.startswith('//'):
        return redirect(target)
    return redirect(url_for('index'))


@app.route("/book", methods=["POST"])
@login_required
def book():
    day = parse_day(request.form.get('day'))
    slot = request.form.get('slot')
    if day is None or slot not in SLOT_NAMES:
        abort(400)

    current = now()
    label = f"{fmt_day(day)} · {slot}"
    if slot_bounds(day, slot)[1] <= current:
        flash(f"{label} liegt in der Vergangenheit.", 'warn')
    elif day > last_bookable_day(current.date()):
        flash(f"{label} kann noch nicht gebucht werden.", 'warn')
    else:
        try:
            with db() as con:
                con.execute("INSERT INTO reservations (day, slot, user_id) VALUES (?, ?, ?)",
                            (day.isoformat(), slot, g.user['id']))
            flash(f"Gebucht: {label}", 'ok')
        except sqlite3.IntegrityError:
            flash(f"{label} ist leider schon vergeben.", 'warn')
    return back_to_calendar()


@app.route("/cancel/<int:rid>", methods=["POST"])
@login_required
def cancel(rid):
    with db() as con:
        r = con.execute("SELECT * FROM reservations WHERE id = ?", (rid,)).fetchone()
        if r is None:
            abort(404)
        if r['user_id'] != g.user['id'] and not g.user['is_admin']:
            abort(403)
        day = parse_day(r['day'])
        if slot_bounds(day, r['slot'])[1] <= now():
            flash("Vergangene Buchungen können nicht storniert werden.", 'warn')
        else:
            con.execute("DELETE FROM reservations WHERE id = ?", (rid,))
            flash(f"Storniert: {fmt_day(day)} · {r['slot']}", 'ok')
    return back_to_calendar()


# --- Profile ------------------------------------------------------------------------

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        action = request.form.get('action')
        if action == 'name':
            name = request.form.get('display_name', '').strip()
            if not name or len(name) > 40:
                flash('Der Anzeigename muss 1–40 Zeichen lang sein.', 'warn')
            else:
                with db() as con:
                    con.execute("UPDATE users SET display_name = ? WHERE id = ?", (name, g.user['id']))
                flash('Anzeigename gespeichert.', 'ok')
        elif action == 'password':
            old_password = request.form.get('old_password', '')
            new_password = request.form.get('new_password', '')
            if not check_password_hash(g.user['password'], old_password):
                flash('Das aktuelle Passwort ist falsch.', 'warn')
            elif len(new_password) < MIN_PASSWORD_LENGTH:
                flash(f'Das neue Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.', 'warn')
            else:
                with db() as con:
                    con.execute("UPDATE users SET password = ? WHERE id = ?",
                                (generate_password_hash(new_password), g.user['id']))
                flash('Passwort geändert.', 'ok')
        return redirect(url_for('profile'))

    return render_template('profile.html', min_pw=MIN_PASSWORD_LENGTH)


# --- Admin --------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin():
    with db() as con:
        users = con.execute("SELECT * FROM users ORDER BY display_name COLLATE NOCASE").fetchall()
    return render_template('admin.html', users=users, horizon_weeks=get_setting('horizon_weeks'),
                           min_pw=MIN_PASSWORD_LENGTH)


@app.route("/admin/users", methods=["POST"])
@admin_required
def admin_create_user():
    username = request.form.get('username', '').strip().lower()
    display_name = request.form.get('display_name', '').strip() or username
    password = request.form.get('password', '')
    is_admin = 1 if request.form.get('is_admin') else 0

    if not username or len(username) > 40 or len(display_name) > 40:
        flash('Benutzername und Anzeigename müssen 1–40 Zeichen lang sein.', 'warn')
    elif len(password) < MIN_PASSWORD_LENGTH:
        flash(f'Das Startpasswort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.', 'warn')
    else:
        try:
            with db() as con:
                con.execute(
                    "INSERT INTO users (username, password, display_name, is_admin) VALUES (?, ?, ?, ?)",
                    (username, generate_password_hash(password), display_name, is_admin))
            flash(f'Benutzer „{display_name}“ angelegt.', 'ok')
        except sqlite3.IntegrityError:
            flash(f'Der Benutzername „{username}“ ist schon vergeben.', 'warn')
    return redirect(url_for('admin'))


@app.route("/admin/users/<int:uid>/reset", methods=["POST"])
@admin_required
def admin_reset_password(uid):
    new_password = secrets.token_urlsafe(6)
    with db() as con:
        user = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if user is None:
            abort(404)
        con.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new_password), uid))
    flash(f'Neues Passwort für {user["display_name"]}: {new_password}', 'ok')
    return redirect(url_for('admin'))


@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@admin_required
def admin_delete_user(uid):
    if uid == g.user['id']:
        flash('Du kannst dich nicht selbst löschen.', 'warn')
        return redirect(url_for('admin'))
    with db() as con:
        user = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if user is None:
            abort(404)
        con.execute("DELETE FROM reservations WHERE user_id = ?", (uid,))
        con.execute("DELETE FROM users WHERE id = ?", (uid,))
    flash(f'Benutzer „{user["display_name"]}“ gelöscht.', 'ok')
    return redirect(url_for('admin'))


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    try:
        weeks = int(request.form.get('horizon_weeks', ''))
    except ValueError:
        weeks = 0
    if not 1 <= weeks <= 52:
        flash('Bitte eine Zahl zwischen 1 und 52 Wochen angeben.', 'warn')
    else:
        set_setting('horizon_weeks', weeks)
        flash('Einstellungen gespeichert.', 'ok')
    return redirect(url_for('admin'))


# --- CLI ----------------------------------------------------------------------------

@app.cli.command('create-user')
@click.argument('username')
@click.option('--name', 'display_name', help='Anzeigename (Standard: Benutzername)')
@click.option('--admin', is_flag=True, help='Benutzer erhält Admin-Rechte')
@click.password_option()
def create_user_command(username, display_name, admin, password):
    """Legt einen Benutzer an."""
    with db() as con:
        con.execute("INSERT INTO users (username, password, display_name, is_admin) VALUES (?, ?, ?, ?)",
                    (username, generate_password_hash(password), display_name or username, int(admin)))
    click.echo(f"Benutzer {username} angelegt.")


@app.cli.command('make-admin')
@click.argument('username')
def make_admin_command(username):
    """Gibt einem bestehenden Benutzer Admin-Rechte."""
    with db() as con:
        cur = con.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (username,))
    click.echo(f"{username} ist jetzt Admin." if cur.rowcount else f"Benutzer {username} nicht gefunden.")


# Health endpoint for load balancers / container probes
@app.route('/health')
def health():
    return 'ok', 200


# Ensure the DB schema exists (also when started via Gunicorn)
init_db()

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))

    # For production, run this app with a WSGI server such as Gunicorn on Linux.
    logging.info('Starting development server on 0.0.0.0:%s (use Gunicorn for production)', port)
    app.run(host='0.0.0.0', port=port)
