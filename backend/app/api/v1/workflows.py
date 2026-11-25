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


class WorkflowPromptEditRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000, description="Natural-language description of requested workflow changes")
    save_draft: bool = Field(default=True, description="Whether to apply changes immediately as a new version or draft")


class WorkflowPromptEditResponse(BaseModel):
    generation_id: str
    workflow_id: str
    workflow: Optional[Dict[str, Any]] = None
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
    required_integrations: List[str] = Field(default_factory=list)
    trigger: Dict[str, Any] = Field(default_factory=lambda: {"type": "manual"})
    steps: List[Dict[str, Any]] = Field(default_factory=list)


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    trigger: Optional[Dict[str, Any]] = None
    steps: Optional[List[Dict[str, Any]]] = None
    required_integrations: Optional[List[str]] = None
    change_summary: Optional[str] = None


class WorkflowActivateRequest(BaseModel):
    approved: bool = Field(description="Explicit user approval confirmation")
    version: Optional[int] = Field(default=None, description="Optional version to activate (defaults to current_version)")


class WorkflowDetailResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: Optional[str] = None
    status: str
    schema_version: str
    current_version: int = 1
    active_version: Optional[int] = None
    required_integrations: List[str] = Field(default_factory=list)
    trigger: Dict[str, Any] = Field(default_factory=dict)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: Optional[str] = None
    status: str
    current_version: int = 1
    active_version: Optional[int] = None
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
            current_version=wf.get("current_version", 1),
            active_version=wf.get("active_version"),
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


@router.post("/{workflow_id}/prompt-edit", response_model=WorkflowPromptEditResponse)
async def prompt_edit_workflow(
    workflow_id: str,
    data: WorkflowPromptEditRequest,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db),
):
    """Edit an existing workflow using a natural-language prompt."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)

    res = await service.edit_workflow_via_prompt(
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        workflow_id=workflow_id,
        prompt=data.prompt,
        save_draft=data.save_draft,
    )

    wf_dict = res.get("workflow")
    formatted_wf = None
    if wf_dict:
        formatted_wf = {
            "id": str(wf_dict["_id"]),
            "workspace_id": wf_dict["workspace_id"],
            "name": wf_dict["name"],
            "description": wf_dict.get("description"),
            "status": wf_dict.get("status", "draft"),
            "schema_version": wf_dict.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
            "current_version": wf_dict.get("current_version", 1),
            "active_version": wf_dict.get("active_version"),
            "required_integrations": wf_dict.get("required_integrations", []),
            "trigger": wf_dict.get("trigger", {}),
            "steps": wf_dict.get("steps", []),
            "validation_errors": wf_dict.get("validation_errors", []),
            "created_at": wf_dict.get("created_at", datetime.now(timezone.utc)),
            "updated_at": wf_dict.get("updated_at", datetime.now(timezone.utc)),
        }

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_PROMPT_EDIT,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=workflow_id,
        details={
            "prompt": data.prompt,
            "success": res["result"]["success"],
            "status": res["result"]["status"],
            "save_draft": data.save_draft,
        },
        ip_address=ip,
        user_agent=ua,
    )

    return WorkflowPromptEditResponse(
        generation_id=res["generation_id"],
        workflow_id=workflow_id,
        workflow=formatted_wf,
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


@router.post("", response_model=WorkflowDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    data: WorkflowCreateRequest,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Create a workflow strictly bound to the active workspace."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)

    saved = await service.create_workflow(
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        data=data.model_dump(),
    )

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_CREATE,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=str(saved["_id"]),
        details={"name": data.name, "status": saved["status"], "version": saved.get("current_version", 1)},
        ip_address=ip,
        user_agent=ua
    )

    return WorkflowDetailResponse(
        id=str(saved["_id"]),
        workspace_id=saved["workspace_id"],
        name=saved["name"],
        description=saved.get("description"),
        status=saved.get("status", "draft"),
        schema_version=saved.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
        current_version=saved.get("current_version", 1),
        active_version=saved.get("active_version"),
        required_integrations=saved.get("required_integrations", []),
        trigger=saved.get("trigger", {}),
        steps=saved.get("steps", []),
        validation_errors=saved.get("validation_errors", []),
        approved_at=saved.get("approved_at"),
        approved_by=saved.get("approved_by"),
        created_at=saved.get("created_at", datetime.now(timezone.utc)),
        updated_at=saved.get("updated_at", datetime.now(timezone.utc))
    )


@router.get("/{workflow_id}", response_model=WorkflowDetailResponse)
async def get_workflow(
    workflow_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db)
):
    """Strict tenant-isolated workflow detail retrieval."""
    workspace, _ = workspace_and_role
    repo = WorkflowRepository(database)
    wf = await repo.get_workflow(workspace["id"], workflow_id)
    if not wf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found in this workspace",
        )
    return WorkflowDetailResponse(
        id=str(wf["_id"]),
        workspace_id=wf["workspace_id"],
        name=wf["name"],
        description=wf.get("description"),
        status=wf.get("status", "draft"),
        schema_version=wf.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
        current_version=wf.get("current_version", 1),
        active_version=wf.get("active_version"),
        required_integrations=wf.get("required_integrations", []),
        trigger=wf.get("trigger", {}),
        steps=wf.get("steps", []),
        validation_errors=wf.get("validation_errors", []),
        approved_at=wf.get("approved_at"),
        approved_by=wf.get("approved_by"),
        created_at=wf.get("created_at", datetime.now(timezone.utc)),
        updated_at=wf.get("updated_at", datetime.now(timezone.utc))
    )


@router.patch("/{workflow_id}", response_model=WorkflowDetailResponse)
async def update_workflow(
    workflow_id: str,
    data: WorkflowUpdateRequest,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Strict tenant-isolated workflow update with immutable versioning."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)

    # Filter non-None fields
    update_payload = {k: v for k, v in data.model_dump().items() if v is not None}

    updated = await service.update_workflow_definition(
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        workflow_id=workflow_id,
        data=update_payload,
        change_summary=data.change_summary,
    )

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_UPDATE,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=workflow_id,
        details={
            "updated_fields": list(update_payload.keys()),
            "current_version": updated.get("current_version"),
            "status": updated.get("status"),
        },
        ip_address=ip,
        user_agent=ua
    )

    return WorkflowDetailResponse(
        id=str(updated["_id"]),
        workspace_id=updated["workspace_id"],
        name=updated["name"],
        description=updated.get("description"),
        status=updated.get("status", "draft"),
        schema_version=updated.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
        current_version=updated.get("current_version", 1),
        active_version=updated.get("active_version"),
        required_integrations=updated.get("required_integrations", []),
        trigger=updated.get("trigger", {}),
        steps=updated.get("steps", []),
        validation_errors=updated.get("validation_errors", []),
        approved_at=updated.get("approved_at"),
        approved_by=updated.get("approved_by"),
        created_at=updated.get("created_at", datetime.now(timezone.utc)),
        updated_at=updated.get("updated_at", datetime.now(timezone.utc))
    )


@router.post("/{workflow_id}/validate")
async def validate_workflow_record(
    workflow_id: str,
    version: Optional[int] = None,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db)
):
    """Validate a stored workflow against DAG, tool registry, parameters, reachability and integrations."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    return await service.validate_workflow_by_id(
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
        version=version,
    )


