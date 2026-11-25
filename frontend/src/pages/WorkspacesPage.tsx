import React, { useEffect, useState } from 'react';
import {
    apiAddWorkspaceMember,
    apiCreateWorkspace,
    apiListWorkspaceMembers,
    apiRemoveWorkspaceMember,
    apiUpdateWorkspaceMemberRole,
    WorkspaceMember,
    WorkspaceRole,
} from '../api/client';
import { useAuth } from '../context/AuthContext';

export const WorkspacesPage: React.FC = () => {
    const { workspaces, activeWorkspace, switchWorkspace, refreshWorkspaces } = useAuth();
    const [members, setMembers] = useState<WorkspaceMember[]>([]);
    const [loadingMembers, setLoadingMembers] = useState<boolean>(false);

    // Create workspace form
    const [newWsName, setNewWsName] = useState('');
    const [newWsDesc, setNewWsDesc] = useState('');
    const [creatingWs, setCreatingWs] = useState(false);
    const [wsError, setWsError] = useState<string | null>(null);

    // Add member form
    const [newMemberEmail, setNewMemberEmail] = useState('');
    const [newMemberRole, setNewMemberRole] = useState<WorkspaceRole>('member');
    const [addingMember, setAddingMember] = useState(false);
    const [memberError, setMemberError] = useState<string | null>(null);

    const isOwner = activeWorkspace?.role === 'owner';
    const isAdminOrOwner = isOwner || activeWorkspace?.role === 'admin';

    const loadMembers = async (wsId: string) => {
        try {
            setLoadingMembers(true);
            const data = await apiListWorkspaceMembers(wsId);
            setMembers(data);
        } catch (err: any) {
            setMemberError(err.response?.data?.detail || 'Failed to load members');
        } finally {
            setLoadingMembers(false);
        }
    };

    useEffect(() => {
        if (activeWorkspace) {
            loadMembers(activeWorkspace.id);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeWorkspace?.id]);

    const handleCreateWorkspace = async (e: React.FormEvent) => {
        e.preventDefault();
        setWsError(null);
        setCreatingWs(true);
        try {
            const created = await apiCreateWorkspace(newWsName, newWsDesc);
            setNewWsName('');
            setNewWsDesc('');
            await refreshWorkspaces();
            switchWorkspace(created);
        } catch (err: any) {
            setWsError(err.response?.data?.detail || 'Failed to create workspace');
        } finally {
            setCreatingWs(false);
        }
    };

    const handleAddMember = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!activeWorkspace) return;
        setMemberError(null);
        setAddingMember(true);
        try {
            await apiAddWorkspaceMember(activeWorkspace.id, newMemberEmail, newMemberRole);
            setNewMemberEmail('');
            await loadMembers(activeWorkspace.id);
        } catch (err: any) {
            setMemberError(err.response?.data?.detail || 'Failed to add member');
        } finally {
            setAddingMember(false);
        }
    };

    const handleRoleChange = async (userId: string, role: WorkspaceRole) => {
        if (!activeWorkspace) return;
        try {
            await apiUpdateWorkspaceMemberRole(activeWorkspace.id, userId, role);
            await loadMembers(activeWorkspace.id);
        } catch (err: any) {
            alert(err.response?.data?.detail || 'Failed to update role');
        }
    };

    const handleRemoveMember = async (userId: string) => {
        if (!activeWorkspace) return;
        if (!window.confirm('Are you sure you want to remove this member?')) return;
        try {
            await apiRemoveWorkspaceMember(activeWorkspace.id, userId);
            await loadMembers(activeWorkspace.id);
        } catch (err: any) {
            alert(err.response?.data?.detail || 'Failed to remove member');
        }
    };

    return (
        <div>
            <h1 style={{ fontSize: '1.75rem', marginBottom: '0.5rem' }}>Workspace Management</h1>
            <p style={{ color: '#475569' }}>
                NexusMCP strictly isolates all workflows, credentials, and logs per workspace.
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem', marginTop: '1.5rem' }}>
                {/* Workspaces List & Switcher */}
                <div>
                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem', marginBottom: '1.5rem' }}>
                        <h3 style={{ marginTop: 0, marginBottom: '1rem' }}>Your Workspaces</h3>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                            {workspaces.map((ws) => {
                                const isActive = activeWorkspace?.id === ws.id;
                                return (
                                    <div
                                        key={ws.id}
                                        onClick={() => switchWorkspace(ws)}
                                        style={{
                                            padding: '1rem',
                                            border: isActive ? '2px solid #2563eb' : '1px solid #e2e8f0',
                                            borderRadius: '6px',
                                            backgroundColor: isActive ? '#eff6ff' : '#f8fafc',
                                            cursor: 'pointer',
                                            display: 'flex',
                                            justifyContent: 'space-between',
                                            alignItems: 'center',
                                        }}
                                    >
                                        <div>
                                            <div style={{ fontWeight: 600, color: '#1e293b' }}>
                                                {ws.name} {isActive && <span style={{ color: '#2563eb', fontSize: '0.75rem' }}>(Active)</span>}
                                            </div>
                                            <div style={{ fontSize: '0.8rem', color: '#64748b' }}>
                                                Slug: {ws.slug} {ws.description ? `• ${ws.description}` : ''}
                                            </div>
                                        </div>
                                        <span
                                            style={{
                                                fontSize: '0.75rem',
                                                padding: '0.25rem 0.5rem',
                                                borderRadius: '4px',
                                                backgroundColor: ws.role === 'owner' ? '#fef3c7' : ws.role === 'admin' ? '#e0e7ff' : '#f1f5f9',
                                                color: ws.role === 'owner' ? '#92400e' : ws.role === 'admin' ? '#3730a3' : '#475569',
                                                fontWeight: 600,
                                                textTransform: 'uppercase',
                                            }}
                                        >
                                            {ws.role}
                                        </span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* Create New Workspace */}
                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                        <h3 style={{ marginTop: 0, marginBottom: '1rem' }}>Create New Workspace</h3>
                        {wsError && (
                            <div style={{ padding: '0.5rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '4px', marginBottom: '1rem', fontSize: '0.875rem' }}>
                                {wsError}
                            </div>
                        )}
                        <form onSubmit={handleCreateWorkspace} style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                            <div>
                                <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.25rem' }}>Workspace Name</label>
                                <input
                                    type="text"
                                    required
                                    value={newWsName}
                                    onChange={(e) => setNewWsName(e.target.value)}
                                    placeholder="Marketing Team"
                                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', boxSizing: 'border-box' }}
                                />
                            </div>
                            <div>
                                <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.25rem' }}>Description (Optional)</label>
                                <input
                                    type="text"
                                    value={newWsDesc}
                                    onChange={(e) => setNewWsDesc(e.target.value)}
                                    placeholder="Workflows for marketing automation"
                                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', boxSizing: 'border-box' }}
                                />
                            </div>
                            <button
                                type="submit"
                                disabled={creatingWs}
                                style={{
                                    padding: '0.5rem 1rem',
                                    backgroundColor: '#0f172a',
                                    color: '#fff',
                                    border: 'none',
                                    borderRadius: '4px',
                                    fontWeight: 600,
                                    cursor: 'pointer',
                                    alignSelf: 'flex-start',
                                }}
                            >
                                {creatingWs ? 'Creating...' : '+ Create Workspace'}
                            </button>
                        </form>
                    </div>
                </div>

                {/* Active Workspace Members & RBAC */}
                <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                        <div>
                            <h3 style={{ margin: 0 }}>Team Members</h3>
                            <div style={{ fontSize: '0.85rem', color: '#64748b' }}>
                                Workspace: <strong>{activeWorkspace?.name || 'None'}</strong>
                            </div>
                        </div>
                        <span
                            style={{
                                fontSize: '0.75rem',
                                padding: '0.25rem 0.5rem',
                                borderRadius: '4px',
                                backgroundColor: activeWorkspace?.role === 'owner' ? '#fef3c7' : '#e0e7ff',
                                color: activeWorkspace?.role === 'owner' ? '#92400e' : '#3730a3',
                                fontWeight: 600,
                            }}
                        >
                            Your Role: {activeWorkspace?.role}
                        </span>
                    </div>

                    {/* Add Member Form (Owner / Admin) */}
                    {isAdminOrOwner && (
                        <div style={{ padding: '1rem', backgroundColor: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '6px', marginBottom: '1.5rem' }}>
                            <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9rem' }}>Invite Member</h4>
                            {memberError && (
                                <div style={{ padding: '0.5rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '4px', marginBottom: '0.75rem', fontSize: '0.8rem' }}>
                                    {memberError}
                                </div>
                            )}
                            <form onSubmit={handleAddMember} style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                                <div style={{ flex: 1, minWidth: '180px' }}>
                                    <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, marginBottom: '0.25rem' }}>Email</label>
                                    <input
                                        type="email"
                                        required
                                        value={newMemberEmail}
                                        onChange={(e) => setNewMemberEmail(e.target.value)}
                                        placeholder="colleague@company.com"
                                        style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', boxSizing: 'border-box' }}
                                    />
                                </div>
                                <div style={{ width: '110px' }}>
                                    <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, marginBottom: '0.25rem' }}>Role</label>
                                    <select
                                        value={newMemberRole}
                                        onChange={(e) => setNewMemberRole(e.target.value as WorkspaceRole)}
                                        style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', boxSizing: 'border-box' }}
                                    >
                                        <option value="member">Member</option>
                                        <option value="admin">Admin</option>
                                    </select>
                                </div>
                                <button
                                    type="submit"
                                    disabled={addingMember}
                                    style={{
                                        padding: '0.5rem 1rem',
                                        backgroundColor: '#2563eb',
                                        color: '#fff',
                                        border: 'none',
                                        borderRadius: '4px',
                                        fontWeight: 600,
                                        cursor: 'pointer',
                                    }}
                                >
                                    {addingMember ? 'Adding...' : 'Add'}
                                </button>
                            </form>
                        </div>
                    )}

                    {loadingMembers && <p style={{ color: '#64748b' }}>Loading members...</p>}

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                        {members.map((m) => (
                            <div
                                key={m.id}
                                style={{
                                    display: 'flex',
                                    justifyContent: 'space-between',
                                    alignItems: 'center',
                                    padding: '0.75rem',
                                    border: '1px solid #f1f5f9',
                                    borderRadius: '6px',
                                    backgroundColor: '#fafafa',
                                }}
                            >
                                <div>
                                    <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>{m.full_name}</div>
                                    <div style={{ fontSize: '0.8rem', color: '#64748b' }}>{m.email}</div>
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                    {isOwner && m.role !== 'owner' ? (
                                        <select
                                            value={m.role}
                                            onChange={(e) => handleRoleChange(m.user_id, e.target.value as WorkspaceRole)}
                                            style={{ padding: '0.25rem 0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', fontSize: '0.8rem' }}
                                        >
                                            <option value="member">Member</option>
                                            <option value="admin">Admin</option>
                                            <option value="owner">Owner</option>
                                        </select>
                                    ) : (
                                        <span
                                            style={{
                                                fontSize: '0.75rem',
                                                padding: '0.25rem 0.5rem',
                                                borderRadius: '4px',
                                                backgroundColor: m.role === 'owner' ? '#fef3c7' : '#f1f5f9',
                                                color: m.role === 'owner' ? '#92400e' : '#475569',
                                                fontWeight: 600,
                                            }}
                                        >
                                            {m.role}
                                        </span>
                                    )}

                                    {isAdminOrOwner && m.role !== 'owner' && (
                                        <button
                                            onClick={() => handleRemoveMember(m.user_id)}
                                            style={{
                                                padding: '0.25rem 0.5rem',
                                                backgroundColor: '#fee2e2',
                                                color: '#b91c1c',
                                                border: 'none',
                                                borderRadius: '4px',
                                                fontSize: '0.75rem',
                                                cursor: 'pointer',
                                            }}
                                        >
                                            Remove
                                        </button>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
};
