"""Command line: flask --app laundry <command>."""
from datetime import timedelta

import click
from flask.cli import with_appcontext

from . import slots
from .db import get_db
from .users import UserError, create_user, get_user_by_username

DEMO_USERS = [
    # username, password, display name, admin
    ('alice', 'password123', 'Alice', True),
    ('bob', 'password456', 'Bob', False),
    ('sandra', 'password789', 'Sandra', False),
]


@click.command('create-user')
@with_appcontext
@click.argument('username')
@click.option('--name', 'display_name', help='Anzeigename (Standard: Benutzername)')
@click.option('--admin', is_flag=True, help='Benutzer erhält Admin-Rechte')
@click.password_option()
def create_user_command(username, display_name, admin, password):
    """Legt einen Benutzer an."""
    try:
        create_user(username, password, display_name, admin)
    except UserError as e:
        raise click.ClickException(str(e)) from None
    click.echo(f'Benutzer {username} angelegt.')


@click.command('make-admin')
@with_appcontext
@click.argument('username')
def make_admin_command(username):
    """Gibt einem bestehenden Benutzer Admin-Rechte."""
    user = get_user_by_username(username)
    if user is None:
        raise click.ClickException(f'Benutzer {username} nicht gefunden.')
    with get_db() as con:
        con.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (user['id'],))
    click.echo(f'{username} ist jetzt Admin.')


@click.command('seed-demo')
@with_appcontext
def seed_demo_command():
    """Demo-Benutzer und ein paar Buchungen für die lokale Entwicklung."""
    ids = {}
    for username, password, name, is_admin in DEMO_USERS:
        user = get_user_by_username(username)
        ids[username] = user['id'] if user else create_user(username, password, name, is_admin)

    today = slots.now().date()
    monday = today - timedelta(days=today.weekday())
    demo_bookings = [(0, 'Vormittag', 'alice'), (1, 'Abend', 'sandra'), (3, 'Nachmittag', 'bob'),
                     (4, 'Vormittag', 'sandra'), (5, 'Vormittag', 'bob'), (5, 'Nachmittag', 'alice'),
                     (9, 'Abend', 'sandra')]
    with get_db() as con:
        for offset, slot, username in demo_bookings:
            con.execute('INSERT OR IGNORE INTO reservations (day, slot, user_id) VALUES (?, ?, ?)',
                        ((monday + timedelta(days=offset)).isoformat(), slot, ids[username]))

    click.echo('Demo-Benutzer: ' + ', '.join(f'{u} / {p}' for u, p, _, _ in DEMO_USERS))


def init_app(app):
    for command in (create_user_command, make_admin_command, seed_demo_command):
        app.cli.add_command(command)
