# -----------------------------------------------------------------------------
# daseric/movie-tv-downloader — single-container image
#
# Stage 1: build the React/Vite frontend
# Stage 2: Python runtime with ffmpeg, yt-dlp and all native deps baked in.
#          The built frontend is copied into /app/app/static and served by
#          FastAPI, so the container is truly plug-and-play.
#
# Publish to Docker Hub:
#   docker build -t daseric/movie-tv-downloader:latest .
#   docker push   daseric/movie-tv-downloader:latest
# -----------------------------------------------------------------------------

# ---------- Stage 1: frontend ----------
FROM node:20-alpine AS frontend-builder

WORKDIR /build

# Install deps first for better cache utilisation
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund --loglevel=error

# Then the sources
COPY frontend/ ./
RUN npm run build


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    H0MELAB_HOST=0.0.0.0 \
    H0MELAB_PORT=3000 \
    TZ=Europe/Berlin

# System deps:
#   ffmpeg   - post-processing (H.264/AAC MP4 remux/transcode)
#   curl     - healthcheck + general use
#   tini     - PID 1 reaper for clean shutdown
#   tzdata   - honour the TZ env var
#   build-essential / libxml2-dev / libxslt1-dev / libffi-dev
#            - native build fallback for lxml / curl_cffi / cryptography
#   ca-certificates - TLS trust store
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        tini \
        tzdata \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

WORKDIR /app

# Python deps (pinned) + latest yt-dlp nightly
COPY backend/requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install --pre --upgrade yt-dlp

# Backend source
COPY backend/app ./app

# Bake the built frontend into the image at a well-known path.
# FastAPI (app.main) mounts this as a static SPA.
COPY --from=frontend-builder /build/dist ./app/static

# Data dirs (these are also the default volume mount points in docker-compose)
RUN mkdir -p /movies /tv /config /tmp/h0melab

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:3000/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]
