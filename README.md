# MLE Take-Home: Company Recommendation System

Welcome to the **Machine Learning Engineer** take-home from the OneCredit Data & AI team. It's a simplified version of
a system we run in production, where it powers company recommendations and lead generation.

**Who it's for:** people early in their career, with 0 to 2 years of experience. Fresh graduates are welcome.

If you applied, thanks for your time. If you didn't, we hope this helps you learn something new, such as RabbitMQ or
pgvector.

Assessment version: **2026.11** (`mle-v2026.11`). What changed between versions is in [`CHANGELOG.md`](CHANGELOG.md).

## The scenario

When someone opens a company's page on the OneCredit portal, we want to show a **"Similar companies"** panel next to
it. Someone looking at a coffee shop chain in Selangor should see other companies that do similar things.

The registry team already publishes every company change to a RabbitMQ queue. Your job is to build the service that
powers that panel from the feed.

### What the user sees

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  OneCredit Portal                                         Search companies…  │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Sinar Teguh Bakery Sdn. Bhd.                                   SYN-001550   │
│  Food & Beverage · Selangor                                                  │
│  Operating cafes and coffee shops in the Klang Valley                        │
│                                                                              │
│  Similar companies                                                           │
│  ┌────────────────────────────────────┐  ┌────────────────────────────────┐  │
│  │ Seri Sejahtera Food Sdn. Bhd.      │  │ Everbright Pacific Food        │  │
│  │ Food & Beverage · Johor            │  │ Sdn. Bhd.                      │  │
│  │                                    │  │ Food & Beverage · Kuala Lumpur │  │
│  └────────────────────────────────────┘  └────────────────────────────────┘  │
│  ┌────────────────────────────────────┐  ┌────────────────────────────────┐  │
│  │ Gemilang Delima Food Sdn. Bhd.     │  │ Pacific Everbright Logistik    │  │
│  │ Food & Beverage · Johor            │  │ PLT                            │  │
│  │                                    │  │ Logistics · Kuala Lumpur       │  │
│  └────────────────────────────────────┘  └────────────────────────────────┘  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

Clicking a card opens that company's page, with its own "Similar companies" panel.

### How it works for the user

```mermaid
sequenceDiagram
    actor User as Portal user
    participant Portal as OneCredit portal
    participant Service as Your service
    participant DB as Postgres + pgvector
    User->>Portal: Opens the page for SYN-001550
    Portal->>Service: Similar companies for SYN-001550
    Service->>DB: Nearest neighbours of SYN-001550
    DB-->>Service: Closest companies
    Service-->>Portal: Similar companies, most similar first
    Portal-->>User: Page with the "Similar companies" panel
    User->>Portal: Clicks Seri Sejahtera Food Sdn. Bhd.
    Portal->>Service: Similar companies for SYN-000539
```

### How the data gets there

```mermaid
flowchart LR
    P[Registry feed<br/>producer] -->|company-events| Q[(RabbitMQ)]
    Q --> C[Your consumer]
    C -->|embed descriptions| E[Embedding model]
    C --> D[(Postgres + pgvector<br/>similarity search)]
    A[Your service] --> D
    W[OneCredit portal] --> A
```

### Notes from the registry team

> The feed merges a few upstream sources and we don't validate what they send. Delivery is at-least-once, and every
> message has an AMQP `message_id`. Each event carries the time the change happened at the source in `updatedAt`. We own
> the `company-events` queue and declare it as durable with no extra arguments. We can't change the format before your
> launch, so please work with what's there.

### What the portal needs

- Recommendations that reflect the latest information we've received about each company.
- No lost companies. If a company is in the feed, users expect to see it on the portal and in other companies'
  recommendations.
- Fast enough to render while the company page loads.

## The task

1. Build a **consumer** that reads the `company-events` queue and stores each company in Postgres, with an embedding
   of its business description in pgvector.
2. Build the **service** the portal calls: given a company, return its most similar companies, with what the portal
   needs to draw each card in the mock above. Run the similarity search in Postgres with pgvector. Matching companies
   on their nature of business is enough for a first version.
3. Write a short `DESIGN.md` covering the decisions you made, the assumptions you made where the scenario was unclear,
   what you would change to handle 10x the volume, and what's left.
4. Optional, up to one hour of your six: go deeper in one area you're strong in. For example: how good the
   recommendations are, latency at a realistic scale, how you'd monitor this in production, or a design for explaining
   a recommendation with an LLM (a design only; no paid APIs). Write it up in `DESIGN.md`.

The rest is your call: the schema, the embedding model, your tests and tooling, and the API, including what the portal
gets back when something goes wrong. Tell us in your own README how to start your consumer and service, and we'll
follow it.

Where the scenario leaves something open, make a reasonable call and write it down. There isn't one right answer.

## Embeddings

Use an open-source embedding model that runs on a laptop CPU. Don't call a paid API. Some options:

