"""Laundry room booking for a shared washing machine."""
import os
from datetime import timedelta

from flask import Flask


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get('SECRET_KEY'),
        DATABASE=os.environ.get('DATABASE') or os.path.join(app.instance_path, 'laundry.db'),
        # Local time zone of the building; decides what "today" and "now" mean
        TIMEZONE=os.environ.get('APP_TIMEZONE', 'Europe/Zurich'),
        ADMIN_USERNAME=os.environ.get('ADMIN_USERNAME'),
        ADMIN_PASSWORD=os.environ.get('ADMIN_PASSWORD'),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '0') == '1',
        SESSION_COOKIE_SAMESITE=os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax'),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    if test_config:
        app.config.update(test_config)
    app.logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO').upper())

    if not app.config['SECRET_KEY']:
        if not (app.debug or app.testing):
            raise RuntimeError('SECRET_KEY is not set. Set it in the environment, '
                               'or start with --debug for local development.')
        app.config['SECRET_KEY'] = 'dev-only-secret'
        app.logger.warning('Using the development SECRET_KEY')

    os.makedirs(os.path.dirname(os.path.abspath(app.config['DATABASE'])), exist_ok=True)

    from . import admin, auth, bookings, cli, db, profile

    db.init_app(app)
    auth.init_app(app)
    cli.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(bookings.bp)
    app.register_blueprint(profile.bp)
    app.register_blueprint(admin.bp)

    @app.route('/health')
    def health():
        # Liveness probe for load balancers / container health checks
        return 'ok', 200

    with app.app_context():
        db.init_db()

    return app
