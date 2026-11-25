from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient

from .config import get_settings
from .logger import logger


class Database:
    client: Optional[AsyncIOMotorClient] = None
    indexes_created: bool = False

db = Database()

async def get_db():
    if db.client is None:
        await init_db()
    settings = get_settings()
    assert db.client is not None
    return db.client[settings.MONGO_DB_NAME]

async def init_db():
    settings = get_settings()
    try:
        logger.info("Connecting to MongoDB", uri=settings.MONGO_URI.split("@")[-1])
        db.client = AsyncIOMotorClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS=3000
        )
        # Ping the server
        await db.client.admin.command('ping')
        logger.info("Successfully connected to MongoDB")
        await create_indexes()
    except Exception as e:
        logger.error("Failed to connect to MongoDB", error=str(e))
        # Keep client set so readiness check can report failure appropriately

async def close_db():
    if db.client:
        logger.info("Closing MongoDB connection")
        db.client.close()
        db.client = None
        db.indexes_created = False

async def check_db_health() -> Dict[str, Any]:
    if not db.client:
        return {"status": "down", "error": "Database client not initialized"}
    try:
        await db.client.admin.command('ping')
        if not db.indexes_created:
            await create_indexes()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "down", "error": str(e)}

async def create_indexes():
    """Create essential MongoDB indexes for application entities."""
    try:
        database = await get_db()
        # Users collection
        await database.users.create_index("email", unique=True)

        # Workspaces collection
        await database.workspaces.create_index("name")
        await database.workspaces.create_index("slug", unique=True, sparse=True)

        # Workspace Memberships collection: compound unique index (workspace_id, user_id)
        await database.memberships.create_index(
            [("workspace_id", 1), ("user_id", 1)],
            unique=True
        )
        await database.memberships.create_index("user_id")

        # Refresh Tokens / Sessions collection
        await database.refresh_tokens.create_index("token_hash", unique=True)
        await database.refresh_tokens.create_index("user_id")
        await database.refresh_tokens.create_index("expires_at")

        # Audit Logs collection
        await database.audit_logs.create_index([("workspace_id", 1), ("created_at", -1)])
        await database.audit_logs.create_index("user_id")
        await database.audit_logs.create_index("action")
        await database.audit_logs.create_index("created_at")

        # Workflows collection: enforce workspace_id and version indexing
        await database.workflows.create_index([("workspace_id", 1), ("name", 1)])
        await database.workflows.create_index([("workspace_id", 1), ("status", 1)])

        # Executions summary collection
        await database.workflow_executions.create_index([("workspace_id", 1), ("workflow_id", 1)])
        await database.workflow_executions.create_index([("workspace_id", 1), ("status", 1)])
        await database.workflow_executions.create_index("created_at")

        # Step executions collection
        await database.step_executions.create_index(
            [("workspace_id", 1), ("execution_id", 1), ("step_id", 1), ("attempt", 1)],
            unique=True,
        )
        await database.step_executions.create_index([("workspace_id", 1), ("idempotency_key", 1)])

        # Execution payloads collection
        await database.execution_payloads.create_index(
            [("workspace_id", 1), ("payload_id", 1)],
            unique=True,
        )

        # Connections collection: tenant-scoped connection records
        await database.connections.create_index([("workspace_id", 1), ("user_id", 1)])
        await database.connections.create_index([("workspace_id", 1), ("app_name", 1)])
        await database.connections.create_index("connection_id")
        await database.connections.create_index("external_user_id")

        # Phase 7 Trigger & Ingestion collections
        await database.deduplication_records.create_index(
            [("workspace_id", 1), ("idempotency_key", 1)],
            unique=True,
        )
        await database.deduplication_records.create_index(
            [("expires_at", 1)],
            expireAfterSeconds=0,
        )
        await database.trigger_events.create_index([("workspace_id", 1), ("received_at", -1)])
        await database.trigger_events.create_index([("status", 1)])
        await database.trigger_events.create_index([("event_id", 1)])
        await database.trigger_subscriptions.create_index(
            [("workspace_id", 1), ("workflow_id", 1)],
            unique=True,
        )
        await database.trigger_subscriptions.create_index([("status", 1), ("trigger_type", 1)])
        await database.trigger_checkpoints.create_index(
            [("workspace_id", 1), ("workflow_id", 1)],
            unique=True,
        )

        db.indexes_created = True
        logger.info("MongoDB indexes verified/created")
    except Exception as e:
        logger.error("Failed to create MongoDB indexes", error=str(e))
