import React from 'react';
import { Link, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export const Layout: React.FC = () => {
    const { user, activeWorkspace, workspaces, switchWorkspace, logout, isAuthenticated } = useAuth();
    const navigate = useNavigate();

    const handleLogout = async () => {
        await logout();
        navigate('/login');
    };

    const isAuditAuthorized = activeWorkspace?.role === 'owner' || activeWorkspace?.role === 'admin';

    return (
        <div style={{ display: 'flex', minHeight: '100vh', flexDirection: 'column' }}>
            <header style={{ backgroundColor: '#1e293b', color: '#fff', padding: '0.75rem 2rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>
                    <Link to="/" style={{ color: '#fff', textDecoration: 'none' }}>
                        <h2 style={{ margin: 0, fontSize: '1.25rem', letterSpacing: '-0.025em' }}>NexusMCP</h2>
                    </Link>

                    {isAuthenticated && workspaces.length > 0 && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                            <span style={{ fontSize: '0.75rem', color: '#94a3b8', textTransform: 'uppercase' }}>WS:</span>
                            <select
                                value={activeWorkspace?.id || ''}
                                onChange={(e) => {
                                    const selected = workspaces.find((w) => w.id === e.target.value);
                                    if (selected) switchWorkspace(selected);
                                }}
                                style={{
                                    backgroundColor: '#0f172a',
                                    color: '#f8fafc',
                                    border: '1px solid #334155',
                                    padding: '0.35rem 0.6rem',
                                    borderRadius: '4px',
                                    fontSize: '0.85rem',
                                    cursor: 'pointer',
                                }}
                            >
                                {workspaces.map((ws) => (
                                    <option key={ws.id} value={ws.id}>
                                        {ws.name} ({ws.role})
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}
                </div>

                <nav style={{ display: 'flex', alignItems: 'center', gap: '1.25rem' }}>
                    <Link to="/" style={{ color: '#cbd5e1', textDecoration: 'none', fontSize: '0.9rem' }}>Dashboard</Link>
                    {isAuthenticated && (
                        <>
                            <Link to="/workflows" style={{ color: '#cbd5e1', textDecoration: 'none', fontSize: '0.9rem' }}>Workflows</Link>
                            <Link to="/designer" style={{ color: '#cbd5e1', textDecoration: 'none', fontSize: '0.9rem' }}>AI Designer</Link>
                            <Link to="/workspaces" style={{ color: '#cbd5e1', textDecoration: 'none', fontSize: '0.9rem' }}>Workspaces</Link>
                            {isAuditAuthorized && (
                                <Link to="/audit" style={{ color: '#cbd5e1', textDecoration: 'none', fontSize: '0.9rem' }}>Audit Trail</Link>
                            )}
                            <Link to="/profile" style={{ color: '#cbd5e1', textDecoration: 'none', fontSize: '0.9rem' }}>Profile</Link>
                            <Link to="/integrations" style={{ color: '#cbd5e1', textDecoration: 'none', fontSize: '0.9rem' }}>Integrations</Link>
                        </>
                    )}
                    <Link to="/status" style={{ color: '#cbd5e1', textDecoration: 'none', fontSize: '0.9rem' }}>Status</Link>

                    {isAuthenticated ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginLeft: '0.5rem' }}>
                            <span style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
                                {user?.full_name}
                            </span>
                            <button
                                onClick={handleLogout}
                                style={{
                                    backgroundColor: '#334155',
                                    color: '#f8fafc',
                                    border: 'none',
                                    padding: '0.35rem 0.75rem',
                                    borderRadius: '4px',
                                    fontSize: '0.8rem',
                                    cursor: 'pointer',
                                }}
                            >
                                Logout
                            </button>
                        </div>
                    ) : (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                            <Link
                                to="/login"
                                style={{ color: '#f8fafc', textDecoration: 'none', fontSize: '0.85rem', fontWeight: 600 }}
                            >
                                Sign In
                            </Link>
                            <Link
                                to="/register"
                                style={{
                                    backgroundColor: '#2563eb',
                                    color: '#fff',
                                    textDecoration: 'none',
                                    padding: '0.35rem 0.75rem',
                                    borderRadius: '4px',
                                    fontSize: '0.85rem',
                                    fontWeight: 600,
                                }}
                            >
                                Register
                            </Link>
                        </div>
                    )}
                </nav>
            </header>
            <main style={{ flex: 1, padding: '2rem', maxWidth: '1200px', margin: '0 auto', width: '100%', boxSizing: 'border-box' }}>
                <Outlet />
            </main>
            <footer style={{ backgroundColor: '#f1f5f9', padding: '1rem', textAlign: 'center', fontSize: '0.875rem', color: '#64748b' }}>
                NexusMCP — AI-Powered, Self-Hosted Automation Platform
            </footer>
        </div>
    );
};
