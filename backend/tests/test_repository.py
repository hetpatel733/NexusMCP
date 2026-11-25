from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from app.repositories.base import BaseRepository
from app.repositories.workflow_repo import WorkflowRepository


@pytest.mark.asyncio
async def test_base_repository_create():
    mock_collection = MagicMock()
    mock_collection.insert_one = AsyncMock()
    mock_id = ObjectId()
    mock_result = MagicMock()
    mock_result.inserted_id = mock_id
    mock_collection.insert_one.return_value = mock_result

    repo = BaseRepository(mock_collection)
    doc_id = await repo.create({"name": "Test Workflow", "workspace_id": "ws-1"})
    assert doc_id == str(mock_id)
    mock_collection.insert_one.assert_called_once_with({"name": "Test Workflow", "workspace_id": "ws-1"})

@pytest.mark.asyncio
async def test_workflow_repository_get_by_name():
    mock_db = MagicMock()
    mock_workflows_collection = MagicMock()
    mock_workflows_collection.find_one = AsyncMock(return_value={"name": "Test", "workspace_id": "ws-1"})
    mock_db.workflows = mock_workflows_collection

    repo = WorkflowRepository(mock_db)
    result = await repo.get_by_name("ws-1", "Test")
    assert result is not None
    assert result["name"] == "Test"
    mock_workflows_collection.find_one.assert_called_once_with({"workspace_id": "ws-1", "name": "Test"})
