# OneCreditMY Company Recommendations

A solution to the OneCredit Machine Learning Engineer take-home. A RabbitMQ consumer turns registry events into company records and embeddings. A FastAPI service uses PostgreSQL and pgvector to recommend similar companies based on what they do. All company data is synthetic.

[Live frontend](https://onecredit.rutam.dpdns.org/demo/SYN-001550) · [Live backend API reference (Scalar)](https://onecredit.rutam.dpdns.org/scalar) · [Design decisions](DESIGN.md)

## Run locally

Install Docker with Compose, [uv](https://docs.astral.sh/uv/), and [just](https://github.com/casey/just). Run the commands from the repository root. uv installs Python 3.14 if needed.

```bash
just setup
just up
just migrate
```

1. **Start the consumer** and leave it running.

   ```bash
   uv run --locked python -m companies.worker
   ```

   On first start, FastEmbed downloads the multilingual MiniLM model. Set `FASTEMBED_CACHE_PATH` to an absolute path to use a persistent cache.

2. **Start the API** in a second terminal.

   ```bash
   just api
   ```

3. **Replay the registry feed** in a third terminal.

   ```bash
   just publish
   ```

   In the [RabbitMQ management UI](http://127.0.0.1:15672) (`guest` / `guest`), wait for `company-events` to show one consumer, zero ready messages, and zero unacknowledged messages.

4. **Try a recommendation** in the [local frontend](http://127.0.0.1:8000/demo/SYN-001550), in [local Scalar](http://127.0.0.1:8000/scalar), or with curl.

   ```bash
   curl --fail-with-body 'http://127.0.0.1:8000/companies/SYN-001550/similar?limit=4'
   ```

<details>
<summary>Commands without just and local configuration</summary>

| Recipe | Equivalent command |
| --- | --- |
| `just setup` | `uv sync --locked` |
| `just up` | `docker compose up -d --wait` |
| `just migrate` | `uv run --locked alembic upgrade head` |
| `just api` | `uv run --locked uvicorn companies.api:app` |
| `just publish` | `uv run python -m producer.publish` |

PostgreSQL defaults to `postgresql://postgres:postgres@localhost:5432/companies`. RabbitMQ uses port `5672` and its management UI uses `15672`. To change those ports, export `POSTGRES_PORT`, `RABBITMQ_PORT`, or `RABBITMQ_UI_PORT` before starting Compose. Keep any port overrides in the terminals that run migrations, the consumer, the API, and the publisher. `DATABASE_URL` and `RABBITMQ_URL` override their respective connection URLs. To change the API port, pass `--port 8001` to Uvicorn.

</details>

Stop the consumer and API with Ctrl+C. Run `docker compose down` to stop the local infrastructure and keep its volumes.

> [!WARNING]
> `just down` removes the local PostgreSQL and RabbitMQ volumes. To start with fresh data, run `just up` and `just migrate`, then restart the consumer and API and replay the feed.

## Recommendation API

`GET /companies/{registration_number}/similar` returns active companies ordered by cosine distance, excluding the source company. The optional `limit` parameter accepts 1–50 and defaults to 4. Each result contains `registration_number`, `name`, `industry`, and `state`.

| Response | Meaning |
| --- | --- |
| `200` | Recommendation list, which can be empty |
| `404` | Unknown or inactive source company |
| `422` | Invalid limit |
| `503` | Database connection unavailable |

## How it works

1. The [producer](producer/publish.py) replays 2,518 deliveries for 2,000 synthetic Malaysian companies from [the fixture](data/feed.jsonl) onto the durable `company-events` queue.
2. The [consumer](companies/worker.py) embeds the business description with a CPU-based multilingual MiniLM model and falls back to industry or company name. PostgreSQL accepts only newer registry snapshots, preserves strike-offs as inactive records, and stores invalid events for inspection. The consumer deduplicates by AMQP message ID and acknowledges after commit.
3. The [API](companies/api.py) queries pgvector for similar active companies. The browser demo renders the same recommendations and links each result to its company page.

See [DESIGN.md](DESIGN.md) for assumptions, full-feed verification, limitations, and changes for 10× volume.

## Tests

Run `uv run --locked python -m pytest -q` for tests that require no services. For the full suite, run PostgreSQL and RabbitMQ in a separate Compose project with ephemeral host ports.

```bash
POSTGRES_PORT=0 RABBITMQ_PORT=0 RABBITMQ_UI_PORT=0 \
  docker compose -p onecredit-tests up -d --wait
export TEST_DATABASE_URL="postgresql://postgres:postgres@$(docker compose -p onecredit-tests port postgres 5432)/companies"
export TEST_RABBITMQ_URL="amqp://guest:guest@$(docker compose -p onecredit-tests port rabbitmq 5672)/"
uv run --locked python -m pytest -q
docker compose -p onecredit-tests down -v
```

With the local demo running and seeded, you can also check direct loads, navigation, Back, and the missing-company page with and without JavaScript.

```bash
uv run --with playwright==1.65.2 python -m playwright install chromium --only-shell
uv run --with playwright==1.65.2 python tests/demo_browser.py http://127.0.0.1:8000
```

## Hosted deployment

Cloudflare DNS points the demo hostname to a single ARM64 VM. Uncloud runs the API, consumer, PostgreSQL, and RabbitMQ using [the production manifest](compose.production.yml). Caddy serves the frontend and API over HTTPS. PostgreSQL and RabbitMQ use private networking and persistent volumes.

The [verification workflow](.github/workflows/verify.yml) builds and publishes a SHA-tagged ARM64 image for deployment. Its `api_only` dispatch option updates the API container for frontend-only changes. For a manual deployment, use an authenticated Uncloud client and export a verified `IMAGE_TAG`, `POSTGRES_PASSWORD`, and `RABBITMQ_PASSWORD` from the deployment environment. The deploy hook applies migrations. To seed a new deployment, replay the fixture from the consumer container.

```bash
uc --connect a1 deploy --yes --no-build --file compose.production.yml
uc --connect a1 exec -T onecredit-worker python -m producer.publish
```
