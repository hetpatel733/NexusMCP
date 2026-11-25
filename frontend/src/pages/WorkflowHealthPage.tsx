import React, { useEffect, useState, useCallback } from 'react';
import {
    apiGetAlerts,
    apiGetExecutionMetrics,
    apiGetStepMetrics,
    apiListWorkflows,
    AlertItem,
    ExecutionMetrics,
    StepMetrics,
    WorkflowDetail,
} from '../api/client';
import { useAuth } from '../context/AuthContext';

export const WorkflowHealthPage: React.FC = () => {
    const { activeWorkspace } = useAuth();

    const [workflows, setWorkflows] = useState<WorkflowDetail[]>([]);
    const [selectedWorkflowId, setSelectedWorkflowId] = useState<string>('');
    const [timeframeHours, setTimeframeHours] = useState<number>(24);

    const [execMetrics, setExecMetrics] = useState<ExecutionMetrics | null>(null);
    const [stepMetrics, setStepMetrics] = useState<StepMetrics | null>(null);
    const [alerts, setAlerts] = useState<AlertItem[]>([]);

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const loadHealthData = useCallback(async () => {
        if (!activeWorkspace) return;
        try {
            setLoading(true);
            setError(null);
            const [wfList, em, sm, al] = await Promise.all([
                apiListWorkflows().catch(() => []),
                apiGetExecutionMetrics(selectedWorkflowId || undefined, timeframeHours),
                apiGetStepMetrics(selectedWorkflowId || undefined, timeframeHours),
                apiGetAlerts().catch(() => []),
            ]);
            setWorkflows(wfList);
            setExecMetrics(em);
            setStepMetrics(sm);
            setAlerts(al);
        } catch (err: any) {
            setError(err.message || 'Failed to load observability metrics');
        } finally {
            setLoading(false);
        }
    }, [activeWorkspace, selectedWorkflowId, timeframeHours]);

    useEffect(() => {
        loadHealthData();
    }, [loadHealthData]);

    return (
        <div>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
                <div>
                    <h1 style={{ fontSize: '1.75rem', margin: '0 0 0.5rem 0' }}>Workflow Health & Metrics</h1>
                    <p style={{ color: '#64748b', margin: 0 }}>
                        Real-time operational observability: latency, step durations, retry rates, and provider errors.
                    </p>
                </div>
                <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
                    <select
                        value={timeframeHours}
                        onChange={(e) => setTimeframeHours(Number(e.target.value))}
                        style={{ padding: '0.4rem 0.6rem', borderRadius: '4px', border: '1px solid #cbd5e1', fontSize: '0.85rem' }}
                    >
                        <option value={1}>Last 1 Hour</option>
                        <option value={6}>Last 6 Hours</option>
                        <option value={24}>Last 24 Hours</option>
                        <option value={72}>Last 3 Days</option>
                        <option value={168}>Last 7 Days</option>
                    </select>

                    <button
                        onClick={loadHealthData}
                        style={{
                            padding: '0.4rem 0.8rem',
                            backgroundColor: '#fff',
                            border: '1px solid #cbd5e1',
                            borderRadius: '6px',
                            cursor: 'pointer',
                            fontSize: '0.85rem',
                        }}
                    >
                        ↻ Refresh
                    </button>
                </div>
            </div>

            {/* Error Message */}
            {error && (
                <div style={{ padding: '0.75rem 1rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '6px', marginBottom: '1rem' }}>
                    {error}
                </div>
            )}

            {/* Filter */}
            <div style={{ backgroundColor: '#fff', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0', marginBottom: '1.5rem', display: 'flex', gap: '1rem', alignItems: 'center' }}>
                <label style={{ fontSize: '0.85rem', fontWeight: 600, color: '#475569' }}>Scope to Workflow:</label>
                <select
                    value={selectedWorkflowId}
                    onChange={(e) => setSelectedWorkflowId(e.target.value)}
                    style={{ padding: '0.4rem 0.6rem', borderRadius: '4px', border: '1px solid #cbd5e1', fontSize: '0.85rem' }}
                >
                    <option value="">All Workflows (Workspace Wide)</option>
                    {workflows.map((wf) => (
                        <option key={wf.id} value={wf.id}>{wf.name}</option>
                    ))}
                </select>
            </div>

            {loading ? (
                <div style={{ padding: '3rem', textAlign: 'center', color: '#64748b' }}>Calculating health metrics...</div>
            ) : (
                <>
                    {/* Primary KPI Cards */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', marginBottom: '1.5rem' }}>
                        <div style={{ backgroundColor: '#fff', padding: '1.25rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                            <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Success Rate</div>
                            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: '#16a34a', marginTop: '0.25rem' }}>
                                {execMetrics ? `${execMetrics.success_rate_percent}%` : '0%'}
                            </div>
                            <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: '0.25rem' }}>
                                {execMetrics?.completed || 0} of {execMetrics?.total_executions || 0} successful
                            </div>
                        </div>

                        <div style={{ backgroundColor: '#fff', padding: '1.25rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                            <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Trigger Intake Delay</div>
                            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: '#0f172a', marginTop: '0.25rem' }}>
                                {execMetrics ? `${execMetrics.avg_trigger_delay_ms.toFixed(0)}ms` : '0ms'}
                            </div>
                            <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: '0.25rem' }}>
                                Webhook ingress to dispatch
                            </div>
                        </div>

                        <div style={{ backgroundColor: '#fff', padding: '1.25rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                            <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Queue Latency</div>
                            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: '#0f172a', marginTop: '0.25rem' }}>
                                {execMetrics ? `${execMetrics.avg_queue_latency_ms.toFixed(0)}ms` : '0ms'}
                            </div>
                            <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: '0.25rem' }}>
                                Temporal queue scheduling delay
                            </div>
                        </div>

                        <div style={{ backgroundColor: '#fff', padding: '1.25rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                            <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Total Retried Steps</div>
                            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: (stepMetrics?.total_retries || 0) > 0 ? '#b45309' : '#0f172a', marginTop: '0.25rem' }}>
                                {stepMetrics?.total_retries || 0}
                            </div>
                            <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: '0.25rem' }}>
                                Automatic transient retry attempts
                            </div>
                        </div>
                    </div>

                    {/* Operational Alerts Section */}
                    <div style={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e2e8f0', padding: '1.25rem', marginBottom: '1.5rem' }}>
                        <h3 style={{ margin: '0 0 1rem 0', fontSize: '1rem' }}>Active Operational Alerts ({alerts.length})</h3>
                        {alerts.length === 0 ? (
                            <div style={{ color: '#16a34a', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                <span>✓</span> All systems healthy: No worker downtime, expired connections, or repeated failures.
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                {alerts.map((al) => (
                                    <div
                                        key={al.id}
                                        style={{
                                            padding: '0.75rem 1rem',
                                            borderRadius: '6px',
                                            backgroundColor: al.severity === 'critical' ? '#fee2e2' : '#fef3c7',
                                            border: `1px solid ${al.severity === 'critical' ? '#fca5a5' : '#fde68a'}`,
                                            color: al.severity === 'critical' ? '#991b1b' : '#92400e',
                                            display: 'flex',
                                            justifyContent: 'space-between',
                                            alignItems: 'center',
                                        }}
                                    >
                                        <div>
                                            <strong>[{al.severity.toUpperCase()}] {al.title}:</strong> {al.description}
                                        </div>
                                        <span style={{ fontSize: '0.75rem', fontWeight: 600 }}>{al.category}</span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Step Execution Performance Table */}
                    <div style={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e2e8f0', overflow: 'hidden', marginBottom: '1.5rem' }}>
                        <div style={{ padding: '1rem', borderBottom: '1px solid #e2e8f0' }}>
                            <h3 style={{ margin: 0, fontSize: '1rem' }}>Step Activity Performance & Duration</h3>
                        </div>

                        {!stepMetrics || stepMetrics.step_metrics.length === 0 ? (
                            <div style={{ padding: '2rem', textAlign: 'center', color: '#64748b' }}>
                                No step activity samples recorded in this timeframe.
                            </div>
                        ) : (
                            <div style={{ overflowX: 'auto' }}>
                                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.85rem' }}>
                                    <thead>
                                        <tr style={{ backgroundColor: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
                                            <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Step Name / ID</th>
                                            <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Total Attempts</th>
                                            <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Retries</th>
                                            <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Completed</th>
                                            <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Failures</th>
                                            <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Avg Duration</th>
                                            <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Min / Max Duration</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {stepMetrics.step_metrics.map((s) => (
                                            <tr key={s.step_id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                                                <td style={{ padding: '0.75rem 1rem' }}>
                                                    <div style={{ fontWeight: 600, color: '#0f172a' }}>{s.step_name}</div>
                                                    <div style={{ fontSize: '0.75rem', color: '#64748b' }}>{s.step_id}</div>
                                                </td>
                                                <td style={{ padding: '0.75rem 1rem' }}>{s.total_attempts}</td>
                                                <td style={{ padding: '0.75rem 1rem', color: s.retries > 0 ? '#b45309' : '#64748b', fontWeight: s.retries > 0 ? 600 : 400 }}>
                                                    {s.retries}
                                                </td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#16a34a' }}>{s.completed}</td>
                                                <td style={{ padding: '0.75rem 1rem', color: s.failures > 0 ? '#dc2626' : '#64748b', fontWeight: s.failures > 0 ? 600 : 400 }}>
                                                    {s.failures}
                                                </td>
                                                <td style={{ padding: '0.75rem 1rem' }}>{s.avg_duration_ms}ms</td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#64748b' }}>{s.min_duration_ms}ms / {s.max_duration_ms}ms</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>

                    {/* Provider Error Categorization */}
                    {stepMetrics && Object.keys(stepMetrics.provider_error_breakdown).length > 0 && (
                        <div style={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e2e8f0', padding: '1.25rem' }}>
                            <h3 style={{ margin: '0 0 1rem 0', fontSize: '1rem' }}>Provider Error Breakdown</h3>
                            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                                {Object.entries(stepMetrics.provider_error_breakdown).map(([category, count]) => (
                                    <div
                                        key={category}
                                        style={{
                                            padding: '0.75rem 1.25rem',
                                            backgroundColor: '#f8fafc',
                                            border: '1px solid #e2e8f0',
                                            borderRadius: '6px',
                                        }}
                                    >
                                        <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>
                                            {category.replace('_', ' ')}
                                        </div>
                                        <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#dc2626', marginTop: '0.25rem' }}>
                                            {count}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    );
};
