"""Consume registry upserts with a business description from company-events."""

import asyncio
import json
import os
from datetime import datetime

import aio_pika
from fastembed import TextEmbedding
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from companies.db import database_url
from companies.models import Company
from producer.publish import QUEUE

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


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
                    if event["type"] != "company-upserted":
                        raise ValueError("Expected a company-upserted event")
                    data = event["data"]
                    description = data["natureOfBusiness"]
                    embedding = await asyncio.to_thread(next, iter(model.embed([description])))

                    company = Company(
                        registration_number=event["registrationNumber"].strip().upper(),
                        updated_at=datetime.fromisoformat(event["updatedAt"]),
                        active=True,
                        name=data["companyName"],
                        industry=data.get("industry"),
                        state=data.get("state"),
                        description=description,
                        embedding=embedding.tolist(),
                    )
                    async with sessions.begin() as session:
                        session.add(company)
                    await message.ack()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(consume())
