import React, { createContext, useContext, useEffect, useState } from 'react';
import {
    apiGetMe,
    apiListWorkspaces,
    apiLogin,
    apiLogout,
    apiRegister,
    User,
    Workspace,
} from '../api/client';

interface AuthContextType {
    user: User | null;
    activeWorkspace: Workspace | null;
    workspaces: Workspace[];
    isAuthenticated: boolean;
    isLoading: boolean;
    login: (email: string, password: string) => Promise<void>;
    register: (email: string, password: string, fullName: string) => Promise<void>;
    logout: () => Promise<void>;
    switchWorkspace: (workspace: Workspace) => void;
    refreshWorkspaces: () => Promise<void>;
    refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [user, setUser] = useState<User | null>(null);
    const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
    const [activeWorkspace, setActiveWorkspace] = useState<Workspace | null>(null);
    const [isLoading, setIsLoading] = useState<boolean>(true);

    const loadWorkspacesAndActive = async (preferredId?: string) => {
        try {
            const wsList = await apiListWorkspaces();
            setWorkspaces(wsList);
            const savedWsId = preferredId || localStorage.getItem('nexus_active_workspace_id');
            const found = wsList.find((w) => w.id === savedWsId);
            if (found) {
                setActiveWorkspace(found);
                localStorage.setItem('nexus_active_workspace_id', found.id);
            } else if (wsList.length > 0) {
                setActiveWorkspace(wsList[0]);
                localStorage.setItem('nexus_active_workspace_id', wsList[0].id);
            } else {
                setActiveWorkspace(null);
                localStorage.removeItem('nexus_active_workspace_id');
            }
        } catch {
            setWorkspaces([]);
            setActiveWorkspace(null);
        }
    };

    const initializeAuth = async () => {
        const token = localStorage.getItem('nexus_access_token');
        if (!token) {
            setIsLoading(false);
            return;
        }

        try {
            const userData = await apiGetMe();
            setUser(userData);
            await loadWorkspacesAndActive();
        } catch {
            localStorage.removeItem('nexus_access_token');
            localStorage.removeItem('nexus_refresh_token');
            setUser(null);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        initializeAuth();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const login = async (email: string, password: string) => {
        const data = await apiLogin(email, password);
        localStorage.setItem('nexus_access_token', data.access_token);
        localStorage.setItem('nexus_refresh_token', data.refresh_token);
        setUser(data.user);
        await loadWorkspacesAndActive();
    };

    const register = async (email: string, password: string, fullName: string) => {
        const data = await apiRegister(email, password, fullName);
        localStorage.setItem('nexus_access_token', data.access_token);
        localStorage.setItem('nexus_refresh_token', data.refresh_token);
        setUser(data.user);
        await loadWorkspacesAndActive();
    };

    const logout = async () => {
        await apiLogout();
        setUser(null);
        setWorkspaces([]);
        setActiveWorkspace(null);
    };

    const switchWorkspace = (workspace: Workspace) => {
        setActiveWorkspace(workspace);
        localStorage.setItem('nexus_active_workspace_id', workspace.id);
    };

    const refreshWorkspaces = async () => {
        await loadWorkspacesAndActive(activeWorkspace?.id);
    };

    const refreshUser = async () => {
        const userData = await apiGetMe();
        setUser(userData);
    };

    return (
        <AuthContext.Provider
            value={{
                user,
                activeWorkspace,
                workspaces,
                isAuthenticated: !!user,
                isLoading,
                login,
                register,
                logout,
                switchWorkspace,
                refreshWorkspaces,
                refreshUser,
            }}
        >
            {children}
        </AuthContext.Provider>
    );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = (): AuthContextType => {
    const context = useContext(AuthContext);
    if (!context) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
};
