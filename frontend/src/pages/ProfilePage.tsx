import React, { useState } from 'react';
import { apiUpdateProfile } from '../api/client';
import { useAuth } from '../context/AuthContext';

export const ProfilePage: React.FC = () => {
    const { user, refreshUser } = useAuth();
    const [fullName, setFullName] = useState(user?.full_name || '');
    const [currentPassword, setCurrentPassword] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [updatingProfile, setUpdatingProfile] = useState(false);
    const [updatingPassword, setUpdatingPassword] = useState(false);
    const [profileSuccess, setProfileSuccess] = useState<string | null>(null);
    const [profileError, setProfileError] = useState<string | null>(null);
    const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);
    const [passwordError, setPasswordError] = useState<string | null>(null);

    const handleUpdateName = async (e: React.FormEvent) => {
        e.preventDefault();
        setProfileError(null);
        setProfileSuccess(null);
        setUpdatingProfile(true);
        try {
            await apiUpdateProfile({ full_name: fullName });
            await refreshUser();
            setProfileSuccess('Profile name updated successfully.');
        } catch (err: any) {
            setProfileError(err.response?.data?.detail || 'Failed to update profile.');
        } finally {
            setUpdatingProfile(false);
        }
    };

    const handleUpdatePassword = async (e: React.FormEvent) => {
        e.preventDefault();
        setPasswordError(null);
        setPasswordSuccess(null);

        if (newPassword.length < 8) {
            setPasswordError('New password must be at least 8 characters long.');
            return;
        }
        if (newPassword !== confirmPassword) {
            setPasswordError('New passwords do not match.');
            return;
        }

        setUpdatingPassword(true);
        try {
            await apiUpdateProfile({
                current_password: currentPassword,
                new_password: newPassword,
            });
            setCurrentPassword('');
            setNewPassword('');
            setConfirmPassword('');
            setPasswordSuccess('Password updated successfully.');
        } catch (err: any) {
            setPasswordError(err.response?.data?.detail || 'Failed to update password.');
        } finally {
            setUpdatingPassword(false);
        }
    };

    return (
        <div style={{ maxWidth: '800px', margin: '0 auto' }}>
            <h1 style={{ fontSize: '1.75rem', marginBottom: '0.5rem' }}>User Profile</h1>
            <p style={{ color: '#475569' }}>Manage your personal account settings and security credentials.</p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem', marginTop: '1.5rem' }}>
                {/* General Information */}
                <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                    <h3 style={{ marginTop: 0, marginBottom: '1rem' }}>Personal Information</h3>

                    {profileSuccess && (
                        <div style={{ padding: '0.5rem', backgroundColor: '#dcfce7', color: '#166534', borderRadius: '4px', marginBottom: '1rem', fontSize: '0.875rem' }}>
                            {profileSuccess}
                        </div>
                    )}
                    {profileError && (
                        <div style={{ padding: '0.5rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '4px', marginBottom: '1rem', fontSize: '0.875rem' }}>
                            {profileError}
                        </div>
                    )}

                    <div style={{ marginBottom: '1rem' }}>
                        <label style={{ display: 'block', fontSize: '0.75rem', color: '#64748b', fontWeight: 600 }}>Email Address</label>
                        <div style={{ fontSize: '0.95rem', fontWeight: 500, padding: '0.5rem 0' }}>{user?.email}</div>
                    </div>

                    <div style={{ marginBottom: '1rem' }}>
                        <label style={{ display: 'block', fontSize: '0.75rem', color: '#64748b', fontWeight: 600 }}>Account ID</label>
                        <div style={{ fontSize: '0.85rem', color: '#64748b', fontFamily: 'monospace', padding: '0.25rem 0' }}>{user?.id}</div>
                    </div>

                    <form onSubmit={handleUpdateName} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                        <div>
                            <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.25rem' }}>Full Name</label>
                            <input
                                type="text"
                                required
                                value={fullName}
                                onChange={(e) => setFullName(e.target.value)}
                                style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', boxSizing: 'border-box' }}
                            />
                        </div>

                        <button
                            type="submit"
                            disabled={updatingProfile}
                            style={{
                                padding: '0.5rem 1rem',
                                backgroundColor: '#2563eb',
                                color: '#fff',
                                border: 'none',
                                borderRadius: '4px',
                                fontWeight: 600,
                                cursor: 'pointer',
                                alignSelf: 'flex-start',
                            }}
                        >
                            {updatingProfile ? 'Saving...' : 'Update Name'}
                        </button>
                    </form>
                </div>

                {/* Password / Security */}
                <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                    <h3 style={{ marginTop: 0, marginBottom: '1rem' }}>Change Password</h3>

                    {passwordSuccess && (
                        <div style={{ padding: '0.5rem', backgroundColor: '#dcfce7', color: '#166534', borderRadius: '4px', marginBottom: '1rem', fontSize: '0.875rem' }}>
                            {passwordSuccess}
                        </div>
                    )}
                    {passwordError && (
                        <div style={{ padding: '0.5rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '4px', marginBottom: '1rem', fontSize: '0.875rem' }}>
                            {passwordError}
                        </div>
                    )}

                    <form onSubmit={handleUpdatePassword} style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                        <div>
                            <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.25rem' }}>Current Password</label>
                            <input
                                type="password"
                                required
                                value={currentPassword}
                                onChange={(e) => setCurrentPassword(e.target.value)}
                                placeholder="••••••••"
                                style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', boxSizing: 'border-box' }}
                            />
                        </div>

                        <div>
                            <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.25rem' }}>New Password (min 8 chars)</label>
                            <input
                                type="password"
                                required
                                value={newPassword}
                                onChange={(e) => setNewPassword(e.target.value)}
                                placeholder="••••••••"
                                style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', boxSizing: 'border-box' }}
                            />
                        </div>

                        <div>
                            <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.25rem' }}>Confirm New Password</label>
                            <input
                                type="password"
                                required
                                value={confirmPassword}
                                onChange={(e) => setConfirmPassword(e.target.value)}
                                placeholder="••••••••"
                                style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', boxSizing: 'border-box' }}
                            />
                        </div>

                        <button
                            type="submit"
                            disabled={updatingPassword}
                            style={{
                                padding: '0.5rem 1rem',
                                backgroundColor: '#0f172a',
                                color: '#fff',
                                border: 'none',
                                borderRadius: '4px',
                                fontWeight: 600,
                                cursor: 'pointer',
                                alignSelf: 'flex-start',
                                marginTop: '0.5rem',
                            }}
                        >
                            {updatingPassword ? 'Changing Password...' : 'Change Password'}
                        </button>
                    </form>
                </div>
            </div>
        </div>
    );
};
