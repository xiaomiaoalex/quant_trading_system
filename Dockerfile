FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# pg_dump/pg_restore are required by the backup service; no Docker socket is
# mounted, so tooling runs inside the application image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY trader/requirements-runtime.txt trader/requirements-runtime.txt
RUN pip install --no-cache-dir --retries 10 --timeout 120 \
    -r trader/requirements-runtime.txt

COPY . .
RUN useradd --create-home --uid 10001 qts \
    && mkdir -p /var/lib/qts \
    && chown -R qts:qts /app /var/lib/qts

USER qts
