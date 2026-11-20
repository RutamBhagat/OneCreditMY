"""Consume company events in delivery order and keep the latest registry version."""

import asyncio
import os

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from fastembed import TextEmbedding
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from companies.db import database_url
from companies.events import CompanyUpserted, InvalidEvent, parse_event
from companies.models import Company, EventReceipt
from producer.publish import QUEUE

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


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


async def store_message(session: AsyncSession, message: AbstractIncomingMessage, model: TextEmbedding) -> None:
    if message.message_id is None:
        raise ValueError("Missing AMQP message_id")
    receipt = (
        insert(EventReceipt)
        .values(message_id=message.message_id)
        .on_conflict_do_nothing(index_elements=[EventReceipt.message_id])
        .returning(EventReceipt.message_id)
    )
    if (await session.execute(receipt)).scalar_one_or_none() is None:
        return

    try:
        event = parse_event(message.body)
    except InvalidEvent as error:
        await session.execute(
            update(EventReceipt)
            .where(EventReceipt.message_id == message.message_id)
            .values(raw_body=message.body, rejection_reason=str(error))
        )
        return

    values = {
        "registration_number": event.registration_number,
        "updated_at": event.updated_at,
        "active": False,
        "name": None,
        "industry": None,
        "state": None,
        "description": None,
        "embedding": None,
    }
    if isinstance(event, CompanyUpserted):
        data = event.data
        name, industry, description = data.name, data.industry, data.description
        embedding_text = next((text for text in (description, industry) if text and text.strip()), name)
        embedding = await asyncio.to_thread(next, iter(model.embed([embedding_text])))
        values.update(
            active=True,
            name=name,
            industry=industry,
            state=data.state,
            description=description,
            embedding=embedding.tolist(),
        )
    await session.execute(company_upsert(values))


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
                    async with sessions.begin() as session:
                        await store_message(session, message, model)
                    await message.ack()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(consume())
