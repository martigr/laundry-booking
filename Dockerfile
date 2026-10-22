FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE=/data/laundry.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY laundry/ ./laundry/
COPY docker-entrypoint.sh /usr/local/bin/

# The container starts as root only for the entrypoint, which fixes the owner of
# /data and then runs the app as PUID:PGID (default 1000:1000). See README.
RUN mkdir /data
VOLUME /data

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health')" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
# SQLite allows one writer at a time, so a few workers are plenty.
# The logger class keeps the tokens of mail links out of the access log.
# No control socket (gunicornc is not used): by default Gunicorn creates it in $HOME, which
# is still /root after the entrypoint drops privileges, so it would log "Permission denied".
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "--access-logfile", "-", \
     "--logger-class", "laundry.accesslog.RedactingLogger", "--no-control-socket", \
     "laundry:create_app()"]
