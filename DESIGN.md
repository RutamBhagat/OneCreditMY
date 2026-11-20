# Company recommendations

## Embeddings and retrieval

The worker loads `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` once through FastEmbed. The quantized ONNX model runs locally on a CPU and produces 384-dimensional vectors. The multilingual model handles English and Malay descriptions in the feed.

The worker embeds the first nonblank value from `natureOfBusiness`, `industry`, and `companyName`, in that order. It checks for whitespace when choosing the input and stores the original card fields and description. The name keeps the company eligible for recommendations but may say little about the business. A newer snapshot with a description replaces the fallback embedding through the same atomic upsert.

SQLAlchemy maps the company table and builds one correlated pgvector query. FastAPI uses the query to find the active source and rank other active companies by cosine distance. A partial HNSW index covers active rows with embeddings. The query sorts returned candidates by distance and registration number. It can return different candidates when several companies tie at the limit.

The API returns `404` for an unknown or inactive source and `200 []` for an active source with no candidates. It returns `503` if it cannot connect to PostgreSQL or SQLAlchemy invalidates a disconnected connection. It passes other query errors to the FastAPI error handler.

## Database access and schema revisions

The API and worker use Psycopg as the PostgreSQL driver. The worker uses an async SQLAlchemy session for each event transaction.

Alembic runs `upgrade head` and `downgrade -1`. The initial upgrade can adopt the schema from the old init command and keep the existing rows. The initial downgrade drops the company table and its rows. It leaves the shared pgvector extension installed. The `python -m companies.db init` command still upgrades to head.

## Registry state and delivery

The worker trims and uppercases string registrations. It quarantines numeric registrations because converting them to strings could match an existing key.

The worker accepts ISO timestamps and 13-digit JSON integers. It interprets ISO timestamps without an offset as Malaysia time (UTC+08:00) and preserves explicit offsets. It parses the integers as Unix milliseconds and keeps millisecond precision. Event ordering is independent of the PostgreSQL session time zone.

An upsert is a full snapshot. If the event omits an optional field, the worker clears the previous value. One PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` statement handles upserts and strike-offs. Its update predicate requires the source `updatedAt` to be strictly newer than the stored `updated_at`. Stale deliveries and deliveries with the same timestamp leave all company fields and the embedding unchanged.

A strike-off clears the card and embedding fields and leaves an inactive tombstone, even before the first upsert. Only an event with a newer registry timestamp can reactivate the company.

The worker inserts the AMQP `message_id` as the receipt primary key before decoding or inference. The receipt insert and the company write or rejection share a transaction. PostgreSQL returns a row only if it inserts the receipt. A duplicate delivery skips parsing and inference. The worker acknowledges each delivery after the transaction commits.

The worker decodes the body as JSON and uses a Pydantic discriminated union to validate the selected event schema. It checks text for NUL characters and invalid Unicode before writing to PostgreSQL. For a rejected delivery, the worker stores the original body bytes and the validation reason on the reserved receipt.

Migration `0002_event_receipts` creates an empty receipt table and keeps existing companies. The first replay of an event processed before the migration creates its receipt. Registry timestamps still govern company updates.

A database or inference failure rolls back the transaction and exits the worker. When the connection closes, RabbitMQ requeues the unacknowledged delivery for a later restart. If the acknowledgement fails after commit, the receipt prevents the worker from repeating the write or inference.

## Full-feed verification

On a fresh Compose stack, 2,518 deliveries produced 2,450 receipts, including 32 rejections. The database contained 2,000 companies and had embeddings for all 1,992 active companies. The worker used industry for 35 active companies and company name for four.

Replaying the feed preserved all counts and the hash of all company columns in registration order. RabbitMQ showed zero ready and zero unacknowledged messages after each run. Integration tests also cover stale events, quarantines, rollback, and broker redelivery.

`SYN-001550` returned four café or coffee-shop matches. The local HTTP benchmark used an Apple M5 with 16 GiB RAM. After five warmups, 50 sequential requests measured 5.02 ms p95.

## Hosted demo

Uncloud runs the API, worker, pgvector, and RabbitMQ on one ARM64 VM. Caddy serves the API over HTTPS. PostgreSQL and RabbitMQ use separate persistent volumes and have no published ports. A VM outage takes the demo offline. If the VM loses its disk, restore the data or replay the synthetic fixture.

The API, worker, migration, and feed replay use one image. The build uses digest-pinned Python and uv images to install production dependencies from `uv.lock`. The runtime user has a writable home directory for the Hugging Face download client cache. The client writes the cache on first startup.

The pipeline accepts an API-only dispatch for presentation releases. Uncloud deploys only `onecredit-api` and reuses the existing HTTPS route.

## Browser demo

For the optional browser demo, the recommendation query also returns details for the source company. Jinja2 renders a full page for direct loads, ordinary links, and htmx history navigation. With JavaScript available, htmx boosts links. FastAPI serves the compiled Tailwind stylesheet and pinned htmx script from the application image.

The demo uses business-register conventions to present synthetic company records to reviewers. The design-taste settings are variance 4, motion 1, and density 5. The profile uses labeled fields, and a four-row list shows companies in recommendation order. One green accent marks links and focus in both system color schemes. Chromium checks verified the responsive layout, focus, and contrast.

## At 10× volume

At roughly 20,000 companies, monitor queue age and API latency separately. If inference limits ingestion, batch embeddings and keep the commit-before-acknowledgement order for each delivery. If connection setup dominates HTTP latency, use a bounded API connection pool. Tune HNSW only if query latency warrants it.

The checks use synthetic data and a small HTTP sample. Recommendation relevance still needs human labels, especially for companies with sparse descriptions. In production, supervise worker restarts and set alerts for queue age and rejection rate.
