FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BLINK_DATA_DIR=/data \
    PORT=8099

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY backend/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY backend/ /srv/backend/
COPY tizen/ /srv/tizen/

RUN useradd --create-home --uid 10001 bridge \
    && mkdir -p /data \
    && chown -R bridge:bridge /data /srv

USER bridge

EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/api/health', timeout=3)"

CMD ["python", "-m", "backend.app"]
