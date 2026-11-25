import React, { useEffect, useState } from 'react';
import { apiListAuditLogs, AuditLog } from '../api/client';
import { useAuth } from '../context/AuthContext';

export const AuditLogsPage: React.FC = () => {
    const { activeWorkspace } = useAuth();
    const [logs, setLogs] = useState<AuditLog[]>([]);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);

    const isAuthorized = activeWorkspace?.role === 'owner' || activeWorkspace?.role === 'admin';

    useEffect(() => {
        const loadLogs = async () => {
            if (!activeWorkspace || !isAuthorized) {
                setLoading(false);
                return;
            }
            try {
                setLoading(true);
                const data = await apiListAuditLogs();
                setLogs(data);
            } catch (err: any) {
                setError(err.response?.data?.detail || 'Failed to fetch audit logs');
            } finally {
                setLoading(false);
            }
        };
        loadLogs();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeWorkspace?.id]);

    if (!isAuthorized) {
        return (
            <div style={{ padding: '2rem', textAlign: 'center' }}>
                <h2>Access Restricted</h2>
                <p style={{ color: '#64748b' }}>
                    Audit logs are restricted to workspace Owners and Admins only.
                </p>
            </div>
        );
    }

    return (
        <div>
            <h1 style={{ fontSize: '1.75rem', marginBottom: '0.5rem' }}>Audit Trail</h1>
            <p style={{ color: '#475569' }}>
                Immutable security and administrative log for workspace: <strong>{activeWorkspace?.name}</strong>
            </p>

            {loading && <p>Loading audit events...</p>}
            {error && (
                <div style={{ padding: '1rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '6px', marginBottom: '1rem' }}>
                    {error}
                </div>
            )}

            {!loading && logs.length === 0 && (
                <div style={{ padding: '2rem', textAlign: 'center', backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px' }}>
                    <p style={{ color: '#64748b' }}>No audit events recorded yet for this workspace.</p>
                </div>
            )}

            {!loading && logs.length > 0 && (
                <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', overflow: 'hidden' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
                        <thead>
                            <tr style={{ backgroundColor: '#f8fafc', borderBottom: '1px solid #e2e8f0', textAlign: 'left' }}>
                                <th style={{ padding: '0.75rem 1rem' }}>Action</th>
                                <th style={{ padding: '0.75rem 1rem' }}>User / Actor</th>
                                <th style={{ padding: '0.75rem 1rem' }}>Target Resource</th>
                                <th style={{ padding: '0.75rem 1rem' }}>IP Address</th>
                                <th style={{ padding: '0.75rem 1rem' }}>Timestamp</th>
                                <th style={{ padding: '0.75rem 1rem' }}>Details</th>
                            </tr>
                        </thead>
                        <tbody>
                            {logs.map((log) => (
                                <tr key={log.id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                                    <td style={{ padding: '0.75rem 1rem' }}>
                                        <span style={{ fontWeight: 600, color: '#0f172a' }}>{log.action}</span>
                                    </td>
                                    <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>
                                        {log.user_email || log.user_id || 'System'}
                                    </td>
                                    <td style={{ padding: '0.75rem 1rem', color: '#64748b' }}>
                                        {log.resource_type ? `${log.resource_type} (${log.resource_id || ''})` : '—'}
                                    </td>
                                    <td style={{ padding: '0.75rem 1rem', color: '#64748b', fontFamily: 'monospace' }}>
                                        {log.ip_address || '—'}
                                    </td>
                                    <td style={{ padding: '0.75rem 1rem', color: '#64748b' }}>
                                        {new Date(log.created_at).toLocaleString()}
                                    </td>
                                    <td style={{ padding: '0.75rem 1rem' }}>
                                        <pre style={{ margin: 0, fontSize: '0.75rem', backgroundColor: '#f8fafc', padding: '0.25rem 0.5rem', borderRadius: '4px', maxWidth: '300px', overflowX: 'auto' }}>
                                            {JSON.stringify(log.details || {}, null, 1)}
                                        </pre>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};
