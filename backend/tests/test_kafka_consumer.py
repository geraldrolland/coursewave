"""Tests for the Kafka-to-WebSocket notification consumer."""

import asyncio
import json
from typing import Any

from app.services.notification.pub_sub.kafka.kafka_consumer import KafkaConsumer


class FakeRecord:
    """Minimal Kafka record double."""

    def __init__(self, value: Any) -> None:
        self.value = value


class FakeKafkaConsumer:
    """Return prepared records and record commits."""

    def __init__(self, records: list[FakeRecord]) -> None:
        self.records = records
        self.commits = 0
        self.closed = False

    def poll(self, timeout_ms: int) -> list[FakeRecord]:
        return self.records

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


class FakeWebSocketManager:
    """Capture notifications sent to WebSocket users."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, id: str, message: dict[str, Any]) -> None:
        self.sent.append((id, message))


def test_kafka_consumer_dispatches_and_commits() -> None:
    records = [
        FakeRecord(
            json.dumps(
                {
                    "recipient_id": "user-1",
                    "message": {"title": "Hello"},
                }
            ).encode("utf-8")
        )
    ]
    client = FakeKafkaConsumer(records)
    manager = FakeWebSocketManager()
    consumer = KafkaConsumer(
        consumer=client,  # type: ignore[arg-type]
        manager=manager,  # type: ignore[arg-type]
        topic="notification",
    )

    async def run_once() -> None:
        await consumer.consume_once()
        await consumer.aclose()

    asyncio.run(run_once())

    assert manager.sent == [("user-1", {"title": "Hello"})]
    assert client.commits == 1
    assert client.closed is True


def test_kafka_consumer_commits_invalid_payload_without_sending() -> None:
    client = FakeKafkaConsumer([FakeRecord(b"not-json")])
    manager = FakeWebSocketManager()
    consumer = KafkaConsumer(
        consumer=client,  # type: ignore[arg-type]
        manager=manager,  # type: ignore[arg-type]
        topic="notification",
    )

    async def run_once() -> None:
        await consumer.consume_once()
        await consumer.aclose()

    asyncio.run(run_once())

    assert manager.sent == []
    assert client.commits == 1


def test_kafka_consumer_skips_payload_without_recipient() -> None:
    client = FakeKafkaConsumer(
        [FakeRecord(json.dumps({"message": {"title": "Hello"}}))]
    )
    manager = FakeWebSocketManager()
    consumer = KafkaConsumer(
        consumer=client,  # type: ignore[arg-type]
        manager=manager,  # type: ignore[arg-type]
        topic="notification",
    )

    async def run_once() -> None:
        await consumer.consume_once()
        await consumer.aclose()

    asyncio.run(run_once())

    assert manager.sent == []
    assert client.commits == 1
