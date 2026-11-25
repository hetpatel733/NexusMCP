import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiListWorkflows, WorkflowDetail, WorkflowStatus } from '../api/client';
import { useAuth } from '../context/AuthContext';

export const WorkflowsListPage: React.FC = () => {
    const { isAuthenticated, activeWorkspace } = useAuth();
    const navigate = useNavigate();
    const [workflows, setWorkflows] = useState<WorkflowDetail[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const loadWorkflows = async () => {
        if (!activeWorkspace) return;
        setLoading(true);
        setError(null);
        try {
            const list = await apiListWorkflows();
            setWorkflows(list);
        } catch (err: any) {
            setError(err.response?.data?.detail || err.message || 'Failed to load workflows');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (isAuthenticated && activeWorkspace) {
            loadWorkflows();
        }
    }, [isAuthenticated, activeWorkspace]);

    const getStatusBadge = (status: WorkflowStatus) => {
        const styles: Record<WorkflowStatus, { bg: string; color: string; label: string }> = {
            draft: { bg: '#f1f5f9', color: '#475569', label: 'Draft' },
            ready: { bg: '#e0e7ff', color: '#3730a3', label: 'Ready for Review' },
            active: { bg: '#dcfce7', color: '#166534', label: 'Active' },
            paused: { bg: '#fef3c7', color: '#92400e', label: 'Paused' },
            needs_attention: { bg: '#fee2e2', color: '#991b1b', label: 'Needs Attention' },
            archived: { bg: '#f3f4f6', color: '#6b7280', label: 'Archived' },
        };
        const s = styles[status] || styles.draft;
        return (
            <span
                style={{
                    backgroundColor: s.bg,
                    color: s.color,
                    padding: '0.25rem 0.65rem',
                    borderRadius: '9999px',
                    fontSize: '0.75rem',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    display: 'inline-block',
                }}
            >
                {s.label}
            </span>
        );
    };

    if (!isAuthenticated) {
        return (
            <div style={{ textAlign: 'center', padding: '3rem' }}>
                <h2>Authentication Required</h2>
                <p>Please log in to inspect and manage workflows.</p>
            </div>
        );
    }

    return (
        <div style={{ maxWidth: '1000px', margin: '0 auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
                <div>
                    <h1 style={{ fontSize: '1.75rem', margin: '0 0 0.5rem 0' }}>Automated Workflows</h1>
                    <p style={{ color: '#475569', margin: 0 }}>
                        Inspect, edit, validate, version, and activate workflows strictly scoped to {activeWorkspace?.name}.
                    </p>
                </div>
                <div style={{ display: 'flex', gap: '0.75rem' }}>
                    <Link
                        to="/designer"
                        style={{
                            padding: '0.6rem 1rem',
                            backgroundColor: '#2563eb',
                            color: '#fff',
                            textDecoration: 'none',
                            borderRadius: '6px',
                            fontWeight: 600,
                            fontSize: '0.875rem',
                        }}
                    >
                        + Design with AI
                    </Link>
                </div>
            </div>

            {error && (
                <div style={{ padding: '0.75rem 1rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '6px', marginBottom: '1rem' }}>
                    {error}
                </div>
            )}

            {loading ? (
                <div style={{ padding: '2rem', textAlign: 'center', color: '#64748b' }}>Loading workflows...</div>
            ) : workflows.length === 0 ? (
                <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '3rem', textAlign: 'center' }}>
                    <h3 style={{ marginTop: 0, color: '#1e293b' }}>No Workflows Found</h3>
                    <p style={{ color: '#64748b', marginBottom: '1.5rem' }}>
                        Generate a workflow using natural language or create your first automated orchestration.
                    </p>
                    <Link
                        to="/designer"
                        style={{
                            padding: '0.6rem 1.2rem',
                            backgroundColor: '#2563eb',
                            color: '#fff',
                            textDecoration: 'none',
                            borderRadius: '6px',
                            fontWeight: 600,
                        }}
                    >
                        Open AI Designer
                    </Link>
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                    {workflows.map((wf) => (
                        <div
                            key={wf.id}
                            style={{
                                backgroundColor: '#fff',
                                border: '1px solid #e2e8f0',
                                borderRadius: '8px',
                                padding: '1.25rem 1.5rem',
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                flexWrap: 'wrap',
                                gap: '1rem',
                                transition: 'border-color 0.15s ease',
                            }}
                        >
                            <div style={{ flex: '1 1 300px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.35rem' }}>
                                    <h3 style={{ margin: 0, fontSize: '1.1rem', color: '#0f172a' }}>{wf.name}</h3>
                                    {getStatusBadge(wf.status)}
                                    <span style={{ fontSize: '0.8rem', color: '#64748b', backgroundColor: '#f8fafc', border: '1px solid #e2e8f0', padding: '0.15rem 0.45rem', borderRadius: '4px' }}>
                                        v{wf.current_version} {wf.active_version ? `(active: v${wf.active_version})` : ''}
                                    </span>
                                </div>
                                <p style={{ color: '#64748b', fontSize: '0.875rem', margin: '0 0 0.5rem 0' }}>
                                    {wf.description || 'No description provided.'}
                                </p>
                                <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                                    Trigger: <strong style={{ color: '#475569' }}>{wf.trigger?.type || 'manual'}</strong>
                                    {wf.required_integrations && wf.required_integrations.length > 0 && (
                                        <span style={{ marginLeft: '1rem' }}>
                                            Integrations: {wf.required_integrations.join(', ')}
                                        </span>
                                    )}
                                    <span style={{ marginLeft: '1rem' }}>
                                        Updated: {new Date(wf.updated_at).toLocaleString()}
                                    </span>
                                </div>
                            </div>
                            <div>
                                <button
                                    onClick={() => navigate(`/workflows/${wf.id}`)}
                                    style={{
                                        padding: '0.5rem 1rem',
                                        backgroundColor: '#f1f5f9',
                                        border: '1px solid #cbd5e1',
                                        borderRadius: '6px',
                                        color: '#0f172a',
                                        fontWeight: 600,
                                        fontSize: '0.875rem',
                                        cursor: 'pointer',
                                    }}
                                >
                                    Inspect / Edit &rarr;
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};
