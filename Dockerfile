# One image for both services (api + worker) — a single build instead of two.
# debian-slim rather than python-slim so python3-gammu comes as a prebuilt package from apt.
# Debian ships it for arm64 and amd64, so this builds natively on either; pip would have to
# compile Gammu from C sources instead — slow on x86, unbearable on a Raspberry Pi 3.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-gammu \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY gateway/ gateway/
# The admin panel's Docs section renders these at runtime (gateway/api/docs_pages.py
# resolves them relative to the repo root, i.e. /app/docs/manual in the image).
COPY docs/manual/ docs/manual/

ENV PYTHONUNBUFFERED=1
# The command comes from docker-compose.yml (uvicorn for api, the worker module for worker)
