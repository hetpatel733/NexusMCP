import React from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { AuthProvider } from './context/AuthContext';
import { AuditLogsPage } from './pages/AuditLogsPage';
import { Dashboard } from './pages/Dashboard';
import { IntegrationsPage } from './pages/IntegrationsPage';
import { Login } from './pages/Login';
import { ProfilePage } from './pages/ProfilePage';
import { Register } from './pages/Register';
import { StatusPage } from './pages/Status';
import { WorkspacesPage } from './pages/WorkspacesPage';
import { WorkflowGenerationPage } from './pages/WorkflowGenerationPage';

export const App: React.FC = () => {
    return (
        <AuthProvider>
            <BrowserRouter>
                <Routes>
                    <Route path="/" element={<Layout />}>
                        <Route index element={<Dashboard />} />
                        <Route path="login" element={<Login />} />
                        <Route path="register" element={<Register />} />
                        <Route path="designer" element={<WorkflowGenerationPage />} />
                        <Route path="workspaces" element={<WorkspacesPage />} />
                        <Route path="profile" element={<ProfilePage />} />
                        <Route path="integrations" element={<IntegrationsPage />} />
                        <Route path="audit" element={<AuditLogsPage />} />
                        <Route path="status" element={<StatusPage />} />
                    </Route>
                </Routes>
            </BrowserRouter>
        </AuthProvider>
    );
};

export default App;
