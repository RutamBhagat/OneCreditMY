# Company recommendations

## Embeddings and retrieval

The worker loads `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` once through FastEmbed. The quantized ONNX model runs locally on a CPU and produces 384-dimensional vectors. I chose a multilingual model because the feed contains English and Malay descriptions. [Model artifact](https://huggingface.co/Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q)

The worker embeds the first nonblank value from `natureOfBusiness`, `industry`, and `companyName`, in that order. Using the company name keeps the company eligible for recommendations. The name may say little about the business. A newer snapshot with a description replaces the fallback vector.

FastAPI runs one PostgreSQL query to find the active source and rank other active companies by cosine distance. A partial HNSW index covers active rows with embeddings. The query sorts returned candidates by distance and registration number. The query can return different candidates when several companies tie at the limit. An unknown or inactive source returns `404`. An active source with no candidates returns `200 []`. [API implementation](companies/api.py)

## Registry state and delivery

The worker trims and uppercases string registrations. It quarantines numeric registrations because converting them to strings could match an existing key. It accepts ISO timestamps and 13-digit Unix milliseconds. An ISO timestamp without an offset means Malaysia time, UTC+08:00.

An upsert is a full snapshot. If the event omits an optional field, the worker clears the previous value. PostgreSQL replaces a company only when the incoming `updatedAt` is newer. If timestamps tie, PostgreSQL keeps the first event it committed. A strike-off clears the card and embedding and leaves an inactive tombstone, even if it arrives before the first upsert. Only a newer event can reactivate the company.

The worker reserves the AMQP `message_id` in the same transaction as the update or rejection. It skips parsing and inference for duplicate deliveries. For an invalid delivery, the worker stores the original body bytes and the validation reason in the receipt. The worker acknowledges the delivery after the transaction commits.

A database or inference failure rolls back the transaction and exits the worker. RabbitMQ can redeliver the unacknowledged message after restart. The receipt prevents another write if the acknowledgement fails after commit. [Worker implementation](companies/worker.py) · [Recovery tests](tests/test_receipts.py)

## Full-feed verification

On a fresh Compose stack, the 2,518 deliveries produced 2,450 receipts, including 32 rejections. The database contained 2,000 companies, with embeddings for all 1,992 active companies. The worker used industry for 35 active companies and company name for four. Replaying the feed preserved all counts and the hash of all company columns in registration order. RabbitMQ showed zero ready and zero unacknowledged messages after each run. The integration tests also cover stale events, quarantines, rollback, and broker redelivery. [Setup and tests](README.md#tests)

`SYN-001550` returned four café or coffee-shop matches. On an Apple M5 with 16 GiB RAM, 50 sequential local HTTP requests (after five warmups) measured 5.02 ms p95.

## Hosted demo

Uncloud runs the API, worker, pgvector and RabbitMQ on one ARM64 VM. Caddy serves the API over HTTPS. PostgreSQL and RabbitMQ use separate persistent volumes and have no published ports. A VM outage takes the demo offline, and losing its disk would require restoring the data or replaying the synthetic fixture. [Deployment instructions](README.md#hosted-api-demo)

The optional browser demo projects the source company's details in the same recommendation query and renders a Jinja2 page. Links work as ordinary page loads and htmx boosts them when JavaScript is available. FastAPI serves the compiled Tailwind stylesheet and the pinned htmx script alongside the page.

## At 10× volume

At roughly 20,000 companies, monitor queue age and API latency separately. If inference limits ingestion, batch embeddings. For each delivery, commit the transaction before sending the acknowledgement. If connection setup dominates HTTP latency, use a bounded API connection pool. Tune HNSW only if query latency warrants it.

The checks use synthetic data and a small HTTP sample. Recommendation relevance still needs human labels, especially for sparse companies. The worker needs a restart after a transient failure. In production, supervise restarts and set alerts for queue age and rejection rate.

## AI tools

I used OpenAI Codex to help implement and test the service and draft documentation. I used web search and Context7 to check library APIs and pgvector indexing.
