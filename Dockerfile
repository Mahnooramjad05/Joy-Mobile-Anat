# Device trade-in pricing API, and the nightly price sync.
#
#     docker build -t joy-mobile-api .
#
# One image serves both. The container runs the API; Coolify's Scheduled Tasks
# run `python -m scraper.sync ...` inside that same running container, so the
# sync needs no image, no server and no credentials of its own.
#
# This lives at the repository root because the build context has to be the
# root: the api package uses relative imports (`from . import settings`), so it
# must be importable as `api.app` with the root on the path, and
# requirements.txt and wsgi.py are at the root too. Keeping the Dockerfile
# beside them means no -f flag and no path override in Coolify.
#
# In Coolify: Build Pack = Dockerfile, everything else left at its default.

FROM python:3.11-slim

# Python behaves better in containers with these: no .pyc clutter, unbuffered
# logs so Coolify shows them live, and no pip version nagging in build output.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata: the sync decides whether it is the 06:00 hour in Israel, and zoneinfo
# needs the system timezone database to answer that. python:3.11-slim ships
# without one, so the DST guard cannot work unless this is installed.
RUN apt-get update \
    && apt-get install --no-install-recommends -y tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, in their own layer: this only re-runs when
# requirements.txt changes, so code edits rebuild in seconds.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The API, and the sync that Coolify's scheduled task runs in this container.
# .dockerignore keeps scraper/tests and scraper/fixtures out -- the sync needs
# neither at runtime, and the fixtures are 270 KB of captured catalogue.
COPY api/ ./api/
COPY scraper/ ./scraper/
COPY wsgi.py ./

# Run as a non-root user. The image holds no secrets -- credentials arrive at
# runtime through GOOGLE_CREDENTIALS_JSON -- but a web process should not be
# root regardless.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

# Defaults. Override SPREADSHEET_ID and GOOGLE_CREDENTIALS_JSON in Coolify.
ENV FLASK_APP=api.app \
    FLASK_ENV=production \
    PORT=5000 \
    PYTHONPATH=/app

EXPOSE 5000

# Fails the container if the app stops answering. Coolify reads this to decide
# whether a deployment came up healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4).status == 200 else 1)"

# Gunicorn rather than `flask run`: the Flask development server is single
# threaded and explicitly not for production, and wsgi.py warms the device cache
# at import so the first customer request is not the slow one. Two workers each
# hold their own 30-minute cache, which is fine -- they are read-only.
#
# To use the development server instead, swap the line below for:
#   CMD ["python", "-m", "flask", "run", "--host=0.0.0.0", "--port=5000"]
# FLASK_APP is already set above, so that command works as-is.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-", "wsgi:application"]
