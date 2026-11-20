"""Run against an isolated Compose project with TEST_DATABASE_URL and TEST_RABBITMQ_URL set."""

import json
import os
import re
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, delete, func, select

from companies.db import database_url
from companies.models import Company


@contextmanager
def service(log_path, env, *args):
    with (
        log_path.open("w") as log,
        subprocess.Popen([sys.executable, "-m", *args], env=env, stdout=log, stderr=subprocess.STDOUT) as process,
    ):
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


def wait_for(check, process, log_path, *, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.poll() is None:
        if result := check():
            return result
        time.sleep(0.1)
    pytest.fail(f"Service did not reach the expected state:\n{log_path.read_text()}")


def test_ranked_cards_from_queue(tmp_path, monkeypatch):
    test_database_url = os.environ.get("TEST_DATABASE_URL")
    rabbitmq_url = os.environ.get("TEST_RABBITMQ_URL")
    if not test_database_url or not rabbitmq_url:
        pytest.skip("Set TEST_DATABASE_URL and TEST_RABBITMQ_URL for an isolated Compose project")

    monkeypatch.setenv("DATABASE_URL", test_database_url)
    env = {**os.environ, "RABBITMQ_URL": rabbitmq_url}
    for _ in range(2):
        subprocess.run([sys.executable, "-m", "companies.db", "init"], env=env, check=True, timeout=15)

    prefix = f"SMOKE-{uuid4().hex.upper()}"
    source, cafe, unrelated = (f"{prefix}-{suffix}" for suffix in ("SOURCE", "NEIGHBOR", "METAL"))
    registrations = [source, cafe, unrelated]

    def event(registration, name, description, industry=None, state=None):
        return {
            "type": "company-upserted",
            "registrationNumber": registration,
            "updatedAt": "2026-08-14T09:00:00+08:00",
            "data": {"companyName": name, "natureOfBusiness": description, "industry": industry, "state": state},
        }

    def publish(*events):
        path = tmp_path / "events.jsonl"
        path.write_text("\n".join(map(json.dumps, events)) + "\n", encoding="utf-8")
        subprocess.run(
            [sys.executable, "-m", "producer.publish", "--file", str(path)], env=env, check=True, timeout=15
        )

    worker_log, api_log = tmp_path / "worker.log", tmp_path / "api.log"
    endpoint = f"/companies/{source}/similar"
    with ExitStack() as stack:
        engine = create_engine(database_url(), isolation_level="AUTOCOMMIT")
        stack.callback(engine.dispose)
        db = stack.enter_context(engine.connect())
        assert db.scalar(select(Company.registration_number).limit(1)) is None, "Use an empty test database"
        stack.callback(db.execute, delete(Company).where(Company.registration_number.in_(registrations)))
        worker = stack.enter_context(service(worker_log, env, "companies.worker"))
        api = stack.enter_context(service(api_log, env, "uvicorn", "companies.api:app", "--port", "0"))
        address = wait_for(
            lambda: re.search(r"http://127\.0\.0\.1:\d+", api_log.read_text()), api, api_log, timeout=15
        )

        def committed(count):
            persisted = db.scalar(
                select(func.count()).select_from(Company).where(
                    Company.registration_number.in_(registrations), Company.embedding.is_not(None)
                )
            )
            return persisted == count

        with httpx.Client(base_url=address.group()) as client:
            scalar = client.get("/scalar")
            assert scalar.status_code == 200
            assert scalar.headers["content-type"].startswith("text/html")
            assert "@scalar/api-reference" in scalar.text and "/openapi.json" in scalar.text
            paths = client.get("/openapi.json").json()["paths"]
            assert "/companies/{registration_number}/similar" in paths
            assert "/scalar" not in paths

            assert client.get(endpoint).status_code == 404
            for limit in (0, 51, "invalid"):
                assert client.get(endpoint, params={"limit": limit}).status_code == 422

            publish(event(source, "Valley Café", "Operating cafes and coffee shops in the Klang Valley"))
            wait_for(lambda: committed(1), worker, worker_log, timeout=180)
            response = client.get(endpoint)
            assert response.status_code == 200
            assert response.json() == []

            publish(
                event(
                    cafe, "Kedai Kopi", "Mengendalikan kafe dan kedai kopi di Lembah Klang", "Food & Beverage", "Johor"
                ),
                event(unrelated, "Metalworks", "Precision machining of metal parts for electronics"),
            )
            wait_for(lambda: committed(3), worker, worker_log, timeout=30)
            response = client.get(endpoint)
            assert response.status_code == 200
            cards = response.json()
            assert cards == [
                {"registration_number": cafe, "name": "Kedai Kopi", "industry": "Food & Beverage", "state": "Johor"},
                {"registration_number": unrelated, "name": "Metalworks", "industry": None, "state": None},
            ]
            assert client.get(endpoint, params={"limit": 1}).json() == cards[:1]


def test_registry_time_controls_updates_and_eligibility(tmp_path, monkeypatch):
    test_database_url = os.environ.get("TEST_DATABASE_URL")
    rabbitmq_url = os.environ.get("TEST_RABBITMQ_URL")
    if not test_database_url or not rabbitmq_url:
        pytest.skip("Set TEST_DATABASE_URL and TEST_RABBITMQ_URL for an isolated Compose project")

    monkeypatch.setenv("DATABASE_URL", test_database_url)
    env = {**os.environ, "RABBITMQ_URL": rabbitmq_url}
    subprocess.run([sys.executable, "-m", "companies.db", "init"], env=env, check=True, timeout=15)

    prefix = f"ORDER-{uuid4().hex.upper()}"
    source, candidate = (f"{prefix}-{suffix}" for suffix in ("SOURCE", "CANDIDATE"))
    registrations = [source, candidate]

    def upsert(registration, updated_at, name, description, industry, state):
        return {
            "type": "company-upserted",
            "registrationNumber": registration,
            "updatedAt": updated_at,
            "data": {
                "companyName": name,
                "natureOfBusiness": description,
                "industry": industry,
                "state": state,
            },
        }

    def struck_off(registration, updated_at):
        return {
            "type": "company-struck-off",
            "registrationNumber": registration,
            "updatedAt": updated_at,
        }

    def publish(filename, *events):
        path = tmp_path / filename
        path.write_text("\n".join(map(json.dumps, events)) + "\n", encoding="utf-8")
        subprocess.run(
            [sys.executable, "-m", "producer.publish", "--file", str(path)], env=env, check=True, timeout=15
        )

    source_event = upsert(
        source,
        "2026-08-14T09:00:00+08:00",
        "Portal Source",
        "Operating cafes and coffee shops in the Klang Valley",
        "Food & Beverage",
        "Selangor",
    )
    newer = upsert(
        candidate,
        "2026-08-14T10:00:00+08:00",
        "Current Candidate",
        "Roasting coffee and operating neighbourhood cafes",
        "Food & Beverage",
        "Johor",
    )
    older = upsert(
        candidate,
        "2026-08-14T09:30:00+08:00",
        "Stale Candidate",
        "Precision machining of metal components",
        "Manufacturing",
        "Perak",
    )

    worker_log, api_log = tmp_path / "worker-ordering.log", tmp_path / "api-ordering.log"
    with ExitStack() as stack:
        engine = create_engine(database_url(), isolation_level="AUTOCOMMIT")
        stack.callback(engine.dispose)
        db = stack.enter_context(engine.connect())
        assert db.scalar(select(Company.registration_number).limit(1)) is None, "Use an empty test database"
        stack.callback(db.execute, delete(Company).where(Company.registration_number.in_(registrations)))
        worker = stack.enter_context(service(worker_log, env, "companies.worker"))
        api = stack.enter_context(service(api_log, env, "uvicorn", "companies.api:app", "--port", "0"))
        address = wait_for(
            lambda: re.search(r"http://127\.0\.0\.1:\d+", api_log.read_text()), api, api_log, timeout=15
        )

        def company(registration):
            db.rollback()
            return db.execute(
                select(*Company.__table__.c).where(Company.registration_number == registration)
            ).mappings().one_or_none()

        def has_version(registration, updated_at, active):
            row = company(registration)
            return row is not None and row.updated_at == datetime.fromisoformat(updated_at) and row.active is active

        publish("initial.jsonl", source_event, newer)
        wait_for(
            lambda: has_version(candidate, "2026-08-14T10:00:00+08:00", True),
            worker,
            worker_log,
            timeout=180,
        )
        persisted = company(candidate)
        assert persisted is not None
        expected = {
            "registration_number": persisted.registration_number,
            "updated_at": persisted.updated_at,
            "active": persisted.active,
            "name": persisted.name,
            "industry": persisted.industry,
            "state": persisted.state,
            "description": persisted.description,
            "embedding": tuple(persisted.embedding),
        }

        equal_time_conflict = upsert(
            candidate,
            newer["updatedAt"],
            "Conflicting Candidate",
            "Freight forwarding and warehouse logistics",
            "Logistics",
            "Penang",
        )
        replay_marker = upsert(
            source,
            "2026-08-14T10:15:00+08:00",
            "Portal Source",
            "Operating cafes and coffee shops in the Klang Valley",
            "Food & Beverage",
            "Selangor",
        )
        publish("stale-and-replay.jsonl", older, newer, equal_time_conflict, replay_marker)
        wait_for(
            lambda: has_version(source, "2026-08-14T10:15:00+08:00", True), worker, worker_log, timeout=30
        )
        assert worker.poll() is None, worker_log.read_text()
        persisted = company(candidate)
        assert persisted is not None
        assert {
            "registration_number": persisted.registration_number,
            "updated_at": persisted.updated_at,
            "active": persisted.active,
            "name": persisted.name,
            "industry": persisted.industry,
            "state": persisted.state,
            "description": persisted.description,
            "embedding": tuple(persisted.embedding),
        } == expected

        with httpx.Client(base_url=address.group()) as client:
            source_endpoint = f"/companies/{source}/similar"
            candidate_endpoint = f"/companies/{candidate}/similar"
            assert [card["registration_number"] for card in client.get(source_endpoint).json()] == [candidate]

            publish(
                "candidate-struck.jsonl",
                struck_off(candidate, "2026-08-14T11:00:00+08:00"),
                upsert(
                    candidate,
                    "2026-08-14T10:30:00+08:00",
                    "Stale Reactivation",
                    "Precision machining of metal components",
                    "Manufacturing",
                    "Perak",
                ),
            )
            wait_for(
                lambda: has_version(candidate, "2026-08-14T11:00:00+08:00", False),
                worker,
                worker_log,
                timeout=30,
            )
            assert client.get(source_endpoint).json() == []

            publish("source-struck.jsonl", struck_off(source, "2026-08-14T11:30:00+08:00"))
            wait_for(
                lambda: has_version(source, "2026-08-14T11:30:00+08:00", False), worker, worker_log, timeout=30
            )
            assert client.get(source_endpoint).status_code == 404
            assert has_version(candidate, "2026-08-14T11:00:00+08:00", False)
            assert client.get(candidate_endpoint).status_code == 404

            publish(
                "reactivated.jsonl",
                upsert(
                    source,
                    "2026-08-14T12:00:00+08:00",
                    "Portal Source Reactivated",
                    "Operating cafes and coffee shops in the Klang Valley",
                    "Food & Beverage",
                    "Selangor",
                ),
                upsert(
                    candidate,
                    "2026-08-14T12:00:00+08:00",
                    "Candidate Reactivated",
                    "Roasting coffee and operating neighbourhood cafes",
                    "Food & Beverage",
                    "Johor",
                ),
            )
            wait_for(
                lambda: has_version(candidate, "2026-08-14T12:00:00+08:00", True), worker, worker_log, timeout=30
            )
            assert client.get(candidate_endpoint).status_code == 200
            assert [card["registration_number"] for card in client.get(source_endpoint).json()] == [candidate]
