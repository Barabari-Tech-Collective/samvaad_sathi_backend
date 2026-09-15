import asyncio
import json

import aio_pika
import loguru

from src.config.manager import settings
from src.securities.authorizations.access_revocation import clear_access_revoked, set_access_revoked

_RECONNECT_DELAY_SECONDS = 10


def _connection_url() -> str:
    protocol = "amqps" if settings.RABBITMQ_SSL_ENABLED else "amqp"
    vhost = "" if settings.RABBITMQ_VIRTUAL_HOST == "/" else settings.RABBITMQ_VIRTUAL_HOST
    return (
        f"{protocol}://{settings.RABBITMQ_USERNAME}:{settings.RABBITMQ_PASSWORD}"
        f"@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{vhost}"
    )


async def _handle_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    async with message.process():
        try:
            envelope = json.loads(message.body.decode())
            event_data = envelope.get("eventData", {})
            unique_id = event_data.get("uniqueId")
            product_unique_id = event_data.get("productUniqueId")
            action = event_data.get("action")
        except (json.JSONDecodeError, AttributeError) as exc:
            loguru.logger.error(f"[access-revoked-consumer] Malformed event, dropping: {exc}")
            return

        if not unique_id or action not in ("revoked", "granted"):
            loguru.logger.error(f"[access-revoked-consumer] Missing/invalid fields, dropping: {event_data}")
            return

        # null productUniqueId = every product at once (full-account ban/unban); otherwise
        # only act if it's specifically this product - Samvaad Saathi doesn't care that a
        # student's CodeGuru access changed.
        applies_to_this_product = product_unique_id is None or product_unique_id == settings.SAMPARK_PRODUCT_UNIQUE_ID
        if not applies_to_this_product:
            return

        if action == "revoked":
            await set_access_revoked(unique_id)
            loguru.logger.info(f"[access-revoked-consumer] Marked revoked: {unique_id}")
        else:
            await clear_access_revoked(unique_id)
            loguru.logger.info(f"[access-revoked-consumer] Cleared revoked flag: {unique_id}")


async def _run_forever() -> None:
    while True:
        try:
            connection = await aio_pika.connect_robust(_connection_url())
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)

                # Idempotent: auth-service also declares this exchange (it's the
                # publisher); declaring it again here too is safe and means this consumer
                # doesn't depend on auth-service having started first.
                exchange = await channel.declare_exchange(
                    settings.RABBITMQ_ACCESS_REVOKED_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                )
                queue = await channel.declare_queue(settings.RABBITMQ_ACCESS_REVOKED_QUEUE, durable=True)
                await queue.bind(exchange, routing_key=settings.RABBITMQ_ACCESS_REVOKED_ROUTING_KEY)

                loguru.logger.info(
                    f"[access-revoked-consumer] Listening on queue {settings.RABBITMQ_ACCESS_REVOKED_QUEUE}"
                )
                await queue.consume(_handle_message)
                # Blocks here until the connection drops - aio_pika delivers messages via
                # the callback above on this same event loop in the meantime.
                await asyncio.Future()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            loguru.logger.warning(
                f"[access-revoked-consumer] Disconnected ({exc}); retrying in {_RECONNECT_DELAY_SECONDS}s"
            )
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)


def start_consumer_task() -> asyncio.Task | None:
    """
    Fire-and-forget background task, started at app startup. Deliberately not awaited:
    RabbitMQ being unreachable (or not configured at all - see RABBITMQ_ENABLED) must never
    block or fail app startup, matching how a down Redis doesn't either. Returns None when
    disabled so callers have an explicit signal for whether there's anything to cancel on
    shutdown.
    """
    if not settings.RABBITMQ_ENABLED:
        loguru.logger.info("[access-revoked-consumer] RABBITMQ_ENABLED is false, not starting consumer")
        return None
    return asyncio.create_task(_run_forever())


async def stop_consumer_task(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
