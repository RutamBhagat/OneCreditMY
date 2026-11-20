FROM ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc AS uv
FROM python:3.14-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b AS python

FROM python AS dependencies
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache --no-python-downloads

FROM python
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/home/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app
COPY --from=dependencies /app/.venv .venv
COPY alembic.ini ./
COPY companies/ companies/
COPY migrations/ migrations/
COPY producer/ producer/
COPY data/feed.jsonl data/feed.jsonl
USER app
EXPOSE 8000
CMD ["uvicorn", "companies.api:app", "--host", "0.0.0.0", "--port", "8000"]
