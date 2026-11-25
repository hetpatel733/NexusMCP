import React, { useEffect, useState, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
    apiCancelExecution,
    apiGetAlerts,
    apiGetExecution,
    apiGetExecutionMetrics,
    apiGetExecutionSteps,
    apiListExecutions,
    apiListWorkflows,
    AlertItem,
    ExecutionMetrics,
    ExecutionRecord,
    StepExecutionRecord,
    WorkflowDetail,
} from '../api/client';
import { useAuth } from '../context/AuthContext';

export const ExecutionsPage: React.FC = () => {
    const { activeWorkspace } = useAuth();
    const [searchParams, setSearchParams] = useSearchParams();

    // Filters
    const selectedWorkflowId = searchParams.get('workflow_id') || '';
    const selectedStatus = searchParams.get('status') || '';
    const selectedExecutionId = searchParams.get('execution_id') || '';

    // Data states
    const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
    const [workflows, setWorkflows] = useState<WorkflowDetail[]>([]);
    const [metrics, setMetrics] = useState<ExecutionMetrics | null>(null);
    const [alerts, setAlerts] = useState<AlertItem[]>([]);
    const [selectedExecution, setSelectedExecution] = useState<ExecutionRecord | null>(null);
    const [stepRecords, setStepRecords] = useState<StepExecutionRecord[]>([]);

    // UI states
    const [loading, setLoading] = useState(true);
    const [stepsLoading, setStepsLoading] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({});
    const [liveConnected, setLiveConnected] = useState(false);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);

    const eventSourceRef = useRef<EventSource | null>(null);

    // Load initial list and metrics
    const loadOverview = useCallback(async () => {
        if (!activeWorkspace) return;
        try {
            setLoading(true);
            const [execList, wfList, metricData, alertData] = await Promise.all([
                apiListExecutions({
                    workflow_id: selectedWorkflowId || undefined,
                    status: selectedStatus || undefined,
                    limit: 50,
                }),
                apiListWorkflows().catch(() => []),
                apiGetExecutionMetrics(selectedWorkflowId || undefined, 24).catch(() => null),
                apiGetAlerts().catch(() => []),
            ]);
            setExecutions(execList);
            setWorkflows(wfList);
            setMetrics(metricData);
            setAlerts(alertData);
        } catch (err: any) {
            setErrorMsg(err.message || 'Failed to load execution history');
        } finally {
            setLoading(false);
        }
    }, [activeWorkspace, selectedWorkflowId, selectedStatus]);

    useEffect(() => {
        loadOverview();
    }, [loadOverview]);

    // Load execution details and step logs when selected
    const loadExecutionDetails = useCallback(async (execId: string) => {
        if (!execId) return;
        try {
            setStepsLoading(true);
            const [detail, steps] = await Promise.all([
                apiGetExecution(execId),
                apiGetExecutionSteps(execId),
            ]);
            setSelectedExecution(detail);
            setStepRecords(steps);
        } catch (err: any) {
            console.error('Failed to load execution steps', err);
        } finally {
            setStepsLoading(false);
        }
    }, []);

    useEffect(() => {
        if (selectedExecutionId) {
            loadExecutionDetails(selectedExecutionId);
        } else {
            setSelectedExecution(null);
            setStepRecords([]);
        }
    }, [selectedExecutionId, loadExecutionDetails]);

    // Setup Live SSE stream with polling fallback
    useEffect(() => {
        if (!activeWorkspace) return;

        const token = localStorage.getItem('nexus_access_token');
        const wsId = activeWorkspace.id;
        const apiBase = import.meta.env.VITE_API_URL || 'http://localhost:8000';
        const sseUrl = `${apiBase}/api/v1/observability/live?workspace_id=${wsId}${token ? `&token=${token}` : ''}${selectedExecutionId ? `&execution_id=${selectedExecutionId}` : ''
            }`;

        let es: EventSource | null = null;
        let pollInterval: any = null;

        try {
            es = new EventSource(sseUrl);
            eventSourceRef.current = es;

            es.onopen = () => {
                setLiveConnected(true);
            };

            es.addEventListener('execution:created', (e: MessageEvent) => {
                try {
                    const data = JSON.parse(e.data);
                    setExecutions((prev) => [data.data, ...prev.filter((x) => x.id !== data.data.execution_id)]);
                } catch { }
            });

            es.addEventListener('execution:updated', (e: MessageEvent) => {
                try {
                    const parsed = JSON.parse(e.data);
                    const update = parsed.data;
                    setExecutions((prev) =>
                        prev.map((item) =>
                            item.id === update.execution_id
                                ? { ...item, status: update.status, error: update.error, completed_at: update.completed_at, duration_ms: update.duration_ms }
                                : item
                        )
                    );
                    if (selectedExecutionId && selectedExecutionId === update.execution_id) {
                        setSelectedExecution((prev) => prev ? { ...prev, ...update } : null);
                        // Refresh steps upon completion
                        loadExecutionDetails(selectedExecutionId);
                    }
                } catch { }
            });

            es.addEventListener('step:started', (e: MessageEvent) => {
                try {
                    const parsed = JSON.parse(e.data);
                    if (selectedExecutionId && selectedExecutionId === parsed.execution_id) {
                        loadExecutionDetails(selectedExecutionId);
                    }
                } catch { }
            });

            es.addEventListener('step:completed', () => {
                if (selectedExecutionId) loadExecutionDetails(selectedExecutionId);
            });

            es.addEventListener('step:failed', () => {
                if (selectedExecutionId) loadExecutionDetails(selectedExecutionId);
            });

            es.onerror = () => {
                setLiveConnected(false);
                // Fallback polling every 5 seconds if SSE connection drops
                if (!pollInterval) {
                    pollInterval = setInterval(() => {
                        if (selectedExecutionId) {
                            loadExecutionDetails(selectedExecutionId);
                        }
                    }, 5000);
                }
            };
        } catch {
            setLiveConnected(false);
        }

        return () => {
            if (es) {
                es.close();
            }
            if (pollInterval) {
                clearInterval(pollInterval);
            }
            setLiveConnected(false);
        };
    }, [activeWorkspace, selectedExecutionId, loadExecutionDetails]);

    // Handle cancel execution
    const handleCancel = async (execId: string) => {
        if (!confirm('Are you sure you want to cancel this execution?')) return;
        try {
            setCancelling(true);
            await apiCancelExecution(execId);
            await loadExecutionDetails(execId);
            await loadOverview();
        } catch (err: any) {
            alert(err.response?.data?.detail || err.message || 'Failed to cancel');
        } finally {
            setCancelling(false);
        }
    };

    const toggleStep = (stepId: string) => {
        setExpandedSteps((prev) => ({ ...prev, [stepId]: !prev[stepId] }));
    };

    const getStatusBadge = (status: string) => {
        const colors: Record<string, { bg: string; text: string }> = {
            completed: { bg: '#dcfce7', text: '#15803d' },
            running: { bg: '#dbeafe', text: '#1d4ed8' },
            pending: { bg: '#fef3c7', text: '#b45309' },
            failed: { bg: '#fee2e2', text: '#b91c1c' },
            cancelled: { bg: '#f1f5f9', text: '#475569' },
            skipped: { bg: '#f3e8ff', text: '#7e22ce' },
        };
        const conf = colors[status.toLowerCase()] || { bg: '#f1f5f9', text: '#475569' };
        return (
            <span
                style={{
                    fontSize: '0.75rem',
                    padding: '0.2rem 0.5rem',
                    borderRadius: '4px',
                    backgroundColor: conf.bg,
                    color: conf.text,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                }}
            >
                {status}
            </span>
        );
    };

    return (
        <div>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
                <div>
                    <h1 style={{ fontSize: '1.75rem', margin: '0 0 0.5rem 0' }}>Execution History & Observability</h1>
                    <p style={{ color: '#64748b', margin: 0 }}>
                        Inspect workflow execution traces, step-by-step logs with secret redaction, and live timeline updates.
                    </p>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem' }}>
                        <span
                            style={{
                                width: '10px',
                                height: '10px',
                                borderRadius: '50%',
                                backgroundColor: liveConnected ? '#10b981' : '#f59e0b',
                                display: 'inline-block',
                            }}
                        />
                        <span style={{ color: '#475569' }}>{liveConnected ? 'Live Updates (SSE)' : 'Polling Fallback'}</span>
                    </div>
                    <button
                        onClick={loadOverview}
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

            {/* Operational Alerts Banner */}
            {alerts.length > 0 && (
                <div style={{ marginBottom: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
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

            {/* Metrics Overview Cards */}
            {metrics && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginBottom: '1.5rem' }}>
                    <div style={{ backgroundColor: '#fff', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                        <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Total Executions (24h)</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#0f172a', marginTop: '0.25rem' }}>{metrics.total_executions}</div>
                    </div>
                    <div style={{ backgroundColor: '#fff', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                        <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Success Rate</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#16a34a', marginTop: '0.25rem' }}>{metrics.success_rate_percent}%</div>
                    </div>
                    <div style={{ backgroundColor: '#fff', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                        <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Failure Rate</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 700, color: metrics.failure_rate_percent > 0 ? '#dc2626' : '#64748b', marginTop: '0.25rem' }}>
                            {metrics.failure_rate_percent}%
                        </div>
                    </div>
                    <div style={{ backgroundColor: '#fff', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                        <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Avg Duration</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#0f172a', marginTop: '0.25rem' }}>
                            {metrics.avg_duration_ms > 0 ? `${(metrics.avg_duration_ms / 1000).toFixed(2)}s` : '0ms'}
                        </div>
                    </div>
                    <div style={{ backgroundColor: '#fff', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                        <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Queue Latency</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#0f172a', marginTop: '0.25rem' }}>
                            {metrics.avg_queue_latency_ms.toFixed(0)}ms
                        </div>
                    </div>
                </div>
            )}

            {/* Error message */}
            {errorMsg && (
                <div style={{ padding: '0.75rem 1rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '6px', marginBottom: '1rem' }}>
                    {errorMsg}
                </div>
            )}

            {/* Filters */}
            <div style={{ backgroundColor: '#fff', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0', marginBottom: '1.5rem', display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                    <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#475569' }}>Filter Workflow:</label>
                    <select
                        value={selectedWorkflowId}
                        onChange={(e) => {
                            const newParams = new URLSearchParams(searchParams);
                            if (e.target.value) newParams.set('workflow_id', e.target.value);
                            else newParams.delete('workflow_id');
                            setSearchParams(newParams);
                        }}
                        style={{ padding: '0.4rem 0.6rem', borderRadius: '4px', border: '1px solid #cbd5e1', fontSize: '0.85rem' }}
                    >
                        <option value="">All Workflows</option>
                        {workflows.map((wf) => (
                            <option key={wf.id} value={wf.id}>{wf.name} (v{wf.active_version || wf.current_version})</option>
                        ))}
                    </select>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                    <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#475569' }}>Filter Status:</label>
                    <select
                        value={selectedStatus}
                        onChange={(e) => {
                            const newParams = new URLSearchParams(searchParams);
                            if (e.target.value) newParams.set('status', e.target.value);
                            else newParams.delete('status');
                            setSearchParams(newParams);
                        }}
                        style={{ padding: '0.4rem 0.6rem', borderRadius: '4px', border: '1px solid #cbd5e1', fontSize: '0.85rem' }}
                    >
                        <option value="">All Statuses</option>
                        <option value="pending">Pending</option>
                        <option value="running">Running</option>
                        <option value="completed">Completed</option>
                        <option value="failed">Failed</option>
                        <option value="cancelled">Cancelled</option>
                    </select>
                </div>
            </div>

            {/* Main Content: Execution List + Live Timeline Pane */}
            <div style={{ display: 'grid', gridTemplateColumns: selectedExecutionId ? '1fr 1.2fr' : '1fr', gap: '1.5rem', alignItems: 'start' }}>
                {/* Executions Table */}
                <div style={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e2e8f0', overflow: 'hidden' }}>
                    <div style={{ padding: '1rem', borderBottom: '1px solid #e2e8f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <h3 style={{ margin: 0, fontSize: '1rem' }}>Executions ({executions.length})</h3>
                    </div>

                    {loading ? (
                        <div style={{ padding: '2rem', textAlign: 'center', color: '#64748b' }}>Loading execution history...</div>
                    ) : executions.length === 0 ? (
                        <div style={{ padding: '2rem', textAlign: 'center', color: '#64748b' }}>
                            No executions recorded yet. Trigger a workflow manually or send a webhook event to see history.
                        </div>
                    ) : (
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.85rem' }}>
                                <thead>
                                    <tr style={{ backgroundColor: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Execution / ID</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Status</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Version</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Trigger</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Started At</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Duration</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {executions.map((ex) => {
                                        const isSelected = selectedExecutionId === ex.id;
                                        return (
                                            <tr
                                                key={ex.id}
                                                onClick={() => {
                                                    const newParams = new URLSearchParams(searchParams);
                                                    newParams.set('execution_id', ex.id);
                                                    setSearchParams(newParams);
                                                }}
                                                style={{
                                                    borderBottom: '1px solid #f1f5f9',
                                                    cursor: 'pointer',
                                                    backgroundColor: isSelected ? '#eff6ff' : 'transparent',
                                                }}
                                            >
                                                <td style={{ padding: '0.75rem 1rem' }}>
                                                    <div style={{ fontWeight: 600, color: isSelected ? '#2563eb' : '#0f172a' }}>
                                                        {ex.id.slice(-8)}
                                                    </div>
                                                    <div style={{ fontSize: '0.75rem', color: '#64748b' }}>
                                                        {ex.workflow_id}
                                                    </div>
                                                </td>
                                                <td style={{ padding: '0.75rem 1rem' }}>{getStatusBadge(ex.status)}</td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>v{ex.workflow_version}</td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>{ex.triggered_by}</td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>
                                                    {new Date(ex.started_at).toLocaleTimeString()}
                                                </td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>
                                                    {ex.duration_ms ? `${(ex.duration_ms / 1000).toFixed(2)}s` : '-'}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>

                {/* Execution Detail and Live Timeline */}
                {selectedExecution && (
                    <div style={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e2e8f0', padding: '1.25rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem', borderBottom: '1px solid #e2e8f0', paddingBottom: '0.75rem' }}>
                            <div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                                    <h3 style={{ margin: 0, fontSize: '1.1rem' }}>Trace: {selectedExecution.id}</h3>
                                    {getStatusBadge(selectedExecution.status)}
                                </div>
                                <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: '0.25rem' }}>
                                    Workflow: <strong>{selectedExecution.workflow_id}</strong> (v{selectedExecution.workflow_version}) &bull; Triggered by: {selectedExecution.triggered_by}
                                </div>
                            </div>
                            <div style={{ display: 'flex', gap: '0.5rem' }}>
                                {(selectedExecution.status === 'running' || selectedExecution.status === 'pending') && (
                                    <button
                                        onClick={() => handleCancel(selectedExecution.id)}
                                        disabled={cancelling}
                                        style={{
                                            padding: '0.35rem 0.75rem',
                                            backgroundColor: '#fee2e2',
                                            color: '#991b1b',
                                            border: '1px solid #f87171',
                                            borderRadius: '4px',
                                            fontSize: '0.8rem',
                                            cursor: 'pointer',
                                            fontWeight: 600,
                                        }}
                                    >
                                        {cancelling ? 'Cancelling...' : 'Cancel Execution'}
                                    </button>
                                )}
                                <button
                                    onClick={() => {
                                        const newParams = new URLSearchParams(searchParams);
                                        newParams.delete('execution_id');
                                        setSearchParams(newParams);
                                    }}
                                    style={{
                                        padding: '0.35rem 0.6rem',
                                        backgroundColor: '#f1f5f9',
                                        border: '1px solid #cbd5e1',
                                        borderRadius: '4px',
                                        fontSize: '0.8rem',
                                        cursor: 'pointer',
                                    }}
                                >
                                    ✕ Close
                                </button>
                            </div>
                        </div>

                        {/* Error Callout if execution failed */}
                        {selectedExecution.error && (
                            <div style={{ padding: '0.75rem 1rem', backgroundColor: '#fee2e2', border: '1px solid #f87171', borderRadius: '6px', color: '#991b1b', marginBottom: '1rem', fontSize: '0.85rem' }}>
                                <strong>Execution Error:</strong> {selectedExecution.error}
                            </div>
                        )}

                        {/* Trigger Info */}
                        <div style={{ marginBottom: '1rem', padding: '0.75rem', backgroundColor: '#f8fafc', borderRadius: '6px', border: '1px solid #e2e8f0' }}>
                            <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#475569', textTransform: 'uppercase', marginBottom: '0.25rem' }}>
                                Originating Trigger Event (Redacted)
                            </div>
                            <pre style={{ margin: 0, fontSize: '0.75rem', color: '#334155', maxHeight: '100px', overflowY: 'auto' }}>
                                {JSON.stringify(selectedExecution.trigger_event, null, 2)}
                            </pre>
                        </div>

                        {/* Step Execution Timeline */}
                        <div>
                            <h4 style={{ margin: '0 0 0.75rem 0', fontSize: '0.95rem' }}>Execution Timeline & Step Logs</h4>
                            {stepsLoading && stepRecords.length === 0 ? (
                                <p style={{ fontSize: '0.85rem', color: '#64748b' }}>Loading step logs...</p>
                            ) : stepRecords.length === 0 ? (
                                <p style={{ fontSize: '0.85rem', color: '#64748b' }}>No step activities recorded yet for this execution.</p>
                            ) : (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                    {stepRecords.map((step, idx) => {
                                        const isExpanded = !!expandedSteps[step.id || step.step_id];
                                        return (
                                            <div
                                                key={step.id || `${step.step_id}-${step.attempt}`}
                                                style={{
                                                    border: '1px solid #e2e8f0',
                                                    borderRadius: '6px',
                                                    overflow: 'hidden',
                                                    backgroundColor: step.status === 'failed' ? '#fff1f2' : '#fff',
                                                }}
                                            >
                                                <div
                                                    onClick={() => toggleStep(step.id || step.step_id)}
                                                    style={{
                                                        padding: '0.6rem 0.85rem',
                                                        display: 'flex',
                                                        justifyContent: 'space-between',
                                                        alignItems: 'center',
                                                        cursor: 'pointer',
                                                        backgroundColor: step.status === 'failed' ? '#ffe4e6' : '#f8fafc',
                                                    }}
                                                >
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                                        <span style={{ fontWeight: 700, fontSize: '0.8rem', color: '#64748b' }}>#{idx + 1}</span>
                                                        <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{step.step_name}</span>
                                                        <span style={{ fontSize: '0.75rem', color: '#64748b' }}>({step.step_id})</span>
                                                        {step.attempt > 1 && (
                                                            <span style={{ fontSize: '0.7rem', padding: '0.1rem 0.35rem', backgroundColor: '#fed7aa', color: '#9a3412', borderRadius: '4px', fontWeight: 600 }}>
                                                                Attempt {step.attempt}
                                                            </span>
                                                        )}
                                                    </div>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                                        {step.execution_time_ms !== undefined && step.execution_time_ms !== null && (
                                                            <span style={{ fontSize: '0.75rem', color: '#64748b' }}>{step.execution_time_ms}ms</span>
                                                        )}
                                                        {getStatusBadge(step.status)}
                                                        <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>{isExpanded ? '▲' : '▼'}</span>
                                                    </div>
                                                </div>

                                                {isExpanded && (
                                                    <div style={{ padding: '0.85rem', fontSize: '0.8rem', display: 'flex', flexDirection: 'column', gap: '0.5rem', borderTop: '1px solid #e2e8f0' }}>
                                                        {step.error && (
                                                            <div style={{ padding: '0.5rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '4px' }}>
                                                                <strong>Normalized Error:</strong> {step.error}
                                                            </div>
                                                        )}
                                                        {step.skip_reason && (
                                                            <div style={{ padding: '0.5rem', backgroundColor: '#f3e8ff', color: '#6b21a8', borderRadius: '4px' }}>
                                                                <strong>Skip Reason:</strong> {step.skip_reason}
                                                            </div>
                                                        )}
                                                        <div>
                                                            <div style={{ fontWeight: 600, color: '#475569', marginBottom: '0.2rem' }}>Input Summary (Redacted):</div>
                                                            <pre style={{ backgroundColor: '#f1f5f9', padding: '0.5rem', borderRadius: '4px', margin: 0, maxHeight: '120px', overflowY: 'auto' }}>
                                                                {JSON.stringify(step.input_data || {}, null, 2)}
                                                            </pre>
                                                        </div>
                                                        <div>
                                                            <div style={{ fontWeight: 600, color: '#475569', marginBottom: '0.2rem' }}>Output Summary:</div>
                                                            <pre style={{ backgroundColor: '#f1f5f9', padding: '0.5rem', borderRadius: '4px', margin: 0, maxHeight: '120px', overflowY: 'auto' }}>
                                                                {JSON.stringify(step.output_data || {}, null, 2)}
                                                            </pre>
                                                        </div>
                                                        <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>
                                                            Idempotency Key: {step.idempotency_key}
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
