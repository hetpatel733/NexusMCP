import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const apiClient = axios.create({
    baseURL: `${API_BASE_URL}/api/v1`,
    headers: {
        'Content-Type': 'application/json',
    },
    timeout: 10000,
});

// Attach access token and active workspace to all requests
apiClient.interceptors.request.use((config) => {
    const token = localStorage.getItem('nexus_access_token');
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    const activeWorkspaceId = localStorage.getItem('nexus_active_workspace_id');
    if (activeWorkspaceId) {
        config.headers['X-Workspace-ID'] = activeWorkspaceId;
    }
    return config;
});

// Response interceptor to handle token refresh on 401
apiClient.interceptors.response.use(
    (response) => response,
    async (error) => {
        const originalRequest = error.config;
        if (error.response?.status === 401 && !originalRequest._retry && !originalRequest.url?.includes('/auth/login') && !originalRequest.url?.includes('/auth/refresh')) {
            originalRequest._retry = true;
            const refreshToken = localStorage.getItem('nexus_refresh_token');
            if (refreshToken) {
                try {
                    const res = await axios.post(`${API_BASE_URL}/api/v1/auth/refresh`, {
                        refresh_token: refreshToken,
                    });
                    const { access_token, refresh_token: newRefreshToken } = res.data;
                    localStorage.setItem('nexus_access_token', access_token);
                    if (newRefreshToken) {
                        localStorage.setItem('nexus_refresh_token', newRefreshToken);
                    }
                    originalRequest.headers.Authorization = `Bearer ${access_token}`;
                    return apiClient(originalRequest);
                } catch {
                    localStorage.removeItem('nexus_access_token');
                    localStorage.removeItem('nexus_refresh_token');
                    localStorage.removeItem('nexus_active_workspace_id');
                    window.location.href = '/login';
                }
            }
        }
        return Promise.reject(error);
    }
);

export interface User {
    id: string;
    email: string;
    full_name: string;
    is_active: boolean;
    created_at: string;
}

export type WorkspaceRole = 'owner' | 'admin' | 'member';

export interface Workspace {
    id: string;
    name: string;
    slug: string;
    description?: string;
    created_by: string;
    created_at: string;
    role: WorkspaceRole;
}

export interface WorkspaceMember {
    id: string;
    workspace_id: string;
    user_id: string;
    email: string;
    full_name: string;
    role: WorkspaceRole;
    joined_at: string;
}

export interface AuditLog {
    id: string;
    workspace_id?: string;
    user_id?: string;
    user_email?: string;
    action: string;
    resource_type?: string;
    resource_id?: string;
    details: Record<string, any>;
    ip_address?: string;
    created_at: string;
}

export type ConnectionStatus = 'connected' | 'disconnected' | 'expired' | 'action_required' | 'unknown';

export interface HealthResponse {
    status: string;
    version: string;
    environment: string;
}

export interface ReadinessResponse {
    status: string;
    dependencies: Record<string, any>;
}

export interface AuthResponse {
    access_token: string;
    refresh_token: string;
    token_type: string;
    expires_in: number;
    user: User;
}

// System endpoints
export const fetchHealth = async (): Promise<HealthResponse> => {
    const response = await apiClient.get<HealthResponse>('/health');
    return response.data;
};

export const fetchReadiness = async (): Promise<ReadinessResponse> => {
    const response = await apiClient.get<ReadinessResponse>('/ready');
    return response.data;
};

// Auth endpoints
export const apiLogin = async (email: string, password: string): Promise<AuthResponse> => {
    const response = await apiClient.post<AuthResponse>('/auth/login', { email, password });
    return response.data;
};

export const apiRegister = async (email: string, password: string, fullName: string): Promise<AuthResponse> => {
    const response = await apiClient.post<AuthResponse>('/auth/register', {
        email,
        password,
        full_name: fullName,
    });
    return response.data;
};

