"""Laundry room booking for a shared washing machine."""
import os
from datetime import timedelta

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

# Shown in the page footer; must match `version` in pyproject.toml (test_version_matches_pyproject)
__version__ = '1.3.0'


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
        # Forms are tiny; larger requests get 413 instead of filling memory
        MAX_CONTENT_LENGTH=64 * 1024,
        # Number of reverse proxies in front of the app whose X-Forwarded-For/-Proto headers to trust
        TRUSTED_PROXIES=int(os.environ.get('TRUSTED_PROXIES', '0')),
        # Who to contact about security problems of this instance, shown in /.well-known/security.txt
        SECURITY_CONTACT=os.environ.get('SECURITY_CONTACT'),
        # Who runs this instance and how to reach them, shown on the privacy page (/privacy)
        PRIVACY_CONTROLLER=os.environ.get('PRIVACY_CONTROLLER'),
        PRIVACY_CONTACT=os.environ.get('PRIVACY_CONTACT'),
        # Mail (e.g. Infomaniak: mail.infomaniak.com, 587/starttls or 465/ssl)
        SMTP_HOST=os.environ.get('SMTP_HOST'),
        SMTP_PORT=int(os.environ.get('SMTP_PORT', '587')),
        SMTP_SECURITY=os.environ.get('SMTP_SECURITY', 'starttls'),  # starttls | ssl | none
        SMTP_USER=os.environ.get('SMTP_USER'),
        SMTP_PASSWORD=os.environ.get('SMTP_PASSWORD'),
        MAIL_FROM=os.environ.get('MAIL_FROM'),
        MAIL_FROM_NAME=os.environ.get('MAIL_FROM_NAME', 'Waschküche'),
        # Public address of the app, used for links in mails and the calendar link
        BASE_URL=os.environ.get('BASE_URL', 'http://localhost:5000'),
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

    if app.config['TRUSTED_PROXIES']:
        # Client address (login throttling, logs) and https (HSTS) as seen by the proxy
        n = app.config['TRUSTED_PROXIES']
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n)

    from . import admin, auth, bookings, cli, db, guide, ical, mail, privacy, profile, security

    db.ensure_writable(app.config['DATABASE'])

    db.init_app(app)
    auth.init_app(app)
    cli.init_app(app)
    security.init_app(app)
    privacy.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(bookings.bp)
    app.register_blueprint(profile.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(ical.bp)
    app.register_blueprint(guide.bp)

    @app.context_processor
    def template_globals():
        return {'mail_enabled': mail.is_configured(), 'app_version': __version__}

    @app.route('/health')
    def health():
        # Liveness probe for load balancers / container health checks
        return 'ok', 200

    with app.app_context():
        db.init_db()

    return app
