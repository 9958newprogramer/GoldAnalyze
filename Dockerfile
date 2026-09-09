FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_PROJECT_ROOT=/app

WORKDIR /app

COPY pyproject.toml README.md requirements.lock ./
COPY app ./app
COPY evals ./evals
COPY skills ./skills
COPY scripts ./scripts

RUN python -m pip install --requirement requirements.lock \
    && python -m pip install --no-deps . \
    && groupadd --system --gid 10001 aurumlab \
    && useradd --system --uid 10001 --gid aurumlab --home-dir /app aurumlab \
    && mkdir -p /app/var \
    && chown -R aurumlab:aurumlab /app/var

USER 10001:10001

EXPOSE 8010

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/api/health', timeout=2).read()"]

CMD ["aurumlab"]
