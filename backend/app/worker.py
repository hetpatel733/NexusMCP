import asyncio
import sys

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from .config import get_settings
from .logger import logger


@activity.defn
async def ping_activity(payload: str = "ping") -> str:
    """Basic system activity to verify worker task execution and connectivity."""
    logger.info("Executing ping_activity", payload=payload)
    return f"pong: {payload}"


async def run_worker():
    settings = get_settings()
    logger.info(
        "Initializing Temporal Worker",
        host=settings.TEMPORAL_HOST,
        namespace=settings.TEMPORAL_NAMESPACE,
    )

    # Retry connecting to Temporal
    client = None
    for attempt in range(1, 31):
        try:
            client = await Client.connect(
                settings.TEMPORAL_HOST, namespace=settings.TEMPORAL_NAMESPACE
            )
            logger.info("Connected to Temporal server")
            break
        except Exception as e:
            logger.warning("Waiting for Temporal server...", attempt=attempt, error=str(e))
            await asyncio.sleep(2)

    if not client:
        logger.error("Could not connect to Temporal server after retries. Exiting.")
        sys.exit(1)

    # Initialize worker with basic activity
    worker = Worker(
        client,
        task_queue="nexusmcp-task-queue",
        workflows=[],
        activities=[ping_activity],
    )

    logger.info("Starting Temporal Worker listening on task queue: nexusmcp-task-queue")
    await worker.run()

if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
