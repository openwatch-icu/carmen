FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -r -s /bin/false appuser

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY backend/ ./backend/
COPY frontend/ ./frontend/

RUN mkdir -p /tmp/openwatch && chown appuser:appuser /tmp/openwatch \
    && mkdir -p /home/appuser && chown appuser:appuser /home/appuser

ENV HOME=/home/appuser
ENV PORT=8000
ENV PROXY_ENABLED=true
ENV ALLOWED_ORIGIN=
ENV TMPDIR=/tmp/openwatch

USER appuser

# Gunicorn 25+ enables a control server by default and creates gunicorn.ctl in CWD.
# We run as non-root (appuser); CWD is backend/ (root-owned), so disable the socket.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8000} --worker-class gevent --workers ${GUNICORN_WORKERS:-4} --worker-connections 100 --timeout 30 --worker-tmp-dir /tmp/openwatch --no-control-socket --chdir backend main:app"]
