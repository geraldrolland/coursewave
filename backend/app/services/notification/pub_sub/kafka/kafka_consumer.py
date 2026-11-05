"""Kafka consumer that forwards notification messages to WebSockets."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from kafka import KafkaConsumer as KafkaClient

from app.base_config import get_settings
from app.services.notification.ws.websocket_notif_manager import (
    WebsocketNotificationManager,
    websocket_notification_manager,
)

logger = logging.getLogger("notification.kafka.consumer")

KAFKA_POLL_TIMEOUT_MS = 1000
KAFKA_RETRY_DELAY_SECONDS = 5


class KafkaConsumer:
    """Consume notification payloads and deliver them to WebSocket users."""

    def __init__(
        self,
        consumer: KafkaClient | None = None,
        manager: WebsocketNotificationManager | None = None,
        topic: str | None = None,
        bootstrap_servers: str | None = None,
        group_id: str | None = None,
    ) -> None:
        settings = get_settings()
        self.topic = topic or settings.notification_topic
        self.manager = manager or websocket_notification_manager
        self._bootstrap_servers = (
            bootstrap_servers or settings.kafka_bootstrap_servers
        )
        self._group_id = group_id or settings.kafka_consumer_group_id
        self._consumer = consumer
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="notification-kafka-consumer",
        )

    async def run(self) -> None:
        """Poll Kafka continuously and process notification messages."""

        while True:
            try:
                await self.consume_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Kafka consumer iteration failed")
                await asyncio.sleep(KAFKA_RETRY_DELAY_SECONDS)

    async def consume_once(self) -> None:
        """Poll once, dispatch messages, and commit each processed offset."""

        consumer = await self._run_in_consumer_thread(self._get_consumer)
        records = await self._run_in_consumer_thread(
            consumer.poll,
            KAFKA_POLL_TIMEOUT_MS,
        )

        for record in self._iter_records(records):
            payload = self._decode_payload(record.value)
            if payload is not None:
                await self._dispatch(payload)
            await self._run_in_consumer_thread(consumer.commit)

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        """Send one payload to its recipient's active WebSockets."""

        recipient_id = payload.get("recipient_id")
        message = payload.get("message")

        if not isinstance(recipient_id, str) or not recipient_id:
            logger.warning("Kafka notification payload has no recipient_id")
            return

        if not isinstance(message, dict):
            logger.warning("Kafka notification payload has no message object")
            return

        await self.manager.send(recipient_id, message)

    def _get_consumer(self) -> KafkaClient:
        """Create the Kafka consumer on first use."""

        if self._consumer is None:
            self._consumer = KafkaClient(
                self.topic,
                bootstrap_servers=self._bootstrap_servers,
                group_id=self._group_id,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
            )
        return self._consumer

    async def _run_in_consumer_thread(self, function, *args):
        """Run a blocking consumer operation on its dedicated thread."""

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(function, *args),
        )

    @staticmethod
    def _iter_records(records):
        """Yield records from Kafka poll results or a test list."""

        if isinstance(records, dict):
            for batch in records.values():
                yield from batch
            return

        yield from records

    @staticmethod
    def _decode_payload(raw_value: Any) -> dict[str, Any] | None:
        """Decode a Kafka message value into a JSON object."""

        try:
            if isinstance(raw_value, bytes):
                raw_value = raw_value.decode("utf-8")
            payload = (
                json.loads(raw_value)
                if isinstance(raw_value, str)
                else raw_value
            )
        except (TypeError, ValueError):
            logger.exception("Kafka notification payload is not valid JSON")
            return None

        if not isinstance(payload, dict):
            logger.warning("Kafka notification payload is not a JSON object")
            return None

        return payload

    async def aclose(self) -> None:
        """Close the consumer and its dedicated thread."""

        if self._consumer is not None:
            await self._run_in_consumer_thread(self._consumer.close)
            self._consumer = None
        self._executor.shutdown(wait=False)


__all__ = ["KafkaConsumer", "KAFKA_POLL_TIMEOUT_MS"]
