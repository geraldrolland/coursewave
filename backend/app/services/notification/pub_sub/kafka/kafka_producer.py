"""Kafka producer singleton used by notification publishers."""

import json
import threading
from typing import Any

from kafka import KafkaProducer as KafkaClient

from app.base_config import get_settings

KAFKA_SEND_TIMEOUT_SECONDS = 10


class KafkaProducer:
    """Create and reuse one Kafka producer per process."""

    _instance: "KafkaProducer | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "KafkaProducer":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._client = None
                    instance._client_lock = threading.Lock()
                    instance._bootstrap_servers = (
                        get_settings().kafka_bootstrap_servers
                    )
                    cls._instance = instance
        return cls._instance

    def send(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish a dictionary payload to a Kafka topic."""

        future = self._get_client().send(topic, payload)
        future.get(timeout=KAFKA_SEND_TIMEOUT_SECONDS)

    def _get_client(self) -> KafkaClient:
        """Create the underlying Kafka client on first use."""

        with self._client_lock:
            if self._client is None:
                self._client = KafkaClient(
                    bootstrap_servers=self._bootstrap_servers,
                    value_serializer=lambda value: json.dumps(value).encode(
                        "utf-8"
                    ),
                    acks="all",
                    retries=3,
                )
            return self._client


__all__ = [
    "KafkaProducer",
    "KAFKA_SEND_TIMEOUT_SECONDS",
    "kafka_producer",
]

kafka_producer = KafkaProducer()
