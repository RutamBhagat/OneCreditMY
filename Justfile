set shell := ["bash", "-euo", "pipefail", "-c"]

default: setup

# Install the producer's dependencies.
setup:
    uv sync --locked

# Start Postgres (with pgvector) and RabbitMQ, and wait until both are healthy.
up:
    docker compose up -d --wait

# Apply all pending database migrations.
migrate:
    uv run --locked alembic upgrade head

# Start the consumer.
worker:
    uv run --locked python -m companies.worker

# Start the recommendation API and Scalar docs.
api:
    uv run --locked uvicorn companies.api:app

# Run the tests.
test:
    uv run --locked python -m pytest -q

# Roll back one database migration. This can delete data.
rollback:
    uv run --locked alembic downgrade -1

# Stop everything and delete the volumes.
down:
    docker compose down -v

# Replay the registry feed onto the company-events queue.
publish:
    uv run python -m producer.publish
