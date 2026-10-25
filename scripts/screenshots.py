"""Retake the screenshots of the README and project page (docs/screenshots/) and of the help page
(laundry/static/help/).

Each set starts the app on its own temporary database with a fixed clock and fixed data, so the
pictures don't depend on the day they are taken. Needs Playwright with Chromium, which is not in
requirements-dev.txt because only this script uses it:

    pip install playwright && playwright install chromium
    python scripts/screenshots.py            # both sets; or: readme, help

To use an already installed Chromium instead, set CHROMIUM=/path/to/chrome.

The script prints the sizes. Check the width/height attributes in templates/help.html (2x images:
half) and of week.png in site/index.html afterwards.
"""
import argparse
import io
import json
import os
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from laundry import create_app, push, slots  # noqa: E402
from laundry.cli import seed_demo_command  # noqa: E402
from laundry.db import get_db  # noqa: E402
from laundry.users import create_user  # noqa: E402

PORT = 5077
BASE = f'http://127.0.0.1:{PORT}'
HIGHLIGHT = '.hl { outline: 3px solid #FB923C !important; outline-offset: 3px; }'
PASSWORD = 'screenshots'
PUSH_ENDPOINT = 'https://fcm.googleapis.com/fcm/send/screenshots'

# Headless Chromium has no push service: stand in for it, so the profile shows the push button.
# The subscription "exists" when the server knows PUSH_ENDPOINT for the user (data-endpoints).
FAKE_PUSH = """
PushManager.prototype.getSubscription = async function () {
  const box = document.getElementById('push-device');
  const known = box ? JSON.parse(box.dataset.endpoints) : [];
  return known.includes(ENDPOINT) ? {endpoint: ENDPOINT} : null;
};
""".replace('ENDPOINT', json.dumps(PUSH_ENDPOINT))


def save(png, path):
    """Store a screenshot with a reduced palette (a fraction of the size, looks the same)."""
    img = Image.open(io.BytesIO(png)).convert('RGB')
    img.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(path, optimize=True)
    print(f'{path.relative_to(ROOT)}  {img.width}x{img.height}px')


def union(*boxes, pad=0):
    x0 = min(b['x'] for b in boxes) - pad
    y0 = min(b['y'] for b in boxes) - pad
    x1 = max(b['x'] + b['width'] for b in boxes) + pad
    y1 = max(b['y'] + b['height'] for b in boxes) + pad
    return {'x': x0, 'y': y0, 'width': x1 - x0, 'height': y1 - y0}


def mark(page, selector):
    page.locator(selector).evaluate_all("els => els.forEach(e => e.classList.add('hl'))")


def unmark(page):
    page.evaluate("document.querySelectorAll('.hl').forEach(e => e.classList.remove('hl'))")


def login(page, username, password=PASSWORD):
    page.goto(BASE + '/login')
    page.fill('#username', username)
    page.fill('#password', password)
    page.click('button[type=submit]')
    page.mouse.move(0, 0)  # no hover effect on what was under the login button


def push_page(browser, username, password=PASSWORD, **context):
    """A logged-in page whose browser may show notifications and pretends to have a push service."""
    ctx = browser.new_context(permissions=['notifications'], **context)
    ctx.add_init_script(FAKE_PUSH)
    page = ctx.new_page()
    login(page, username, password)
    return page


def subscribe_device(con, user_id):
    p256dh = 'B' + 'A' * 86  # never used: nothing is sent
    con.execute('INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) '
                "VALUES (?, ?, ?, 'AAAAAAAAAAAAAAAAAAAAAA', '2026-10-01T12:00:00')", (user_id, PUSH_ENDPOINT, p256dh))


def notifications_card(page):
    page.goto(BASE + '/profile')
    page.wait_for_selector('#push-toggle:not([hidden])')
    return page.locator('form.card', has=page.locator('#push-device'))