export const apiLogout = async (): Promise<void> => {
    const refreshToken = localStorage.getItem('nexus_refresh_token');
    try {
        await apiClient.post('/auth/logout', { refresh_token: refreshToken });
    } catch {
        // Continue cleanup
    } finally {
        localStorage.removeItem('nexus_access_token');
        localStorage.removeItem('nexus_refresh_token');
        localStorage.removeItem('nexus_active_workspace_id');
    }
};

export const apiGetMe = async (): Promise<User> => {
    const response = await apiClient.get<User>('/auth/me');
    return response.data;
};

export const apiUpdateProfile = async (data: { full_name?: string; current_password?: string; new_password?: string }): Promise<User> => {
    const response = await apiClient.patch<User>('/auth/profile', data);
    return response.data;
};

// Workspaces endpoints
export const apiListWorkspaces = async (): Promise<Workspace[]> => {
    const response = await apiClient.get<Workspace[]>('/workspaces');
    return response.data;
};

export const apiCreateWorkspace = async (name: string, description?: string): Promise<Workspace> => {
    const response = await apiClient.post<Workspace>('/workspaces', { name, description });
    return response.data;
};

export const apiListWorkspaceMembers = async (workspaceId: string): Promise<WorkspaceMember[]> => {
    const response = await apiClient.get<WorkspaceMember[]>(`/workspaces/${workspaceId}/members`);
    return response.data;
};

export const apiAddWorkspaceMember = async (workspaceId: string, email: string, role: WorkspaceRole): Promise<WorkspaceMember> => {
    const response = await apiClient.post<WorkspaceMember>(`/workspaces/${workspaceId}/members`, { email, role });
    return response.data;
};

export const apiUpdateWorkspaceMemberRole = async (workspaceId: string, userId: string, role: WorkspaceRole): Promise<void> => {
    await apiClient.patch(`/workspaces/${workspaceId}/members/${userId}`, { role });
};

export const apiRemoveWorkspaceMember = async (workspaceId: string, userId: string): Promise<void> => {
    await apiClient.delete(`/workspaces/${workspaceId}/members/${userId}`);
};

// Audit logs endpoints
export const apiListAuditLogs = async (limit = 50): Promise<AuditLog[]> => {
    const response = await apiClient.get<AuditLog[]>('/audit-logs', { params: { limit } });
    return response.data;
};

// Integration connection endpoints
export interface Connection {
    id: string;
    workspace_id: string;
    user_id: string;
    app_name: string;
    connection_id: string;
    status: string;
    connected_at?: string;
    expires_at?: string;
    created_at: string;
    updated_at: string;
}

export interface ConnectionCreateRequest {
    app_name: string;
    redirect_url?: string;
}

export interface ConnectionInitiateResponse {
    id: string;
    workspace_id: string;
    user_id: string;
    app_name: string;
    connection_id: string;
    status: string;
    redirect_url: string;
    external_user_id: string;
}

export interface ConnectionStatusResponse {
    connection_id: string;
    app_name: string;
    status: string;
    external_user_id: string;
    connected_at?: string;
    expires_at?: string;
}

export const apiListConnections = async (params?: {
    app_name?: string;
    status?: string;
    limit?: number;
    skip?: number;
}): Promise<Connection[]> => {
    const response = await apiClient.get<Connection[]>('/connections', { params });
    return response.data;
};

export const apiInitiateConnection = async (data: ConnectionCreateRequest): Promise<ConnectionInitiateResponse> => {
    const response = await apiClient.post<ConnectionInitiateResponse>('/connections', data);
    return response.data;
};

export const apiGetConnectionStatus = async (connectionId: string): Promise<ConnectionStatusResponse> => {
    const response = await apiClient.get<ConnectionStatusResponse>(`/connections/${connectionId}/status`);
    return response.data;
};

export const apiRevokeConnection = async (connectionId: string): Promise<void> => {
    await apiClient.delete(`/connections/${connectionId}`);
};

// Workflows & Versioning API (Phase 6)
export type WorkflowStatus = 'draft' | 'ready' | 'active' | 'paused' | 'needs_attention' | 'archived';

