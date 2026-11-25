import React from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export const Dashboard: React.FC = () => {
    const { user, activeWorkspace, isAuthenticated } = useAuth();

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
                <div>
                    <h1 style={{ fontSize: '1.75rem', margin: '0 0 0.5rem 0' }}>
                        {isAuthenticated ? `Welcome back, ${user?.full_name}` : 'Welcome to NexusMCP'}
                    </h1>
                    <p style={{ color: '#475569', margin: 0 }}>
                        AI-powered, self-hosted workflow automation platform with strict tenant isolation and durable execution.
                    </p>
                </div>

                {isAuthenticated && activeWorkspace && (
                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '0.75rem 1.25rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
                        <div>
                            <div style={{ fontSize: '0.75rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 600 }}>Active Workspace</div>
                            <div style={{ fontWeight: 600, color: '#0f172a' }}>{activeWorkspace.name}</div>
                        </div>
                        <span
                            style={{
                                fontSize: '0.75rem',
                                padding: '0.25rem 0.5rem',
                                borderRadius: '4px',
                                backgroundColor: activeWorkspace.role === 'owner' ? '#fef3c7' : '#e0e7ff',
                                color: activeWorkspace.role === 'owner' ? '#92400e' : '#3730a3',
                                fontWeight: 600,
                                textTransform: 'uppercase',
                            }}
                        >
                            {activeWorkspace.role}
                        </span>
                    </div>
                )}
            </div>

            {!isAuthenticated ? (
                <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '2rem', textAlign: 'center', maxWidth: '600px', margin: '3rem auto' }}>
                    <h2 style={{ marginTop: 0 }}>Get Started with NexusMCP</h2>
                    <p style={{ color: '#64748b', marginBottom: '1.5rem' }}>
                        Sign in to access your organization workspaces or create a new account with a dedicated tenant sandbox.
                    </p>
                    <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center' }}>
                        <Link
                            to="/login"
                            style={{
                                padding: '0.75rem 1.5rem',
                                backgroundColor: '#2563eb',
                                color: '#fff',
                                textDecoration: 'none',
                                borderRadius: '6px',
                                fontWeight: 600,
                            }}
                        >
                            Sign In
                        </Link>
                        <Link
                            to="/register"
                            style={{
                                padding: '0.75rem 1.5rem',
                                backgroundColor: '#f1f5f9',
                                color: '#0f172a',
                                textDecoration: 'none',
                                borderRadius: '6px',
                                fontWeight: 600,
                                border: '1px solid #cbd5e1',
                            }}
                        >
                            Create Account
                        </Link>
                    </div>
                </div>
            ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1.5rem', marginTop: '1.5rem' }}>
                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                        <h3 style={{ marginTop: 0, fontSize: '1.1rem' }}>Workspaces & Team</h3>
                        <p style={{ color: '#64748b', fontSize: '0.875rem' }}>
                            Switch workspaces, manage team members, and configure role-based permissions (Owner, Admin, Member).
                        </p>
                        <Link
                            to="/workspaces"
                            style={{ display: 'inline-block', marginTop: '0.5rem', color: '#2563eb', fontWeight: 600, textDecoration: 'none', fontSize: '0.875rem' }}
                        >
                            Manage Workspaces &rarr;
                        </Link>
                    </div>

                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                        <h3 style={{ marginTop: 0, fontSize: '1.1rem' }}>Tenant Isolation</h3>
                        <p style={{ color: '#64748b', fontSize: '0.875rem' }}>
                            All workflows, executions, and database entities are strictly scoped to your active workspace ID.
                        </p>
                        <div style={{ marginTop: '0.5rem', fontSize: '0.8rem', color: '#10b981', fontWeight: 600 }}>
                            ✓ Strict backend & repository isolation active
                        </div>
                    </div>

                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                        <h3 style={{ marginTop: 0, fontSize: '1.1rem' }}>Audit Trail</h3>
                        <p style={{ color: '#64748b', fontSize: '0.875rem' }}>
                            Review immutable security events, member invitations, logins, and workflow modifications.
                        </p>
                        <Link
                            to="/audit"
                            style={{ display: 'inline-block', marginTop: '0.5rem', color: '#2563eb', fontWeight: 600, textDecoration: 'none', fontSize: '0.875rem' }}
                        >
                            View Audit Logs &rarr;
                        </Link>
                    </div>

                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                        <h3 style={{ marginTop: 0, fontSize: '1.1rem' }}>System Status</h3>
                        <p style={{ color: '#64748b', fontSize: '0.875rem' }}>
                            Real-time health probes and dependency readiness for MongoDB, Temporal, and backend services.
                        </p>
                        <Link
                            to="/status"
                            style={{ display: 'inline-block', marginTop: '0.5rem', color: '#2563eb', fontWeight: 600, textDecoration: 'none', fontSize: '0.875rem' }}
                        >
                            Check Health &rarr;
                        </Link>
                    </div>
                </div>
            )}
        </div>
    );
};
