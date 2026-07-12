FROM python:3.14-slim

# tzdata: cron schedules are IST; curl: fetch supercronic
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata curl && rm -rf /var/lib/apt/lists/*

# supercronic: container-native cron (stdout logging, no daemon)
ARG SUPERCRONIC_VERSION=v0.2.34
RUN curl -fsSL -o /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && chmod +x /usr/local/bin/supercronic

ENV TZ=Asia/Kolkata \
    PYTHONUNBUFFERED=1

# uv: fast, lockfile-driven dependency install
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/req.txt \
    && uv pip install --system --no-cache -r /tmp/req.txt

COPY bondlens/ bondlens/
COPY scripts/ scripts/
COPY crontab .

# default: the API. The jobs service overrides with supercronic.
CMD ["uvicorn", "bondlens.app:app", "--host", "0.0.0.0", "--port", "8000"]