export interface DataMapping {
    target_field: string;
    source: string;
    value?: any;
}

export interface StepCondition {
    field: string;
    operator: string;
    value?: any;
}

export interface ErrorPolicy {
    strategy: string;
    max_retries: number;
    retry_delay_seconds: number;
    backoff_multiplier: number;
}

export interface WorkflowStep {
    id: string;
    name: string;
    tool_id: string;
    provider: string;
    app_name?: string;
    arguments: Record<string, any>;
    data_mappings: DataMapping[];
    dependencies: string[];
    conditions: StepCondition[];
    error_policy: ErrorPolicy;
}

export interface TriggerConfig {
    type: string;
    provider?: string;
    event?: string;
    cron?: string;
    interval_seconds?: number;
    parameters: Record<string, any>;
}

export interface WorkflowDetail {
    id: string;
    workspace_id: string;
    name: string;
    description?: string;
    status: WorkflowStatus;
    schema_version: string;
    current_version: number;
    active_version?: number;
    required_integrations: string[];
    trigger: TriggerConfig;
    steps: WorkflowStep[];
    validation_errors?: string[];
    approved_at?: string;
    approved_by?: string;
    created_at: string;
    updated_at: string;
}

export interface WorkflowVersionRecord {
    id: string;
    workspace_id: string;
    workflow_id: string;
    version: number;
    definition: {
        name: string;
        description?: string;
        status: string;
        required_integrations: string[];
        trigger: TriggerConfig;
        steps: WorkflowStep[];
    };
    change_summary?: string;
    created_by: string;
    created_at: string;
}

export interface WorkflowValidationResponse {
    valid: boolean;
    workflow_id: string;
    version: number;
    definition_errors: string[];
    integration_errors: string[];
    errors: string[];
}

export interface ToolItem {
    id: string;
    provider: string;
    name: string;
    description: string;
    input_schema: {
        type?: string;
        properties?: Record<string, any>;
        required?: string[];
    };
    connection_required: boolean;
    scopes: string[];
    risk_category: string;
    tags: string[];
}

export const apiListWorkflows = async (): Promise<WorkflowDetail[]> => {
    const res = await apiClient.get<WorkflowDetail[]>('/workflows');
    return res.data;
};

export const apiGetWorkflow = async (workflowId: string): Promise<WorkflowDetail> => {
    const res = await apiClient.get<WorkflowDetail>(`/workflows/${workflowId}`);
    return res.data;
};

export const apiCreateWorkflow = async (data: Partial<WorkflowDetail>): Promise<WorkflowDetail> => {
    const res = await apiClient.post<WorkflowDetail>('/workflows', data);
    return res.data;
};

export const apiUpdateWorkflow = async (
    workflowId: string,
    data: Partial<WorkflowDetail> & { change_summary?: string }
): Promise<WorkflowDetail> => {
    const res = await apiClient.patch<WorkflowDetail>(`/workflows/${workflowId}`, data);
    return res.data;
};

export const apiValidateWorkflow = async (workflowId: string, version?: number): Promise<WorkflowValidationResponse> => {
    const res = await apiClient.post<WorkflowValidationResponse>(`/workflows/${workflowId}/validate`, null, {
        params: version ? { version } : undefined,
    });
    return res.data;
};

export const apiActivateWorkflow = async (
    workflowId: string,
    approved: boolean,
    version?: number
): Promise<WorkflowDetail> => {
    const res = await apiClient.post<WorkflowDetail>(`/workflows/${workflowId}/activate`, {
        approved,
        version,
    });
    return res.data;
};

export const apiPauseWorkflow = async (workflowId: string): Promise<WorkflowDetail> => {
    const res = await apiClient.post<WorkflowDetail>(`/workflows/${workflowId}/pause`);
    return res.data;
};