def run(prepare, shoot):
    """Serve a fresh app with the data from prepare(app) and call shoot(browser, app)."""
    with tempfile.TemporaryDirectory() as tmp:
        # Mail and push on, so the profile and the help page show both (nothing is sent)
        app = create_app({'SECRET_KEY': 'screenshots', 'DATABASE': f'{tmp}/demo.db', 'SMTP_HOST': 'smtp.invalid',
                          'MAIL_FROM': 'waschkueche@example.ch', 'VAPID_PRIVATE_KEY': push.new_private_key()})
        with app.app_context():
            prepare(app)
        server = make_server('127.0.0.1', PORT, app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM'))
                shoot(browser, app)
                browser.close()
        finally:
            server.shutdown()


# --- README and project page ------------------------------------------------------------------

README_DIR = ROOT / 'docs' / 'screenshots'
README_NOW = datetime(2026, 10, 15, 14, 20)  # Thursday afternoon: today and a running slot
AUDIT_NOW = datetime(2026, 10, 22, 14, 0)    # a week later, after the entries of the audit log

README_USERS = [  # username, display name, admin, email, household
    ('alice', 'Alice', True, 'alice@example.ch', None),
    ('bob', 'Bob', False, None, 'Müller 2. OG'),
    ('marco', 'Marco', False, 'marco@example.ch', 'Keller 1. OG'),
    ('sandra', 'Sandra', False, None, 'Müller 2. OG'),
    ('lea', 'Lea', False, None, None),
]
README_BOOKINGS = {  # day of October 2026: (Vormittag, Nachmittag, Abend)
    12: ('alice', None, 'marco'), 13: (None, 'bob', None), 14: ('lea', None, 'sandra'),
    15: ('marco', 'sandra', None), 16: ('bob', None, 'sandra'), 17: ('bob', 'bob', 'alice'),
    18: (None, 'marco', None), 24: ('alice', 'lea', None), 28: (None, None, 'marco'),
    31: ('bob', None, None),
}
AUDIT_ENTRIES = [  # oldest first: time, username, action, detail, address
    ('2026-10-21T07:42:10', 'sandra', 'login', '', '192.168.1.23'),
    ('2026-10-21T07:43:02', 'sandra', 'book', 'Sa 24. Okt. · Vormittag', '192.168.1.23'),
    ('2026-10-21T12:14:05', 'marco', 'push_enabled', 'fcm.googleapis.com', '192.168.1.40'),
    ('2026-10-21T12:15:47', 'marco', 'notifications', 'Erinnerung: 1 Stunde vorher (Mail + Push), freie Slots: Push',
     '192.168.1.40'),
    ('2026-10-21T18:05:33', None, 'login_failed', 'falsches Passwort', '192.168.1.17'),
    ('2026-10-21T18:05:51', 'bob', 'login', 'angemeldet bleiben', '192.168.1.17'),
    ('2026-10-21T18:06:20', 'bob', 'book', 'Do 22. Okt. · ganzer Tag', '192.168.1.17'),
    ('2026-10-22T08:10:04', 'alice', 'login', '', '192.168.1.10'),
    ('2026-10-22T08:11:30', 'alice', 'household_assigned', 'marco → Keller 1. OG', '192.168.1.10'),
    ('2026-10-22T08:12:02', 'alice', 'settings', 'Buchbar im Voraus: 6 → 4 Wochen', '192.168.1.10'),
    ('2026-10-22T09:30:12', 'sandra', 'cancel', 'Do 22. Okt. · Abend (Buchung von Bob)', '192.168.1.23'),
    ('2026-10-22T11:02:44', None, 'login_failed', 'unbekannter Benutzername', '203.0.113.7'),
    ('2026-10-22T11:02:59', None, 'login_failed', 'unbekannter Benutzername', '203.0.113.7'),
    ('2026-10-22T13:47:18', 'marco', 'book', 'Fr 23. Okt. · Nachmittag – Abend', '192.168.1.40'),
]
README_NOTES = {  # (day of October 2026, slot): note
    (16, 'Abend'): '60°-Wäsche, Maschine danach frei',
    (17, 'Vormittag'): 'Ich hole die Wäsche erst um 13 Uhr',
}
FAILED_LOGIN_NAMES = iter(['bob', 'admin', 'root'])  # what was typed at the failed logins above


def readme_data(app):
    slots.now = lambda: README_NOW
    ids = {}
    for username, name, is_admin, email, _ in README_USERS:
        ids[username] = create_user(username, PASSWORD, name, is_admin, email)
    with get_db() as con:
        for household in sorted({u[4] for u in README_USERS if u[4]}):
            con.execute('INSERT INTO households (name) VALUES (?)', (household,))
        for username, _, _, email, household in README_USERS:
            con.execute('UPDATE users SET email_verified = ?, household_id = '
                        '(SELECT id FROM households WHERE name = ?) WHERE id = ?',
                        (int(bool(email)), household, ids[username]))
        for day, owners in README_BOOKINGS.items():
            for slot, owner in zip(slots.SLOT_NAMES, owners, strict=True):
                if owner:
                    con.execute('INSERT INTO reservations (day, slot, user_id, note) VALUES (?, ?, ?, ?)',
                                (date(2026, 10, day).isoformat(), slot, ids[owner], README_NOTES.get((day, slot))))
        # Marco: reminders by mail and push, freed slots by push, on this "phone"
        con.execute('UPDATE users SET reminder_minutes = 60, reminder_mail = 1, reminder_push = 1, freed_push = 1 '
                    'WHERE id = ?', (ids['marco'],))
        subscribe_device(con, ids['marco'])
    readme_data.ids = ids


def readme_audit(app):
    """Replace the entries the logins of the screenshot run wrote with a realistic week."""
    ids = readme_data.ids
    with app.app_context(), get_db() as con:
        con.execute('DELETE FROM audit_log')
        for at, username, action, detail, ip in AUDIT_ENTRIES:
            typed = username or next(FAILED_LOGIN_NAMES)
            con.execute('INSERT INTO audit_log (at, user_id, username, action, detail, ip) VALUES (?, ?, ?, ?, ?, ?)',
                        (at, ids.get(username), typed, action, detail, ip))


def readme_shots(browser, app):
    page = browser.new_page(viewport={'width': 1280, 'height': 720})
    page.goto(BASE + '/login')
    save(page.screenshot(), README_DIR / 'login.png')

    login(page, 'bob')
    save(page.screenshot(full_page=True), README_DIR / 'week.png')
    page.goto(BASE + '/month?month=2026-10')
    save(page.screenshot(full_page=True), README_DIR / 'month.png')

    phone = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2,
                                is_mobile=True, has_touch=True).new_page()
    login(phone, 'bob')
    save(phone.screenshot(), README_DIR / 'phone.png')

    phone = push_page(browser, 'marco', viewport={'width': 390, 'height': 844}, device_scale_factor=2,
                      is_mobile=True, has_touch=True)
    card = notifications_card(phone)
    save(phone.screenshot(clip=union(card.bounding_box(), pad=8), full_page=True), README_DIR / 'notifications.png')

    admin = browser.new_page(viewport={'width': 1280, 'height': 720})
    login(admin, 'alice')
    admin.goto(BASE + '/admin')
    save(admin.screenshot(full_page=True), README_DIR / 'admin.png')

    slots.now = lambda: AUDIT_NOW
    readme_audit(app)
    admin.goto(BASE + '/admin/audit')
    save(admin.screenshot(full_page=True), README_DIR / 'audit.png')


