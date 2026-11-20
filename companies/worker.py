"""Consume company events in delivery order and keep the latest registry version."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta, timezone

import aio_pika
from fastembed import TextEmbedding
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from companies.db import database_url
from companies.models import Company
from producer.publish import QUEUE

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MALAYSIA_TIME = timezone(timedelta(hours=8))
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def parse_registry_timestamp(value: str | int) -> datetime:
    if isinstance(value, int):
        if not 10**12 <= value < 10**13:
            raise ValueError("Unix timestamps must be 13-digit milliseconds")
        return UNIX_EPOCH + timedelta(milliseconds=value)

    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=MALAYSIA_TIME)
    return timestamp


def company_upsert(values: dict):
    statement = insert(Company).values(**values)
    return statement.on_conflict_do_update(
        index_elements=[Company.registration_number],
        set_={
            "updated_at": statement.excluded.updated_at,
            "active": statement.excluded.active,
            "name": statement.excluded.name,
            "industry": statement.excluded.industry,
            "state": statement.excluded.state,
            "description": statement.excluded.description,
            "embedding": statement.excluded.embedding,
        },
        where=statement.excluded.updated_at > Company.updated_at,
    )


async def consume() -> None:
    model = await asyncio.to_thread(TextEmbedding, MODEL_NAME)
    port = os.environ.get("RABBITMQ_PORT", "5672")
    broker_url = os.environ.get("RABBITMQ_URL", f"amqp://guest:guest@localhost:{port}/")

    engine = create_async_engine(database_url())
    sessions = async_sessionmaker(engine)
    try:
        async with await aio_pika.connect(broker_url) as broker:
            channel = await broker.channel()
            await channel.set_qos(prefetch_count=1)
            queue = await channel.declare_queue(QUEUE, durable=True)

            async with queue.iterator() as messages:
                async for message in messages:
                    event = json.loads(message.body)
                    values = {
                        "registration_number": event["registrationNumber"].strip().upper(),
                        "updated_at": parse_registry_timestamp(event["updatedAt"]),
                        "active": False,
                        "name": None,
                        "industry": None,
                        "state": None,
                        "description": None,
                        "embedding": None,
                    }
                    if event["type"] == "company-upserted":
                        data = event["data"]
                        description = data["natureOfBusiness"]
                        embedding = await asyncio.to_thread(next, iter(model.embed([description])))
                        values.update(
                            active=True,
                            name=data["companyName"],
                            industry=data.get("industry"),
                            state=data.get("state"),
                            description=description,
                            embedding=embedding.tolist(),
                        )
                    elif event["type"] != "company-struck-off":
                        raise ValueError(f"Unsupported event type: {event['type']}")

                    async with sessions.begin() as session:
                        await session.execute(company_upsert(values))
                    await message.ack()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(consume())
