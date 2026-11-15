"""
RabbitMQ integration: publish + consume.
Publish and consume message events.
Works with a RabbitMQ cluster.
"""
import aio_pika
import os
import json
import logging
import asyncio
from typing import Optional, Callable, Awaitable
from datetime import datetime, timezone

logger = logging.getLogger("message-service.mq")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://mq_user:mq_pass@rabbitmq:5672/")
# Для HA: можно указать несколько узлов через ; (aio-pika RobustConnection поддерживает)
RABBITMQ_URLS = os.getenv("RABBITMQ_URLS", RABBITMQ_URL)

connection: Optional[aio_pika.RobustConnection] = None
channel: Optional[aio_pika.Channel] = None
_consumer_tag: Optional[str] = None
_consume_task: Optional[asyncio.Task] = None

# Processed events counter
processed_events = 0
last_event: Optional[dict] = None


async def init_mq():
    """Инициализация соединения, exchange, queue и запуск consumer."""
    global connection, channel
    # RobustConnection автоматически переподключается при падении узла
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)

    # Topic exchange для событий
    exchange = await channel.declare_exchange(
        "messages", aio_pika.ExchangeType.TOPIC, durable=True
    )

    # Очередь для потребления (durable + quorum-like behaviour via arguments where possible)
    queue = await channel.declare_queue(
        "message_events",
        durable=True,
        arguments={
            "x-queue-type": "classic",  # classic + durable; в кластере можно x-queue-type=quorum
        },
    )
    await queue.bind(exchange, routing_key="message.#")

    logger.info("RabbitMQ initialized: exchange=messages, queue=message_events")


async def start_consumer(handler: Optional[Callable[[dict], Awaitable[None]]] = None):
    """Запускает фоновое потребление сообщений из очереди."""
    global _consume_task
    if not channel:
        raise RuntimeError("MQ channel not initialized")

    async def _default_handler(payload: dict):
        global processed_events, last_event
        processed_events += 1
        last_event = {
            **payload,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            "Consumed event #%d type=%s payload_keys=%s",
            processed_events,
            payload.get("type"),
            list(payload.keys()),
        )
        # Persist consumer stats in Redis
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/1"), decode_responses=True)
            await r.incr("mq:processed_total")
            await r.set("mq:last_event", json.dumps(last_event, default=str), ex=3600)
            await r.close()
        except Exception as e:
            logger.debug("Could not write consume stats to Redis: %s", e)


    on_message = handler or _default_handler

    async def _consume_loop():
        queue = await channel.get_queue("message_events")
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    try:
                        body = message.body.decode()
                        payload = json.loads(body)
                        await on_message(payload)
                    except Exception as e:
                        logger.exception("Failed to process message: %s", e)

    _consume_task = asyncio.create_task(_consume_loop())
    logger.info("RabbitMQ consumer started")


async def close_mq():
    global connection, channel, _consume_task
    if _consume_task and not _consume_task.done():
        _consume_task.cancel()
        try:
            await _consume_task
        except asyncio.CancelledError:
            pass
    if connection:
        await connection.close()
    connection = None
    channel = None


async def publish_message_event(event_type: str, payload: dict):
    """Публикуем событие (created, deleted и т.д.) в topic exchange."""
    if not channel:
        logger.warning("Cannot publish: channel is None")
        return
    body = json.dumps({"type": event_type, **payload}, default=str).encode()
    exchange = await channel.get_exchange("messages")
    await exchange.publish(
        aio_pika.Message(
            body=body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        ),
        routing_key=f"message.{event_type}",
    )
    logger.debug("Published message.%s", event_type)


def get_consumer_stats() -> dict:
    """Return consumer statistics."""
    return {
        "processed_events": processed_events,
        "last_event": last_event,
        "connected": connection is not None and not connection.is_closed,
    }
