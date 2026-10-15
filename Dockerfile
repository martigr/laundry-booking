FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY laundry/ .

EXPOSE 5000

# Run a production WSGI server (Gunicorn). For Windows hosts you can set USE_WAITRESS=1 and start with
# waitress-serve --listen=0.0.0.0:5000 app:app from the container shell instead.
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