# --- Help page --------------------------------------------------------------------------------

HELP_DIR = ROOT / 'laundry' / 'static' / 'help'
HELP_NOW = datetime(2026, 10, 22, 14, 20)  # a Thursday afternoon: "running" slot and today are visible


HELP_NOTES = {  # (day, slot): note, on top of the demo bookings
    ('2026-10-24', 'Nachmittag'): '60°-Wäsche, Maschine danach frei',  # Alice's
}


def help_data(app):
    slots.now = lambda: HELP_NOW
    app.test_cli_runner().invoke(seed_demo_command)  # bob / password456, not in a household
    with get_db() as con:
        # Bob: confirmed address, reminder by mail, freed slots by mail and push
        con.execute("UPDATE users SET email = 'bob@example.ch', email_verified = 1, reminder_minutes = 60, "
                    "reminder_mail = 1, notify_freed = 1, freed_push = 1 WHERE username = 'bob'")
        con.executemany('UPDATE reservations SET note = ? WHERE day = ? AND slot = ?',
                        [(note, day, slot) for (day, slot), note in HELP_NOTES.items()])


def help_shots(browser, app):
    page = browser.new_page(viewport={'width': 1280, 'height': 1000})
    login(page, 'bob', 'password456')
    page.add_style_tag(content=HIGHLIGHT)
    save(page.screenshot(clip=union(page.locator('.split').bounding_box(), pad=8)), HELP_DIR / 'week.png')

    # Book Friday evening; the flash message and the new blue slot
    page.click('button[aria-label="2026-10-23 Abend buchen"]')
    page.mouse.move(0, 0)
    page.add_style_tag(content=HIGHLIGHT + '.page-head, .legend { display: none; }')
    mark(page, 'button[aria-label="2026-10-23 Abend (Du) stornieren"]')
    # The message and the left part of the week, up to Saturday
    box = union(page.locator('.flash').bounding_box(), page.locator('.week').bounding_box(), pad=8)
    sat = page.locator('.day').nth(5).bounding_box()
    box['width'] = sat['x'] + sat['width'] + 8 - box['x']
    save(page.screenshot(clip=box), HELP_DIR / 'book.png')
    unmark(page)

    # Whole-day buttons in the next week (one day is partly booked: "Alle freien")
    page.goto(BASE + '/?week=' + (HELP_NOW.date() + timedelta(days=4)).isoformat())
    page.add_style_tag(content=HIGHLIGHT)
    mark(page, '.day-book')
    save(page.screenshot(clip=union(page.locator('.week').bounding_box(), pad=8)), HELP_DIR / 'book-day.png')
    unmark(page)

    # Cancelling: hover over an own booking, and the list of upcoming bookings
    hi_res = browser.new_page(viewport={'width': 1280, 'height': 1000}, device_scale_factor=2,
                              storage_state=page.context.storage_state())
    hi_res.goto(BASE + '/')
    hi_res.add_style_tag(content=HIGHLIGHT)
    own = 'button[aria-label="2026-10-24 Vormittag (Du) stornieren"]'
    hi_res.hover(own)
    mark(hi_res, own)
    days = hi_res.locator('.day')
    save(hi_res.screenshot(clip=union(days.nth(4).bounding_box(), days.nth(6).bounding_box(), pad=8)),
         HELP_DIR / 'cancel-slot.png')
    unmark(hi_res)
    hi_res.mouse.move(0, 0)
    mark(hi_res, '.upcoming .btn')
    save(hi_res.locator('.split-side .card').nth(1).screenshot(), HELP_DIR / 'cancel-list.png')
    unmark(hi_res)

    # Notes: the badges on Saturday (Alice's note, "+" on bob's own booking), then bob writing one
    saturday = days.nth(5)
    saturday.locator('.note-badge').evaluate_all("els => els.forEach(e => e.classList.add('hl'))")
    save(hi_res.screenshot(clip=union(days.nth(4).bounding_box(), days.nth(6).bounding_box(), pad=8)),
         HELP_DIR / 'note-badge.png')
    unmark(hi_res)
    saturday.locator('.note-badge.empty').click()
    hi_res.fill('.note-pop:popover-open textarea', 'Ich hole die Wäsche erst um 13 Uhr')
    hi_res.mouse.move(0, 0)
    save(hi_res.locator('.note-pop:popover-open').screenshot(), HELP_DIR / 'note-edit.png')
    hi_res.close()

    page.goto(BASE + '/month?month=2026-10')
    save(page.screenshot(clip=union(page.locator('.page-head').bounding_box(),
                                    page.locator('.month').bounding_box(), pad=8)), HELP_DIR / 'month.png')
    page.close()

    phone = browser.new_context(viewport={'width': 390, 'height': 760}, device_scale_factor=2,
                                is_mobile=True, has_touch=True)
    page = phone.new_page()
    login(page, 'bob', 'password456')
    save(page.screenshot(), HELP_DIR / 'phone.png')
    phone.close()

    # Profile: the push button (not yet active on this phone) and the channels
    page = push_page(browser, 'bob', 'password456', viewport={'width': 390, 'height': 844}, device_scale_factor=2,
                     is_mobile=True, has_touch=True)
    card = notifications_card(page)
    page.add_style_tag(content=HIGHLIGHT)
    mark(page, '#push-toggle, .channels')
    save(page.screenshot(clip=union(card.bounding_box(), pad=8), full_page=True), HELP_DIR / 'notifications.png')
    page.context.close()


SETS = {'readme': (readme_data, readme_shots), 'help': (help_data, help_shots)}


def main():
    parser = argparse.ArgumentParser(description='Retake the screenshots.')
    parser.add_argument('sets', nargs='*', metavar='readme|help', help='default: both')
    names = parser.parse_args().sets or list(SETS)
    for name in names:
        if name not in SETS:
            parser.error(f'unknown set {name!r}')
    for name in names:
        run(*SETS[name])


if __name__ == '__main__':
    main()
