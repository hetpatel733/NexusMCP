import React, { useEffect, useState, useCallback } from 'react';
import {
    apiDiscardDLQEvent,
    apiGetDLQEvent,
    apiListDLQEvents,
    apiReplayDLQEvent,
    DLQEvent,
} from '../api/client';
import { useAuth } from '../context/AuthContext';

export const DeadLetterQueuePage: React.FC = () => {
    const { activeWorkspace } = useAuth();

    const [events, setEvents] = useState<DLQEvent[]>([]);
    const [selectedEvent, setSelectedEvent] = useState<DLQEvent | null>(null);
    const [statusFilter, setStatusFilter] = useState<string>('');
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(false);
    const [useLatestVersion, setUseLatestVersion] = useState(false);
    const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

    const loadDLQ = useCallback(async () => {
        if (!activeWorkspace) return;
        try {
            setLoading(true);
            const data = await apiListDLQEvents({
                status: statusFilter || undefined,
                limit: 50,
            });
            setEvents(data);
        } catch (err: any) {
            setFeedback({ type: 'error', message: err.message || 'Failed to load DLQ events' });
        } finally {
            setLoading(false);
        }
    }, [activeWorkspace, statusFilter]);

    useEffect(() => {
        loadDLQ();
    }, [loadDLQ]);

    const handleSelectEvent = async (dlqId: string) => {
        try {
            const ev = await apiGetDLQEvent(dlqId);
            setSelectedEvent(ev);
        } catch (err: any) {
            alert(err.message || 'Failed to fetch event detail');
        }
    };

    const handleReplay = async (dlqId: string) => {
        try {
            setActionLoading(true);
            setFeedback(null);
            const res = await apiReplayDLQEvent(dlqId, {
                use_latest_version: useLatestVersion,
            });
            setFeedback({
                type: 'success',
                message: `Replay dispatched successfully! Execution ID: ${res.execution_id} (Version ${res.workflow_version || 'pinned'})`,
            });
            await loadDLQ();
            if (selectedEvent && selectedEvent.id === dlqId) {
                await handleSelectEvent(dlqId);
            }
        } catch (err: any) {
            setFeedback({
                type: 'error',
                message: err.response?.data?.detail || err.message || 'Failed to replay event',
            });
        } finally {
            setActionLoading(false);
        }
    };

    const handleDiscard = async (dlqId: string) => {
        if (!confirm('Are you sure you want to discard this event from the Dead Letter Queue?')) return;
        try {
            setActionLoading(true);
            setFeedback(null);
            await apiDiscardDLQEvent(dlqId);
            setFeedback({ type: 'success', message: 'Event successfully discarded.' });
            await loadDLQ();
            if (selectedEvent && selectedEvent.id === dlqId) {
                setSelectedEvent(null);
            }
        } catch (err: any) {
            setFeedback({
                type: 'error',
                message: err.response?.data?.detail || err.message || 'Failed to discard event',
            });
        } finally {
            setActionLoading(false);
        }
    };

    const getStatusBadge = (status: string) => {
        const colors: Record<string, { bg: string; text: string }> = {
            failed: { bg: '#fee2e2', text: '#b91c1c' },
            replaying: { bg: '#fef3c7', text: '#b45309' },
            resolved: { bg: '#dcfce7', text: '#15803d' },
            discarded: { bg: '#f1f5f9', text: '#475569' },
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
                    <h1 style={{ fontSize: '1.75rem', margin: '0 0 0.5rem 0' }}>Dead Letter Queue (DLQ)</h1>
                    <p style={{ color: '#64748b', margin: 0 }}>
                        Safely inspect failed trigger deliveries and execution events, with secret redaction and idempotent replay.
                    </p>
                </div>
                <button
                    onClick={loadDLQ}
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

            {/* Notification / Feedback */}
            {feedback && (
                <div
                    style={{
                        padding: '0.75rem 1rem',
                        borderRadius: '6px',
                        marginBottom: '1rem',
                        backgroundColor: feedback.type === 'success' ? '#dcfce7' : '#fee2e2',
                        color: feedback.type === 'success' ? '#166534' : '#991b1b',
                        border: `1px solid ${feedback.type === 'success' ? '#86efac' : '#fca5a5'}`,
                    }}
                >
                    {feedback.message}
                </div>
            )}

            {/* Filter */}
            <div style={{ backgroundColor: '#fff', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0', marginBottom: '1.5rem', display: 'flex', gap: '1rem', alignItems: 'center' }}>
                <label style={{ fontSize: '0.85rem', fontWeight: 600, color: '#475569' }}>Filter by Status:</label>
                <select
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)}
                    style={{ padding: '0.4rem 0.6rem', borderRadius: '4px', border: '1px solid #cbd5e1', fontSize: '0.85rem' }}
                >
                    <option value="">All Statuses</option>
                    <option value="failed">Failed</option>
                    <option value="replaying">Replaying</option>
                    <option value="resolved">Resolved</option>
                    <option value="discarded">Discarded</option>
                </select>
            </div>

            {/* Main Content Layout */}
            <div style={{ display: 'grid', gridTemplateColumns: selectedEvent ? '1fr 1.2fr' : '1fr', gap: '1.5rem', alignItems: 'start' }}>
                {/* DLQ Events List */}
                <div style={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e2e8f0', overflow: 'hidden' }}>
                    <div style={{ padding: '1rem', borderBottom: '1px solid #e2e8f0' }}>
                        <h3 style={{ margin: 0, fontSize: '1rem' }}>Failed Events ({events.length})</h3>
                    </div>

                    {loading ? (
                        <div style={{ padding: '2rem', textAlign: 'center', color: '#64748b' }}>Loading Dead Letter Queue...</div>
                    ) : events.length === 0 ? (
                        <div style={{ padding: '2rem', textAlign: 'center', color: '#64748b' }}>
                            No failed events found in DLQ. All trigger events are processing normally.
                        </div>
                    ) : (
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.85rem' }}>
                                <thead>
                                    <tr style={{ backgroundColor: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Event ID</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Workflow</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Provider</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Stage</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Status</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Replays</th>
                                        <th style={{ padding: '0.75rem 1rem', color: '#475569' }}>Received</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {events.map((ev) => {
                                        const isSelected = selectedEvent?.id === ev.id;
                                        return (
                                            <tr
                                                key={ev.id}
                                                onClick={() => handleSelectEvent(ev.id)}
                                                style={{
                                                    borderBottom: '1px solid #f1f5f9',
                                                    cursor: 'pointer',
                                                    backgroundColor: isSelected ? '#eff6ff' : 'transparent',
                                                }}
                                            >
                                                <td style={{ padding: '0.75rem 1rem', fontWeight: 600, color: isSelected ? '#2563eb' : '#0f172a' }}>
                                                    {ev.event_id}
                                                </td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>
                                                    {ev.workflow_id || 'unassigned'}
                                                </td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>{ev.provider}</td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>{ev.failure_stage}</td>
                                                <td style={{ padding: '0.75rem 1rem' }}>{getStatusBadge(ev.status)}</td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>{ev.replay_count}</td>
                                                <td style={{ padding: '0.75rem 1rem', color: '#475569' }}>
                                                    {new Date(ev.created_at).toLocaleTimeString()}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>

                {/* Inspect and Replay Panel */}
                {selectedEvent && (
                    <div style={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e2e8f0', padding: '1.25rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem', borderBottom: '1px solid #e2e8f0', paddingBottom: '0.75rem' }}>
                            <div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                                    <h3 style={{ margin: 0, fontSize: '1.1rem' }}>Inspect Failed Event</h3>
                                    {getStatusBadge(selectedEvent.status)}
                                </div>
                                <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: '0.25rem' }}>
                                    Event ID: <strong>{selectedEvent.event_id}</strong> &bull; Provider: {selectedEvent.provider} ({selectedEvent.event_type})
                                </div>
                            </div>
                            <button
                                onClick={() => setSelectedEvent(null)}
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

                        {/* Error Reason */}
                        <div style={{ padding: '0.75rem 1rem', backgroundColor: '#fee2e2', border: '1px solid #f87171', borderRadius: '6px', color: '#991b1b', marginBottom: '1rem', fontSize: '0.85rem' }}>
                            <strong>Failure Reason:</strong> {selectedEvent.error}
                        </div>

                        {/* Event Details */}
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '1rem', fontSize: '0.8rem' }}>
                            <div><strong>Workflow ID:</strong> {selectedEvent.workflow_id || 'N/A'}</div>
                            <div><strong>Workflow Version:</strong> {selectedEvent.workflow_version ? `v${selectedEvent.workflow_version}` : 'N/A'}</div>
                            <div><strong>Failure Stage:</strong> {selectedEvent.failure_stage}</div>
                            <div><strong>Replay Count:</strong> {selectedEvent.replay_count}</div>
                        </div>

                        {/* Redacted Payload View */}
                        <div style={{ marginBottom: '1rem' }}>
                            <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#475569', textTransform: 'uppercase', marginBottom: '0.25rem' }}>
                                Redacted Event Payload
                            </div>
                            <pre style={{ margin: 0, fontSize: '0.75rem', color: '#334155', backgroundColor: '#f8fafc', padding: '0.75rem', borderRadius: '6px', border: '1px solid #e2e8f0', maxHeight: '150px', overflowY: 'auto' }}>
                                {JSON.stringify(selectedEvent.payload, null, 2)}
                            </pre>
                        </div>

                        {/* Replay Controls */}
                        <div style={{ borderTop: '1px solid #e2e8f0', paddingTop: '1rem' }}>
                            <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9rem' }}>Controlled Replay</h4>
                            <p style={{ fontSize: '0.8rem', color: '#64748b', margin: '0 0 0.75rem 0' }}>
                                Replays are idempotent. A unique idempotency key prevents duplicate execution on network retry.
                            </p>

                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem', fontSize: '0.85rem' }}>
                                <input
                                    type="checkbox"
                                    id="useLatestVersion"
                                    checked={useLatestVersion}
                                    onChange={(e) => setUseLatestVersion(e.target.checked)}
                                />
                                <label htmlFor="useLatestVersion" style={{ color: '#334155', cursor: 'pointer' }}>
                                    Replay with latest active workflow version (default: original pinned version)
                                </label>
                            </div>

                            <div style={{ display: 'flex', gap: '0.75rem' }}>
                                <button
                                    onClick={() => handleReplay(selectedEvent.id)}
                                    disabled={actionLoading || selectedEvent.status === 'replaying'}
                                    style={{
                                        padding: '0.5rem 1rem',
                                        backgroundColor: '#2563eb',
                                        color: '#fff',
                                        border: 'none',
                                        borderRadius: '6px',
                                        fontSize: '0.85rem',
                                        fontWeight: 600,
                                        cursor: 'pointer',
                                    }}
                                >
                                    {actionLoading ? 'Processing...' : '▶ Replay Event'}
                                </button>
                                <button
                                    onClick={() => handleDiscard(selectedEvent.id)}
                                    disabled={actionLoading}
                                    style={{
                                        padding: '0.5rem 1rem',
                                        backgroundColor: '#f1f5f9',
                                        color: '#475569',
                                        border: '1px solid #cbd5e1',
                                        borderRadius: '6px',
                                        fontSize: '0.85rem',
                                        cursor: 'pointer',
                                    }}
                                >
                                    Discard
                                </button>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
