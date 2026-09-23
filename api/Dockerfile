# Device trade-in pricing API.
#
# IMPORTANT: build from the REPOSITORY ROOT, not from api/.
#
#     docker build -f api/Dockerfile -t joy-mobile-api .
#
# The api package uses relative imports (`from . import settings`), so it has to
# be imported as `api.app` with the repository root on the path. requirements.txt
# and wsgi.py also live at the root. A build context of api/ cannot see any of
# that, so the context is the root and this file just happens to live in api/.
#
# In Coolify: Build Pack = Dockerfile, Dockerfile Location = /api/Dockerfile,
# Base Directory = / (the repository root).

FROM python:3.11-slim

# Python behaves better in containers with these: no .pyc clutter, unbuffered
# logs so Coolify shows them live, and no pip version nagging in build output.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, in their own layer: this only re-runs when
# requirements.txt changes, so code edits rebuild in seconds.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The application. scraper/config.py comes along because api/settings.py falls
# back to it when SPREADSHEET_ID is not set in the environment.
COPY api/ ./api/
COPY wsgi.py ./
COPY scraper/config.py ./scraper/config.py

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