@router.post("/{workflow_id}/activate", response_model=WorkflowDetailResponse)
async def activate_workflow_endpoint(
    workflow_id: str,
    data: WorkflowActivateRequest,
    request: Request,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER])),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Activate a workflow with explicit user approval and deterministic verification."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)

    updated = await service.activate_workflow(
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        workflow_id=workflow_id,
        approved=data.approved,
        version=data.version,
    )

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_ENABLE,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=workflow_id,
        details={
            "active_version": updated.get("active_version"),
            "approved": data.approved,
        },
        ip_address=ip,
        user_agent=ua
    )

    return WorkflowDetailResponse(
        id=str(updated["_id"]),
        workspace_id=updated["workspace_id"],
        name=updated["name"],
        description=updated.get("description"),
        status=updated.get("status", "draft"),
        schema_version=updated.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
        current_version=updated.get("current_version", 1),
        active_version=updated.get("active_version"),
        required_integrations=updated.get("required_integrations", []),
        trigger=updated.get("trigger", {}),
        steps=updated.get("steps", []),
        validation_errors=updated.get("validation_errors", []),
        approved_at=updated.get("approved_at"),
        approved_by=updated.get("approved_by"),
        created_at=updated.get("created_at", datetime.now(timezone.utc)),
        updated_at=updated.get("updated_at", datetime.now(timezone.utc))
    )


