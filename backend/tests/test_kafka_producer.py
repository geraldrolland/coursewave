"""Tests for the Kafka notification producer."""

from collections.abc import Iterator
from typing import Any

import pytest

import app.services.notification.pub_sub.kafka.kafka_producer as kafka_module


@pytest.fixture(autouse=True)
def reset_kafka_singleton() -> Iterator[None]:
    kafka_module.KafkaProducer._instance = None
    yield
    kafka_module.KafkaProducer._instance = None


def test_kafka_producer_is_a_singleton() -> None:
    first = kafka_module.KafkaProducer()
    second = kafka_module.KafkaProducer()

    assert first is second


def test_kafka_producer_sends_payload_as_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFuture:
        def __init__(self) -> None:
            self.timeout: int | None = None

        def get(self, timeout: int) -> None:
            self.timeout = timeout

    class FakeKafkaClient:
        instances: list["FakeKafkaClient"] = []

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.sent: list[tuple[str, dict[str, Any]]] = []
            self.futures: list[FakeFuture] = []
            self.instances.append(self)

        def send(
            self,
            topic: str,
            payload: dict[str, Any],
        ) -> FakeFuture:
            future = FakeFuture()
            self.sent.append((topic, payload))
            self.futures.append(future)
            return future

    monkeypatch.setattr(kafka_module, "KafkaClient", FakeKafkaClient)

    producer = kafka_module.KafkaProducer()
    producer.send("notification", {"message": "hello"})
    producer.send("notification", {"message": "again"})

    assert len(FakeKafkaClient.instances) == 1
    client = FakeKafkaClient.instances[0]
    assert client.kwargs["bootstrap_servers"] == "localhost:9092"
    assert client.kwargs["acks"] == "all"
    assert client.kwargs["retries"] == 3
    assert client.kwargs["value_serializer"]({"message": "hello"}) == (
        b'{"message": "hello"}'
    )
    assert client.sent == [
        ("notification", {"message": "hello"}),
        ("notification", {"message": "again"}),
    ]
    assert [future.timeout for future in client.futures] == [
        kafka_module.KAFKA_SEND_TIMEOUT_SECONDS,
        kafka_module.KAFKA_SEND_TIMEOUT_SECONDS,
    ]
