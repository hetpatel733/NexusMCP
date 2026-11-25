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
