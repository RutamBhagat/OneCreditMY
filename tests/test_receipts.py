"""Exercise replay deduplication through the worker, RabbitMQ and Postgres."""

import asyncio
import hashlib
import json
import os
from array import array
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from psycopg.errors import CheckViolation
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from companies import worker
from companies.db import database_url
from companies.models import Company, EventReceipt
from producer.publish import publish


@pytest.mark.parametrize("failure_mode", ["company_write", "inference"])
def test_failed_delivery_requeues_and_committed_replay_skips_inference(monkeypatch, failure_mode):
    test_database_url = os.environ.get("TEST_DATABASE_URL")
    rabbitmq_url = os.environ.get("TEST_RABBITMQ_URL")
    if not test_database_url or not rabbitmq_url:
        pytest.skip("Set TEST_DATABASE_URL and TEST_RABBITMQ_URL for an isolated Compose project")
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("RABBITMQ_URL", rabbitmq_url)
    command.upgrade(Config("alembic.ini"), "head")

    inference_inputs = []
    redeliveries = []
    store_message = worker.store_message

    async def record_delivery(session, message, model):
        redeliveries.append((message.message_id, message.redelivered))
        await store_message(session, message, model)

    class RecordingEmbedding:
        def __init__(self, _model_name):
            pass

        def embed(self, documents):
            inference_inputs.extend(documents)
            if failure_mode == "inference" and len(inference_inputs) == 1:
                raise RuntimeError("Test inference failed")
            yield array("f", [1.0] * 384)

    monkeypatch.setattr(worker, "TextEmbedding", RecordingEmbedding)
    monkeypatch.setattr(worker, "store_message", record_delivery)
    registration = f"REPLAY-{uuid4().hex.upper()}"
    marker = f"{registration}-MARKER"
    event = {"registrationNumber": registration, "updatedAt": "2026-08-14T01:00:00Z"}
    body = json.dumps(
        {
            **event,
            "type": "company-upserted",
            "data": {"companyName": "Replay Café", "natureOfBusiness": "Operating coffee shops"},
        }
    )
    marker_body = json.dumps({**event, "type": "company-struck-off", "registrationNumber": marker})
    message_id, marker_id = (hashlib.sha256(line.encode()).hexdigest() for line in (body, marker_body))
    constraint_name = "test_reject_company_write"

    async def check_replay():
        engine = create_async_engine(database_url(), isolation_level="AUTOCOMMIT")
        consumer = asyncio.create_task(worker.consume())
        try:
            async with engine.connect() as db:
                async def wait_for_company(number):
                    statement = select(Company.registration_number).where(Company.registration_number == number)
                    async with asyncio.timeout(15):
                        while await db.scalar(statement) is None:
                            if consumer.done():
                                consumer.result()
                            await asyncio.sleep(0.05)

                async def counts() -> tuple[int, int]:
                    companies = await db.scalars(
                        select(func.count()).select_from(Company).where(Company.registration_number != marker)
                    )
                    receipts = await db.scalars(
                        select(func.count()).select_from(EventReceipt).where(EventReceipt.message_id != marker_id)
                    )
                    return companies.one(), receipts.one()

                initial_companies, initial_receipts = await counts()
                reject_write = failure_mode == "company_write"
                if reject_write:
                    await db.exec_driver_sql(
                        f"ALTER TABLE companies ADD CONSTRAINT {constraint_name} "
                        f"CHECK (registration_number <> '{registration}') NOT VALID"
                    )
                try:
                    await publish([body], rabbitmq_url)
                    if reject_write:
                        with pytest.raises(IntegrityError) as failure:
                            await asyncio.wait_for(consumer, timeout=15)
                        cause = failure.value.orig
                        assert isinstance(cause, CheckViolation)
                        assert cause.diag.constraint_name == constraint_name
                    else:
                        with pytest.raises(RuntimeError, match="^Test inference failed$"):
                            await asyncio.wait_for(consumer, timeout=15)
                    assert await counts() == (initial_companies, initial_receipts)
                    assert redeliveries == [(message_id, False)]
                finally:
                    if reject_write:
                        await db.exec_driver_sql(f"ALTER TABLE companies DROP CONSTRAINT {constraint_name}")

                # The only copy was published before the processing failure.
                consumer = asyncio.create_task(worker.consume())
                await wait_for_company(registration)
                before = await counts()
                await publish([body, marker_body], rabbitmq_url)
                # The sequential worker commits the marker only after handling the replay.
                await wait_for_company(marker)
                assert redeliveries == [(message_id, False), (message_id, True), (message_id, False), (marker_id, False)]
                assert inference_inputs == ["Operating coffee shops"] * 2
                assert before == await counts() == (initial_companies + 1, initial_receipts + 1)
                receipt = await db.execute(
                    select(EventReceipt.raw_body, EventReceipt.rejection_reason).where(
                        EventReceipt.message_id == message_id
                    )
                )
                assert receipt.one() == (None, None)
        finally:
            consumer.cancel()
            try:
                await asyncio.gather(consumer, return_exceptions=True)
            finally:
                async with engine.begin() as db:
                    await db.execute(delete(EventReceipt).where(EventReceipt.message_id.in_([message_id, marker_id])))
                    await db.execute(delete(Company).where(Company.registration_number.in_([registration, marker])))
                await engine.dispose()

    asyncio.run(check_replay())
