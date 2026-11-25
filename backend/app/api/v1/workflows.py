from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ...db import get_db
from ...models.audit import AuditAction
from ...models.workflow import (
    CURRENT_WORKFLOW_SCHEMA_VERSION,
    WorkflowDefinition,
    WorkflowGenerationResult,
    get_workflow_json_schema,
)
from ...models.workspace import WorkspaceRole
from ...repositories.audit_repo import AuditRepository
from ...repositories.workflow_repo import WorkflowRepository
from ...security.dependencies import get_current_user, get_current_workspace, require_role
from ...services.workflow_service import WorkflowService

router = APIRouter(prefix="/workflows", tags=["Workflows"])


class WorkflowGenerateRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000, description="Natural-language description of desired workflow")
    save_draft: bool = Field(default=False, description="Whether to automatically save as draft if generation succeeds")


class WorkflowGenerateResponse(BaseModel):
    generation_id: str
    workflow_id: Optional[str] = None
    result: WorkflowGenerationResult


class WorkflowValidateRequest(BaseModel):
    workflow: Dict[str, Any]


class WorkflowValidateResponse(BaseModel):
    valid: bool
    errors: List[str] = Field(default_factory=list)


class WorkflowCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    status: str = Field(default="draft")
    trigger: Dict[str, Any] = Field(default_factory=dict)
    steps: List[Dict[str, Any]] = Field(default_factory=list)


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class WorkflowResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def get_client_info(request: Request) -> tuple[Optional[str], Optional[str]]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


@router.get("", response_model=List[WorkflowResponse])
async def list_workflows(
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db)
):
    """List workflows strictly belonging to the caller's active workspace."""
    workspace, _ = workspace_and_role
    repo = WorkflowRepository(database)
    workflows = await repo.list_by_workspace(workspace["id"])
    return [
        WorkflowResponse(
            id=str(wf["_id"]),
            workspace_id=wf["workspace_id"],
            name=wf["name"],
            description=wf.get("description"),
            status=wf.get("status", "draft"),
            created_at=wf.get("created_at", datetime.now(timezone.utc)),
            updated_at=wf.get("updated_at", datetime.now(timezone.utc))
        )
        for wf in workflows
    ]


@router.get("/schema")
async def get_workflow_schema():
    """Return the canonical versioned JSON Schema for NexusMCP workflows."""
    return {
        "schema_version": CURRENT_WORKFLOW_SCHEMA_VERSION,
        "json_schema": get_workflow_json_schema(),
    }


@router.post("/generate", response_model=WorkflowGenerateResponse)
async def generate_workflow_from_prompt(
    data: WorkflowGenerateRequest,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db),
):
    """Generate a structured, validated workflow definition from natural language."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)

    res = await service.generate_workflow(
        prompt=data.prompt,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        save_draft=data.save_draft,
    )

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_GENERATE,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow_generation",
        resource_id=res["generation_id"],
        details={
            "status": res["result"]["status"],
            "success": res["result"]["success"],
            "save_draft": data.save_draft,
        },
        ip_address=ip,
        user_agent=ua,
    )

    return WorkflowGenerateResponse(
        generation_id=res["generation_id"],
        workflow_id=res["workflow_id"],
        result=WorkflowGenerationResult.model_validate(res["result"]),
    )


@router.post("/validate", response_model=WorkflowValidateResponse)
async def validate_workflow_endpoint(
    data: WorkflowValidateRequest,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Deterministic server-side validation of a workflow definition against workspace tools."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    errors = await service.validate_custom_workflow(workspace["id"], data.workflow)
    return WorkflowValidateResponse(
        valid=len(errors) == 0,
        errors=errors,
    )


@router.get("/generations")
async def list_generation_history(
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """List stored workflow generation sessions for the current workspace."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    return await service.list_generation_records(workspace["id"])


@router.get("/generations/{generation_id}")
async def get_generation_detail(
    generation_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Retrieve full generation record including prompt, model metadata, and schema version."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    record = await service.get_generation_record(workspace["id"], generation_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation record not found in this workspace",
        )
    record["id"] = str(record["_id"])
    del record["_id"]
    return record


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    data: WorkflowCreateRequest,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Create a workflow strictly bound to the active workspace."""
    workspace, _ = workspace_and_role
    repo = WorkflowRepository(database)
    now = datetime.now(timezone.utc)

    doc = {
        "workspace_id": workspace["id"],
        "name": data.name,
        "description": data.description,
        "status": data.status,
        "trigger": data.trigger,
        "steps": data.steps,
        "created_by": current_user["id"],
        "created_at": now,
        "updated_at": now,
    }
    wf_id = await repo.create(doc)

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_CREATE,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=wf_id,
        details={"name": data.name, "status": data.status},
        ip_address=ip,
        user_agent=ua
    )

    return WorkflowResponse(
        id=wf_id,
        workspace_id=workspace["id"],
        name=data.name,
        description=data.description,
        status=data.status,
        created_at=now,
        updated_at=now
    )


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db)
):
    """Strict tenant-isolated workflow retrieval."""
    workspace, _ = workspace_and_role
    repo = WorkflowRepository(database)
    wf = await repo.get_workflow(workspace["id"], workflow_id)
    if not wf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found in this workspace",
        )
    return WorkflowResponse(
        id=str(wf["_id"]),
        workspace_id=wf["workspace_id"],
        name=wf["name"],
        description=wf.get("description"),
        status=wf.get("status", "draft"),
        created_at=wf.get("created_at", datetime.now(timezone.utc)),
        updated_at=wf.get("updated_at", datetime.now(timezone.utc))
    )


@router.patch("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    data: WorkflowUpdateRequest,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Strict tenant-isolated workflow update."""
    workspace, _ = workspace_and_role
    repo = WorkflowRepository(database)
    existing = await repo.get_workflow(workspace["id"], workflow_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found in this workspace",
        )

    now = datetime.now(timezone.utc)
    update_data: Dict[str, Any] = {"updated_at": now}
    if data.name is not None:
        update_data["name"] = data.name
    if data.description is not None:
        update_data["description"] = data.description
    if data.status is not None:
        update_data["status"] = data.status

    await repo.update_workflow(workspace["id"], workflow_id, update_data)

    action = AuditAction.WORKFLOW_UPDATE
    if data.status == "active":
        action = AuditAction.WORKFLOW_ENABLE
    elif data.status == "paused" or data.status == "disabled":
        action = AuditAction.WORKFLOW_DISABLE

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=action,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=workflow_id,
        details={"updated_fields": list(update_data.keys())},
        ip_address=ip,
        user_agent=ua
    )

    updated = await repo.get_workflow(workspace["id"], workflow_id)
    assert updated is not None
    return WorkflowResponse(
        id=str(updated["_id"]),
        workspace_id=updated["workspace_id"],
        name=updated["name"],
        description=updated.get("description"),
        status=updated.get("status", "draft"),
        created_at=updated.get("created_at", now),
        updated_at=updated.get("updated_at", now)
    )


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: str,
    request: Request,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Strict tenant-isolated workflow deletion. Requires Owner or Admin role."""
    workspace, _ = workspace_and_role
    repo = WorkflowRepository(database)
    deleted = await repo.delete_workflow(workspace["id"], workflow_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found in this workspace",
        )

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_DELETE,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=workflow_id,
        ip_address=ip,
        user_agent=ua
    )

    return {"message": "Workflow successfully deleted"}
