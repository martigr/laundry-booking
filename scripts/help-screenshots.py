"""Retake the screenshots of the help page (laundry/static/help/*.png).

Starts the app on a temporary database with demo data and a fixed clock, logs in as bob and
photographs the parts the help page explains. Needs Playwright with Chromium, which is not in
requirements-dev.txt because only this script uses it:

    pip install playwright && playwright install chromium
    python scripts/help-screenshots.py

To use an already installed Chromium instead, set CHROMIUM=/path/to/chrome.

Check the width/height attributes in templates/help.html afterwards (the script prints the sizes).
"""
import io
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from laundry import create_app, slots  # noqa: E402
from laundry.cli import seed_demo_command  # noqa: E402

OUT = ROOT / 'laundry' / 'static' / 'help'
NOW = datetime(2026, 10, 22, 14, 20)  # a Thursday afternoon: "running" slot and today are visible
PORT = 5077
HIGHLIGHT = '.hl { outline: 3px solid #FB923C !important; outline-offset: 3px; }'


def save(png, name):
    """Store a screenshot with a reduced palette (a fraction of the size, looks the same)."""
    img = Image.open(io.BytesIO(png)).convert('RGB')
    img.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(OUT / f'{name}.png', optimize=True)
    print(f'{name}.png  {img.width}x{img.height}px')


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


def login(page, base):
    page.goto(base + '/login')
    page.fill('#username', 'bob')
    page.fill('#password', 'password456')
    page.click('button[type=submit]')
    page.mouse.move(0, 0)  # no hover effect on what was under the login button


def main():
    slots.now = lambda: NOW
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app({'SECRET_KEY': 'screenshots', 'DATABASE': f'{tmp}/demo.db'})
        with app.app_context():
            app.test_cli_runner().invoke(seed_demo_command)
        server = make_server('127.0.0.1', PORT, app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{PORT}'
        OUT.mkdir(exist_ok=True)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM'))
                shoot_desktop(browser, base)
                shoot_phone(browser, base)
                browser.close()
        finally:
            server.shutdown()


def shoot_desktop(browser, base):
    page = browser.new_page(viewport={'width': 1280, 'height': 1000})
    login(page, base)
    page.add_style_tag(content=HIGHLIGHT)
    save(page.screenshot(clip=union(page.locator('.split').bounding_box(), pad=8)), 'week')

    # Book Friday evening; the flash message and the new blue slot
    page.click('button[aria-label="2026-10-23 Abend buchen"]')
    page.mouse.move(0, 0)
    page.add_style_tag(content=HIGHLIGHT + '.page-head, .legend { display: none; }')
    mark(page, 'button[aria-label="2026-10-23 Abend (Du) stornieren"]')
    # The message and the left part of the week, up to Saturday
    box = union(page.locator('.flash').bounding_box(), page.locator('.week').bounding_box(), pad=8)
    sat = page.locator('.day').nth(5).bounding_box()
    box['width'] = sat['x'] + sat['width'] + 8 - box['x']
    save(page.screenshot(clip=box), 'book')
    unmark(page)

    # Whole-day buttons in the next week (one day is partly booked: "Alle freien")
    page.goto(base + '/?week=2026-10-26')
    page.add_style_tag(content=HIGHLIGHT)
    mark(page, '.day-book')
    save(page.screenshot(clip=union(page.locator('.week').bounding_box(), pad=8)), 'book-day')
    unmark(page)

    # Cancelling: hover over an own booking, and the list of upcoming bookings
    hi_res = browser.new_page(viewport={'width': 1280, 'height': 1000}, device_scale_factor=2,
                              storage_state=page.context.storage_state())
    hi_res.goto(base + '/')
    hi_res.add_style_tag(content=HIGHLIGHT)
    own = 'button[aria-label="2026-10-24 Vormittag (Du) stornieren"]'
    hi_res.hover(own)
    mark(hi_res, own)
    days = hi_res.locator('.day')
    save(hi_res.screenshot(clip=union(days.nth(4).bounding_box(), days.nth(6).bounding_box(), pad=8)),
         'cancel-slot')
    unmark(hi_res)
    hi_res.mouse.move(0, 0)
    mark(hi_res, '.upcoming .btn')
    save(hi_res.locator('.split-side .card').nth(1).screenshot(), 'cancel-list')
    hi_res.close()

    page.goto(base + '/month?month=2026-10')
    save(page.screenshot(clip=union(page.locator('.page-head').bounding_box(),
                                    page.locator('.month').bounding_box(), pad=8)), 'month')
    page.close()


def shoot_phone(browser, base):
    context = browser.new_context(viewport={'width': 390, 'height': 760}, device_scale_factor=2,
                                  is_mobile=True, has_touch=True)
    page = context.new_page()
    login(page, base)
    save(page.screenshot(), 'phone')
    context.close()


if __name__ == '__main__':
    main()