@router.post("/{workflow_id}/pause", response_model=WorkflowDetailResponse)
async def pause_workflow_endpoint(
    workflow_id: str,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Pause an active workflow."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    updated = await service.pause_workflow(workspace["id"], current_user["id"], workflow_id)

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_PAUSE,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=workflow_id,
        ip_address=ip,
        user_agent=ua
    )

    return WorkflowDetailResponse(
        id=str(updated["_id"]),
        workspace_id=updated["workspace_id"],
        name=updated["name"],
        description=updated.get("description"),
        status=updated.get("status", "draft"),
        schema_version=updated.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
        current_version=updated.get("current_version", 1),
        active_version=updated.get("active_version"),
        required_integrations=updated.get("required_integrations", []),
        trigger=updated.get("trigger", {}),
        steps=updated.get("steps", []),
        validation_errors=updated.get("validation_errors", []),
        approved_at=updated.get("approved_at"),
        approved_by=updated.get("approved_by"),
        created_at=updated.get("created_at", datetime.now(timezone.utc)),
        updated_at=updated.get("updated_at", datetime.now(timezone.utc))
    )


@router.post("/{workflow_id}/resume", response_model=WorkflowDetailResponse)
async def resume_workflow_endpoint(
    workflow_id: str,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Resume a paused workflow."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    updated = await service.resume_workflow(workspace["id"], current_user["id"], workflow_id)

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_RESUME,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=workflow_id,
        ip_address=ip,
        user_agent=ua
    )

    return WorkflowDetailResponse(
        id=str(updated["_id"]),
        workspace_id=updated["workspace_id"],
        name=updated["name"],
        description=updated.get("description"),
        status=updated.get("status", "draft"),
        schema_version=updated.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
        current_version=updated.get("current_version", 1),
        active_version=updated.get("active_version"),
        required_integrations=updated.get("required_integrations", []),
        trigger=updated.get("trigger", {}),
        steps=updated.get("steps", []),
        validation_errors=updated.get("validation_errors", []),
        approved_at=updated.get("approved_at"),
        approved_by=updated.get("approved_by"),
        created_at=updated.get("created_at", datetime.now(timezone.utc)),
        updated_at=updated.get("updated_at", datetime.now(timezone.utc))
    )


@router.post("/{workflow_id}/archive", response_model=WorkflowDetailResponse)
async def archive_workflow_endpoint(
    workflow_id: str,
    request: Request,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Archive a workflow."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    updated = await service.archive_workflow(workspace["id"], current_user["id"], workflow_id)

    ip, ua = get_client_info(request)
    audit_repo = AuditRepository(database)
    await audit_repo.record_event(
        action=AuditAction.WORKFLOW_ARCHIVE,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_email=current_user["email"],
        resource_type="workflow",
        resource_id=workflow_id,
        ip_address=ip,
        user_agent=ua
    )

    return WorkflowDetailResponse(
        id=str(updated["_id"]),
        workspace_id=updated["workspace_id"],
        name=updated["name"],
        description=updated.get("description"),
        status=updated.get("status", "draft"),
        schema_version=updated.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
        current_version=updated.get("current_version", 1),
        active_version=updated.get("active_version"),
        required_integrations=updated.get("required_integrations", []),
        trigger=updated.get("trigger", {}),
        steps=updated.get("steps", []),
        validation_errors=updated.get("validation_errors", []),
        approved_at=updated.get("approved_at"),
        approved_by=updated.get("approved_by"),
        created_at=updated.get("created_at", datetime.now(timezone.utc)),
        updated_at=updated.get("updated_at", datetime.now(timezone.utc))
    )


@router.get("/{workflow_id}/versions")
async def list_workflow_versions(
    workflow_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db)
):
    """List immutable version history for a workflow."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    return await service.list_versions(workspace["id"], workflow_id)


@router.get("/{workflow_id}/versions/{version}")
async def get_workflow_version(
    workflow_id: str,
    version: int,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db)
):
    """Fetch exact immutable version definition."""
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    ver = await service.get_version(workspace["id"], workflow_id, version)
    if not ver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow version not found")
    return ver


@router.get("/{workflow_id}/execution-version")
async def get_execution_version(
    workflow_id: str,
    execution_id: Optional[str] = None,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db)
):
    """Resolve exact immutable workflow version for an execution or active trigger.

    Requirement 7:
    - Existing executions continue using their original workflow version.
    - New triggers use the newly activated version.
    """
    workspace, _ = workspace_and_role
    service = WorkflowService(database)
    return await service.resolve_execution_version(
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
        execution_id=execution_id,
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

    # Clean up any active trigger subscription
    try:
        from ...triggers.lifecycle import SubscriptionLifecycleManager
        lifecycle = SubscriptionLifecycleManager(database)
        await lifecycle.unregister_workflow_trigger(workspace["id"], workflow_id)
    except Exception:
        pass

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
