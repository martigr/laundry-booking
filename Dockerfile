FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE=/data/laundry.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY laundry/ ./laundry/

# Run as an unprivileged user; the database lives on a volume
RUN useradd --system --uid 1000 app && mkdir /data && chown app /data
USER app
VOLUME /data

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health')" || exit 1

# SQLite allows one writer at a time, so a few workers are plenty
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "laundry:create_app()"]