export const apiResumeWorkflow = async (workflowId: string): Promise<WorkflowDetail> => {
    const res = await apiClient.post<WorkflowDetail>(`/workflows/${workflowId}/resume`);
    return res.data;
};

export const apiArchiveWorkflow = async (workflowId: string): Promise<WorkflowDetail> => {
    const res = await apiClient.post<WorkflowDetail>(`/workflows/${workflowId}/archive`);
    return res.data;
};

export const apiListWorkflowVersions = async (workflowId: string): Promise<WorkflowVersionRecord[]> => {
    const res = await apiClient.get<WorkflowVersionRecord[]>(`/workflows/${workflowId}/versions`);
    return res.data;
};

export const apiGetWorkflowVersion = async (workflowId: string, version: number): Promise<WorkflowVersionRecord> => {
    const res = await apiClient.get<WorkflowVersionRecord>(`/workflows/${workflowId}/versions/${version}`);
    return res.data;
};

export interface WorkflowPromptEditResponse {
    generation_id: string;
    workflow_id: string;
    workflow?: WorkflowDetail;
    result: {
        success: boolean;
        status: string;
        workflow?: any;
        clarifications?: any[];
        explanation?: any;
        errors?: string[];
    };
}

export const apiPromptEditWorkflow = async (
    workflowId: string,
    prompt: string,
    saveDraft: boolean = true
): Promise<WorkflowPromptEditResponse> => {
    const res = await apiClient.post<WorkflowPromptEditResponse>(`/workflows/${workflowId}/prompt-edit`, {
        prompt,
        save_draft: saveDraft,
    });
    return res.data;
};

export const apiListTools = async (): Promise<ToolItem[]> => {
    const res = await apiClient.get<ToolItem[]>('/tools');
    return res.data;
};

// ==========================================
// Phase 9: Execution History & Observability
// ==========================================

export interface ExecutionRecord {
    id: string;
    workspace_id: string;
    workflow_id: string;
    workflow_version: number;
    status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
    triggered_by: string;
    trigger_event: Record<string, any>;
    started_at: string;
    completed_at?: string;
    duration_ms?: number;
    error?: string;
    steps?: Record<string, any>;
    temporal_workflow_id?: string;
    correlation_id?: string;
}

export interface StepExecutionRecord {
    id: string;
    workspace_id: string;
    execution_id: string;
    workflow_id: string;
    workflow_version: number;
    step_id: string;
    step_name: string;
    attempt: number;
    status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
    idempotency_key: string;
    correlation_id?: string;
    input_data: Record<string, any>;
    output_data?: Record<string, any>;
    error?: string;
    skip_reason?: string;
    started_at: string;
    completed_at?: string;
    execution_time_ms?: number;
}

export interface ExecutionMetrics {
    workspace_id: string;
    workflow_id?: string;
    timeframe_hours: number;
    total_executions: number;
    completed: number;
    failed: number;
    running: number;
    pending: number;
    cancelled: number;
    success_rate_percent: number;
    failure_rate_percent: number;
    avg_duration_ms: number;
    avg_trigger_delay_ms: number;
    avg_queue_latency_ms: number;
}

export interface StepMetricItem {
    step_id: string;
    step_name: string;
    total_attempts: number;
    retries: number;
    completed: number;
    failures: number;
    avg_duration_ms: number;
    min_duration_ms: number;
    max_duration_ms: number;
}

export interface StepMetrics {
    workspace_id: string;
    workflow_id?: string;
    timeframe_hours: number;
    total_retries: number;
    step_metrics: StepMetricItem[];
    provider_error_breakdown: Record<string, number>;
}

export interface AlertItem {
    id: string;
    workspace_id: string;
    category: 'repeated_failures' | 'expired_connections' | 'webhook_renewal_failures' | 'unavailable_workers';
    severity: 'info' | 'warning' | 'critical';
    title: string;
    description: string;
    resource_type: string;
    resource_id?: string;
    created_at: string;
    metadata: Record<string, any>;
}

