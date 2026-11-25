import asyncio
import sys

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from .config import get_settings
from .db import close_db, get_db, init_db
from .logger import logger
from .triggers.polling_engine import PollingEngine
from .triggers.scheduler import ScheduleEngine
from .triggers.service import TriggerIngestionService


@activity.defn
async def ping_activity(payload: str = "ping") -> str:
    """Basic system activity to verify worker task execution and connectivity."""
    logger.info("Executing ping_activity", payload=payload)
    return f"pong: {payload}"


async def run_trigger_scheduler_loop(db, ingestion_service):
    """Background task running polling engine and schedule triggers periodically."""
    polling_engine = PollingEngine(db, ingestion_service)
    schedule_engine = ScheduleEngine(db, ingestion_service)

    logger.info("Trigger background polling and scheduler loop started")
    while True:
        try:
            # Run one cycle of polling
            await polling_engine.run_single_cycle()
            # Run one cycle of schedule checks
            await schedule_engine.run_single_cycle()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in trigger scheduler background loop", error=str(e))
        # Wait 10 seconds between cycles
        await asyncio.sleep(10)


async def run_worker():
    settings = get_settings()
    logger.info(
        "Initializing Temporal Worker",
        host=settings.TEMPORAL_HOST,
        namespace=settings.TEMPORAL_NAMESPACE,
    )

    # Initialize DB for trigger background tasks
    try:
        await init_db()
        db = await get_db()
        ingestion_service = TriggerIngestionService(db)
        # Recover any accepted unprocessed events on worker startup
        recovered = await ingestion_service.recover_unprocessed_events()
        if recovered > 0:
            logger.info("Worker recovered unprocessed trigger events on startup", count=recovered)
    except Exception as e:
        logger.warning("Worker DB initialization warning", error=str(e))
        db = None
        ingestion_service = None

    # Start trigger scheduler background task if DB ready
    scheduler_task = None
    if db and ingestion_service:
        scheduler_task = asyncio.create_task(run_trigger_scheduler_loop(db, ingestion_service))

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

    # Inject Temporal client into ingestion service for durable dispatch
    if ingestion_service and client:
        ingestion_service.temporal_client = client

    # Initialize execution activities with MongoDB connection
    from .execution.activities import ExecutionActivities
    from .execution.workflows import TEMPORAL_TASK_QUEUE, WorkflowExecutionWorkflow

    execution_activities = ExecutionActivities(db)

    # Initialize worker with workflow and activities
    worker = Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        workflows=[WorkflowExecutionWorkflow],
        activities=[
            ping_activity,
            execution_activities.load_execution_definition,
            execution_activities.execute_step,
            execution_activities.complete_execution,
            execution_activities.store_payload_activity,
            execution_activities.get_payload_activity,
        ],
    )

    logger.info("Starting Temporal Worker listening on task queue", task_queue=TEMPORAL_TASK_QUEUE)
    await worker.run()

if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
