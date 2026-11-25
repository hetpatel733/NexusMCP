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
import { WorkflowsListPage } from './pages/WorkflowsListPage';
import { WorkflowEditorPage } from './pages/WorkflowEditorPage';
import { ExecutionsPage } from './pages/ExecutionsPage';
import { WorkflowHealthPage } from './pages/WorkflowHealthPage';
import { DeadLetterQueuePage } from './pages/DeadLetterQueuePage';

export const App: React.FC = () => {
    return (
        <AuthProvider>
            <BrowserRouter>
                <Routes>
                    <Route path="/" element={<Layout />}>
                        <Route index element={<Dashboard />} />
                        <Route path="login" element={<Login />} />
                        <Route path="register" element={<Register />} />
                        <Route path="workflows" element={<WorkflowsListPage />} />
                        <Route path="workflows/:workflowId" element={<WorkflowEditorPage />} />
                        <Route path="executions" element={<ExecutionsPage />} />
                        <Route path="health-metrics" element={<WorkflowHealthPage />} />
                        <Route path="dlq" element={<DeadLetterQueuePage />} />
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
