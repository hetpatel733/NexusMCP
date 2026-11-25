import React, { useEffect, useState } from 'react';
import { fetchHealth, fetchReadiness, HealthResponse, ReadinessResponse } from '../api/client';

export const StatusPage: React.FC = () => {
    const [health, setHealth] = useState<HealthResponse | null>(null);
    const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const loadStatus = async () => {
            try {
                setLoading(true);
                const [hData, rData] = await Promise.all([fetchHealth(), fetchReadiness()]);
                setHealth(hData);
                setReadiness(rData);
            } catch (err: any) {
                setError(err.message || 'Failed to fetch status');
            } finally {
                setLoading(false);
            }
        };
        loadStatus();
    }, []);

    return (
        <div>
            <h1 style={{ fontSize: '1.75rem', marginBottom: '0.5rem' }}>System Status</h1>
            <p style={{ color: '#475569' }}>Real-time health and readiness checks for NexusMCP core services.</p>

            {loading && <p>Checking service states...</p>}
            {error && (
                <div style={{ padding: '1rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '6px', marginBottom: '1rem' }}>
                    <strong>Error connecting to API:</strong> {error}
                </div>
            )}

            {!loading && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', marginTop: '1.5rem' }}>
                    <div style={{ padding: '1.5rem', backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px' }}>
                        <h3 style={{ marginTop: 0 }}>API Liveness</h3>
                        <p><strong>Status:</strong> <span style={{ color: health?.status === 'ok' ? '#16a34a' : '#dc2626' }}>{health?.status || 'Unknown'}</span></p>
                        <p><strong>Environment:</strong> {health?.environment || 'Unknown'}</p>
                        <p><strong>Version:</strong> {health?.version || 'Unknown'}</p>
                    </div>

                    <div style={{ padding: '1.5rem', backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px' }}>
                        <h3 style={{ marginTop: 0 }}>Dependency Readiness</h3>
                        <p><strong>Overall:</strong> <span style={{ color: readiness?.status === 'ready' ? '#16a34a' : '#dc2626' }}>{readiness?.status || 'Unknown'}</span></p>
                        <div>
                            <strong>Dependencies:</strong>
                            <pre style={{ backgroundColor: '#f8fafc', padding: '0.5rem', borderRadius: '4px', overflowX: 'auto' }}>
                                {JSON.stringify(readiness?.dependencies || {}, null, 2)}
                            </pre>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};
