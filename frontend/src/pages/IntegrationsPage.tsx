import React, { useCallback, useEffect, useState } from 'react';
import {
    apiGetConnectionStatus,
    apiInitiateConnection,
    apiListConnections,
    apiRevokeConnection,
    Connection,
    ConnectionStatus,
} from '../api/client';
import { useAuth } from '../context/AuthContext';

const STATUS_LABELS: Record<ConnectionStatus, string> = {
    connected: 'Connected',
    disconnected: 'Disconnected',
    expired: 'Expired',
    action_required: 'Action Required',
    unknown: 'Unknown',
};

const STATUS_COLORS: Record<ConnectionStatus, string> = {
    connected: '#16a34a',
    disconnected: '#64748b',
    expired: '#dc2626',
    action_required: '#ca8a04',
    unknown: '#64748b',
};

export const IntegrationsPage: React.FC = () => {
    const { activeWorkspace } = useAuth();
    const [connections, setConnections] = useState<Connection[]>([]);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);
    const [connecting, setConnecting] = useState<string | null>(null);
    const [revoking, setRevoking] = useState<string | null>(null);

    const loadConnections = useCallback(async () => {
        if (!activeWorkspace) return;
        setLoading(true);
        setError(null);
        try {
            const data = await apiListConnections();
            setConnections(data);
        } catch (err: any) {
            setError(err.response?.data?.detail || 'Failed to load connections');
        } finally {
            setLoading(false);
        }
    }, [activeWorkspace]);

    useEffect(() => {
        loadConnections();
    }, [loadConnections]);

    const handleConnect = async (appName: string) => {
        setConnecting(appName);
        setError(null);
        try {
            const result = await apiInitiateConnection({ app_name: appName });
            if (result.redirect_url) {
                window.open(result.redirect_url, '_blank');
            }
            await loadConnections();
        } catch (err: any) {
            setError(err.response?.data?.detail || `Failed to connect to ${appName}`);
        } finally {
            setConnecting(null);
        }
    };

    const handleRefreshStatus = async (conn: Connection) => {
        setRevoking(conn.id);
        try {
            const updated = await apiGetConnectionStatus(conn.id);
            setConnections(
                connections.map((c) =>
                    c.id === conn.id ? { ...c, status: updated.status as ConnectionStatus } : c
                )
            );
        } catch {
            // Keep existing status on failure
        } finally {
            setRevoking(null);
        }
    };

    const handleRevoke = async (conn: Connection) => {
        if (!window.confirm(`Are you sure you want to disconnect ${conn.app_name}?`)) return;
        setRevoking(conn.id);
        try {
            await apiRevokeConnection(conn.id);
            setConnections(connections.filter((c) => c.id !== conn.id));
        } catch (err: any) {
            setError(err.response?.data?.detail || `Failed to disconnect ${conn.app_name}`);
        } finally {
            setRevoking(null);
        }
    };

    const statusBadge = (status: ConnectionStatus) => (
        <span
            style={{
                fontSize: '0.75rem',
                padding: '0.2rem 0.6rem',
                borderRadius: '12px',
                color: '#fff',
                backgroundColor: STATUS_COLORS[status],
                fontWeight: 600,
                textTransform: 'capitalize',
            }}
        >
            {STATUS_LABELS[status]}
        </span>
    );

    if (!activeWorkspace) {
        return (
            <div style={{ padding: '2rem', textAlign: 'center', color: '#64748b' }}>
                <h1 style={{ fontSize: '1.75rem' }}>Integrations</h1>
                <p>No active workspace selected.</p>
            </div>
        );
    }

    return (
        <div style={{ maxWidth: '900px', margin: '0 auto', padding: '1.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
                <div>
                    <h1 style={{ fontSize: '1.75rem', margin: 0 }}>Integrations</h1>
                    <p style={{ color: '#475569', margin: '0.25rem 0 0 0' }}>
                        Connect third-party applications to this workspace. Connections are managed through
                        Composio and are isolated to this workspace.
                    </p>
                </div>
            </div>

            {error && (
                <div
                    style={{
                        padding: '1rem',
                        backgroundColor: '#fee2e2',
                        color: '#991b1b',
                        borderRadius: '6px',
                        marginBottom: '1rem',
                    }}
                >
                    {error}
                </div>
            )}

            <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                        <tr style={{ textAlign: 'left', borderBottom: '1px solid #e2e8f0' }}>
                            <th style={{ padding: '1rem', color: '#64748b', fontSize: '0.875rem', fontWeight: 600 }}>App</th>
                            <th style={{ padding: '1rem', color: '#64748b', fontSize: '0.875rem', fontWeight: 600 }}>Status</th>
                            <th style={{ padding: '1rem', color: '#64748b', fontSize: '0.875rem', fontWeight: 600 }}>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr>
                                <td colSpan={3} style={{ padding: '2rem', textAlign: 'center', color: '#64748b' }}>
                                    Loading connections...
                                </td>
                            </tr>
                        ) : connections.length === 0 ? (
                            <tr>
                                <td colSpan={3} style={{ padding: '2rem', textAlign: 'center', color: '#64748b' }}>
                                    No connections yet. Click "Connect" next to an app below to get started.
                                </td>
                            </tr>
                        ) : (
                            connections.map((conn) => (
                                <tr
                                    key={conn.id}
                                    style={{ borderBottom: '1px solid #f1f5f9' }}
                                >
                                    <td style={{ padding: '1rem', fontWeight: 500, color: '#0f172a' }}>
                                        {conn.app_name}
                                    </td>
                                    <td style={{ padding: '1rem' }}>
                                        {statusBadge(conn.status as ConnectionStatus)}
                                    </td>
                                    <td style={{ padding: '1rem', display: 'flex', gap: '0.5rem' }}>
                                        {conn.status === 'connected' ? (
                                            <>
                                                <button
                                                    onClick={() => handleRefreshStatus(conn)}
                                                    disabled={revoking === conn.id}
                                                    style={{
                                                        padding: '0.4rem 0.8rem',
                                                        backgroundColor: '#f1f5f9',
                                                        color: '#0f172a',
                                                        border: '1px solid #cbd5e1',
                                                        borderRadius: '4px',
                                                        fontSize: '0.85rem',
                                                        cursor: 'pointer',
                                                        fontWeight: 500,
                                                        opacity: revoking === conn.id ? 0.6 : 1,
                                                    }}
                                                >
                                                    {revoking === conn.id ? 'Refreshing...' : 'Refresh'}
                                                </button>
                                                <button
                                                    onClick={() => handleRevoke(conn)}
                                                    disabled={revoking === conn.id}
                                                    style={{
                                                        padding: '0.4rem 0.8rem',
                                                        backgroundColor: '#fff',
                                                        color: '#dc2626',
                                                        border: '1px solid #fca5a5',
                                                        borderRadius: '4px',
                                                        fontSize: '0.85rem',
                                                        cursor: 'pointer',
                                                        fontWeight: 500,
                                                        opacity: revoking === conn.id ? 0.6 : 1,
                                                    }}
                                                >
                                                    {revoking === conn.id ? 'Revoking...' : 'Disconnect'}
                                                </button>
                                            </>
                                        ) : (
                                            <>
                                                <button
                                                    onClick={() => handleConnect(conn.app_name)}
                                                    disabled={connecting === conn.app_name}
                                                    style={{
                                                        padding: '0.4rem 0.8rem',
                                                        backgroundColor: '#2563eb',
                                                        color: '#fff',
                                                        border: 'none',
                                                        borderRadius: '4px',
                                                        fontSize: '0.85rem',
                                                        cursor: 'pointer',
                                                        fontWeight: 500,
                                                        opacity: connecting === conn.app_name ? 0.6 : 1,
                                                    }}
                                                >
                                                    {connecting === conn.app_name ? 'Connecting...' : 'Connect'}
                                                </button>
                                            </>
                                        )}
                                    </td>
                                </tr>
                            ))
                        )}
                    </tbody>
                </table>
            </div>

            <div
                style={{
                    marginTop: '2rem',
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
                    gap: '1rem',
                }}
            >
                <h3 style={{ gridColumn: '1 / -1', marginBottom: '0.5rem', color: '#475569' }}>
                    Available Apps
                </h3>
                {[
                    { name: 'Github', app: 'github' },
                    { name: 'Google Workspace', app: 'googleworkspace' },
                    { name: 'Slack', app: 'slack' },
                    { name: 'Notion', app: 'notion' },
                    { name: 'Trello', app: 'trello' },
                    { name: 'Linear', app: 'linear' },
                    { name: 'Jira', app: 'jira' },
                    { name: 'Discord', app: 'discord' },
                ].map((app) => {
                    const alreadyConnected = connections.some((c) => c.app_name === app.app);
                    return (
                        <div
                            key={app.app}
                            style={{
                                backgroundColor: '#fff',
                                border: '1px solid #e2e8f0',
                                borderRadius: '8px',
                                padding: '1rem',
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                            }}
                        >
                            <span style={{ fontWeight: 500, color: '#0f172a' }}>{app.name}</span>
                            <button
                                onClick={() => handleConnect(app.app)}
                                disabled={alreadyConnected || connecting === app.app}
                                style={{
                                    padding: '0.4rem 0.9rem',
                                    backgroundColor: alreadyConnected ? '#e2e8f0' : '#2563eb',
                                    color: alreadyConnected ? '#64748b' : '#fff',
                                    border: 'none',
                                    borderRadius: '4px',
                                    fontSize: '0.85rem',
                                    cursor: alreadyConnected ? 'not-allowed' : 'pointer',
                                    fontWeight: 500,
                                    opacity: connecting === app.app ? 0.6 : 1,
                                }}
                            >
                                {alreadyConnected ? 'Connected' : connecting === app.app ? 'Connecting...' : 'Connect'}
                            </button>
                        </div>
                    );
                })}
            </div>
        </div>
    );
};
