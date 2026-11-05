"""Publish/subscribe helpers for the notification service."""

from app.services.notification.pub_sub.kafka_producer import KafkaProducer

__all__ = ["KafkaProducer"]