- `BAAI/bge-small-en-v1.5`, a small CPU model (384 dimensions), through
  [fastembed](https://qdrant.github.io/fastembed/) or sentence-transformers. The panel in the mock above comes from it.
- A placeholder while you build, such as a hashed bag-of-words vector, as long as you say so in `DESIGN.md`.

## Getting started

Run these commands from the repository root. You need Docker with Compose and [uv](https://docs.astral.sh/uv/),
which installs Python 3.14 if needed. The command blocks use [just](https://github.com/casey/just), with direct
equivalents below.

```bash
just setup
just up
just migrate
```

Start the worker and leave it running. On the first start, FastEmbed downloads the multilingual MiniLM model into
`fastembed_cache` under the system temporary directory. Set `FASTEMBED_CACHE_PATH` to an absolute path if you want a
persistent cache, and use the same value when running integration tests.

```bash
uv run --locked python -m companies.worker
```

Start the API in a second terminal.

```bash
just api
```

In a third terminal, publish the fixture.

```bash
just publish
```

Once `company-events` shows a consumer and zero ready and zero unacknowledged messages in the RabbitMQ management UI,
request up to four cards for `SYN-001550`. If you get a `404` during the first import, wait for the worker to catch up
and try again.

```bash
curl --fail-with-body 'http://127.0.0.1:8000/companies/SYN-001550/similar?limit=4'
```

Each card includes `registration_number`, `name`, `industry`, and `state`. Interactive API docs are available in
[Scalar](http://127.0.0.1:8000/scalar).

### Direct commands without `just`

```bash
uv sync --locked
docker compose up -d --wait
uv run --locked alembic upgrade head
```

Start the worker as above, then run `uv run --locked uvicorn companies.api:app` in a second terminal. In a third
terminal, publish the feed and use the same curl command once ingestion finishes.

```bash
uv run python -m producer.publish
```

### Local ports and cleanup

Postgres uses `postgresql://postgres:postgres@localhost:5432/companies`. RabbitMQ uses port `5672` and its management
UI is at http://localhost:15672 with `guest` / `guest`. If needed, export `POSTGRES_PORT`, `RABBITMQ_PORT`, and
`RABBITMQ_UI_PORT` before starting Compose. Use the same `POSTGRES_PORT` and `RABBITMQ_PORT` in the migration, worker,
API, and publisher terminals. `DATABASE_URL` overrides the database connection and `RABBITMQ_URL` overrides the
broker connection. If port `8000` is occupied, add `--port 8001` to the direct Uvicorn command and use that port in curl.

Stop the worker and API with Ctrl+C. To delete the local queue and database volumes, run `just down` or
`docker compose down -v`. To start with fresh data, follow the startup commands again, including migrations.
`just rollback` rolls back one database migration. Rolling back the initial migration drops the company table.

### Tests

Run `uv run --locked python -m pytest -q` for tests that do not need Compose. The integration tests skip unless their
connection URLs are set. To run the full suite, use a separate Compose project with automatically assigned host ports.

```bash
POSTGRES_PORT=0 RABBITMQ_PORT=0 RABBITMQ_UI_PORT=0 \
  docker compose -p onecredit-tests up -d --wait
export TEST_DATABASE_URL="postgresql://postgres:postgres@$(docker compose -p onecredit-tests port postgres 5432)/companies"
export TEST_RABBITMQ_URL="amqp://guest:guest@$(docker compose -p onecredit-tests port rabbitmq 5672)/"
uv run --locked python -m pytest -q
docker compose -p onecredit-tests down -v
```

## What's already here

```
.
├── data/feed.jsonl        # the registry feed: 2,518 messages about 2,000 synthetic Malaysian companies, duplicates included
├── producer/publish.py    # replays the feed onto the queue
├── docker-compose.yml     # Postgres with pgvector, and RabbitMQ
└── Justfile               # setup, up, publish, down
```

Add whatever else you need (`uv add` for libraries).

## How to submit

1. Click **Use this template** → **Create a new repository**, and make it **public**. Any name is fine.
2. Build your solution in your copy, with `DESIGN.md` at the top level.
3. When you're done, fill in the [submission form](https://docs.google.com/forms/d/e/1FAIpQLSdjCsXP5jFrKrIi4krLVS8Sq0rhM4mLlhycpsAo34sSj4h3FA/viewform) with a link to your repo and your résumé.

If the setup itself is broken (Docker, the services or the data won't start), open a
[Setup problem](https://github.com/OneCreditMY/mle-take-home/issues/new?template=setup-problem.yml) issue. Issues are public, so don't put your
solution or personal details in one.

## Time

Plan for **4 to 6 hours**, within 7 days of creating your copy, and stop at 6 hours. If you run out of time,
write down what you'd do next. A clear, honest "here's what's left" counts in your favour.

## What we look at

- Does it work, and does it keep working?
- How you tested it.
- How you reasoned about your choices and assumptions in `DESIGN.md`.
- How readable your code is.

We don't expect a perfect or complete solution.

## Using AI tools

Go ahead. Tell us in `DESIGN.md` which tools you used and for what. In the interview we'll ask you
to walk us through your code, so make sure you understand everything you submit.

## After you submit

- We review your work as soon as we can.
- If we move forward, the next interview is a walkthrough of your code with us.
- Either way, you'll get written feedback by email.
- Your repo stays yours. Keep it public as a portfolio piece if you like.

## About the data

Everything in this repo is synthetic. We generated it for this assessment.

- The companies, names, registration numbers, descriptions and events are made up. Any resemblance to a real company
  or person is a coincidence.
- No OneCredit customer data, credit data or production data is in this repo, and none was used to create it.
- Registration numbers use a made-up `SYN-` format, so they can't be mistaken for real Companies Commission of
  Malaysia (SSM) numbers.
- Your copy is public, so keep secrets, credentials and personal data out of it.
