FROM node:22-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package*.json ./
# ci, not install: the lockfile decides the versions.
RUN npm ci
COPY frontend/ ./
# The build number is baked into the bundle so the interface can tell when a
# browser is serving an old one.
COPY BUILD /BUILD
# The bundler alone; type checking is `npm run check`, so a leftover source file
# nothing imports cannot fail an image build.
RUN npm run build

FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="AniFlow" \
      org.opencontainers.image.description="Deterministic anime download manager" \
      org.opencontainers.image.licenses="CC-BY-NC-ND-4.0" \
      net.unraid.docker.icon="/logo-256.png"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
# tzdata makes TZ take effect. ffmpeg is installed for ffprobe alone: AniFlow
# reads each imported file to tell whether the media server can play it directly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY VERSION BUILD ./
# Includes the built-in themes (backend/app/themes/).
COPY backend/ ./backend/
COPY --from=frontend-build /frontend/dist ./frontend_dist/

# The build context may come from a host where files are 0770 and owned by root;
# a non-root container user could then not read its own application.
RUN chmod -R a+rX /app

RUN mkdir -p /config/log /config/indexers /config/subgroup /config/rss_source /config/themes /data/covers /media /downloads /import

ENV CONFIG_DIR=/config \
    DATA_DIR=/data \
    LOG_FILE=/config/log/aniflow.log \
    APP_PORT=9630 \
    APP_ENV=production \
    TZ=UTC

EXPOSE 9630

# Healthy means the database answers. A torrent client that is down is
# deliberately not unhealthy: restarting AniFlow would not fix it. Python, not
# curl, because the image has no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import json,os,urllib.request as u; p=os.environ.get('APP_PORT','9630'); r=u.urlopen('http://127.0.0.1:'+p+'/api/system/health',timeout=4); raise SystemExit(0 if json.load(r).get('database')=='ok' else 1)"

CMD ["sh", "-c", "alembic -c /app/backend/alembic.ini upgrade head && exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${APP_PORT:-9630}"]