export interface DLQEvent {
    id: string;
    workspace_id: string;
    event_id: string;
    workflow_id?: string;
    workflow_version?: number;
    provider: string;
    event_type: string;
    error: string;
    payload: Record<string, any>;
    headers?: Record<string, string>;
    failure_stage: string;
    status: 'failed' | 'replaying' | 'resolved' | 'discarded';
    replay_count: number;
    last_replayed_at?: string;
    created_at: string;
    updated_at: string;
}

export const apiListExecutions = async (params?: {
    workflow_id?: string;
    status?: string;
    limit?: number;
    skip?: number;
}): Promise<ExecutionRecord[]> => {
    const res = await apiClient.get<ExecutionRecord[]>('/executions', { params });
    return res.data;
};

export const apiGetExecution = async (executionId: string): Promise<ExecutionRecord> => {
    const res = await apiClient.get<ExecutionRecord>(`/executions/${executionId}`);
    return res.data;
};

export const apiGetExecutionSteps = async (executionId: string): Promise<StepExecutionRecord[]> => {
    const res = await apiClient.get<StepExecutionRecord[]>(`/executions/${executionId}/steps`);
    return res.data;
};

export const apiCancelExecution = async (executionId: string): Promise<{ message: string; execution_id: string }> => {
    const res = await apiClient.post<{ message: string; execution_id: string }>(`/executions/${executionId}/cancel`);
    return res.data;
};

export const apiRunWorkflow = async (
    workflowId: string,
    triggerData?: Record<string, any>,
    version?: number
): Promise<any> => {
    const res = await apiClient.post(`/workflows/${workflowId}/run`, {
        trigger_data: triggerData || {},
        version,
    });
    return res.data;
};

export const apiGetExecutionMetrics = async (
    workflowId?: string,
    timeframeHours: number = 24
): Promise<ExecutionMetrics> => {
    const res = await apiClient.get<ExecutionMetrics>('/observability/metrics/executions', {
        params: { workflow_id: workflowId, timeframe_hours: timeframeHours },
    });
    return res.data;
};

export const apiGetStepMetrics = async (
    workflowId?: string,
    timeframeHours: number = 24
): Promise<StepMetrics> => {
    const res = await apiClient.get<StepMetrics>('/observability/metrics/steps', {
        params: { workflow_id: workflowId, timeframe_hours: timeframeHours },
    });
    return res.data;
};

export const apiGetAlerts = async (): Promise<AlertItem[]> => {
    const res = await apiClient.get<AlertItem[]>('/observability/alerts');
    return res.data;
};

export const apiListDLQEvents = async (params?: {
    workflow_id?: string;
    status?: string;
    limit?: number;
    skip?: number;
}): Promise<DLQEvent[]> => {
    const res = await apiClient.get<DLQEvent[]>('/observability/dlq', { params });
    return res.data;
};

export const apiGetDLQEvent = async (dlqId: string): Promise<DLQEvent> => {
    const res = await apiClient.get<DLQEvent>(`/observability/dlq/${dlqId}`);
    return res.data;
};

export const apiReplayDLQEvent = async (
    dlqId: string,
    options?: { idempotency_key?: string; use_latest_version?: boolean }
): Promise<any> => {
    const res = await apiClient.post(`/observability/dlq/${dlqId}/replay`, options || {});
    return res.data;
};

export const apiDiscardDLQEvent = async (dlqId: string): Promise<any> => {
    const res = await apiClient.post(`/observability/dlq/${dlqId}/discard`);
    return res.data;
};

export const getLiveUpdatesUrl = (executionId?: string): string => {
    const token = localStorage.getItem('nexus_access_token') || '';
    const wsId = localStorage.getItem('nexus_active_workspace_id') || '';
    const base = `${API_BASE_URL}/api/v1/observability/live`;
    const params = new URLSearchParams();
    if (executionId) params.append('execution_id', executionId);
    if (token) params.append('token', token);
    if (wsId) params.append('workspace_id', wsId);
    return `${base}?${params.toString()}`;
};


