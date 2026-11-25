import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
    apiGetWorkflow,
    apiUpdateWorkflow,
    apiValidateWorkflow,
    apiActivateWorkflow,
    apiPauseWorkflow,
    apiResumeWorkflow,
    apiArchiveWorkflow,
    apiListWorkflowVersions,
    apiListTools,
    apiPromptEditWorkflow,
    WorkflowDetail,
    WorkflowStep,
    WorkflowStatus,
    WorkflowVersionRecord,
    ToolItem,
    DataMapping,
} from '../api/client';
import { useAuth } from '../context/AuthContext';

export const WorkflowEditorPage: React.FC = () => {
    const { workflowId } = useParams<{ workflowId: string }>();
    const { isAuthenticated, activeWorkspace } = useAuth();

    const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
    const [versions, setVersions] = useState<WorkflowVersionRecord[]>([]);
    const [availableTools, setAvailableTools] = useState<ToolItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [validating, setValidating] = useState(false);
    const [activating, setActivating] = useState(false);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    const [successMsg, setSuccessMsg] = useState<string | null>(null);

    // AI prompt-based editing state
    const [promptInput, setPromptInput] = useState('');
    const [promptLoading, setPromptLoading] = useState(false);
    const [promptClarifications, setPromptClarifications] = useState<any[]>([]);

    // Form state for editing
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [triggerType, setTriggerType] = useState('manual');
    const [triggerProvider, setTriggerProvider] = useState('');
    const [triggerEvent, setTriggerEvent] = useState('');
    const [triggerCron, setTriggerCron] = useState('');
    const [triggerInterval, setTriggerInterval] = useState<number | undefined>(undefined);
    const [steps, setSteps] = useState<WorkflowStep[]>([]);
    const [changeSummary, setChangeSummary] = useState('');
    const [requireApprovalConfirmed, setRequireApprovalConfirmed] = useState(false);

    // Validation results
    const [validationResult, setValidationResult] = useState<{
        valid: boolean;
        errors: string[];
    } | null>(null);

    const loadData = async () => {
        if (!workflowId) return;
        setLoading(true);
        setErrorMsg(null);
        try {
            const [wf, vers, tools] = await Promise.all([
                apiGetWorkflow(workflowId),
                apiListWorkflowVersions(workflowId),
                apiListTools().catch(() => []),
            ]);
            setWorkflow(wf);
            setVersions(vers);
            setAvailableTools(tools);

            setName(wf.name);
            setDescription(wf.description || '');
            setTriggerType(wf.trigger?.type || 'manual');
            setTriggerProvider(wf.trigger?.provider || '');
            setTriggerEvent(wf.trigger?.event || '');
            setTriggerCron(wf.trigger?.cron || '');
            setTriggerInterval(wf.trigger?.interval_seconds);
            setSteps(wf.steps || []);
        } catch (err: any) {
            setErrorMsg(err.response?.data?.detail || err.message || 'Failed to load workflow details');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (isAuthenticated && activeWorkspace && workflowId) {
            loadData();
        }
    }, [isAuthenticated, activeWorkspace, workflowId]);

    const handleSave = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!workflowId) return;
        setSaving(true);
        setErrorMsg(null);
        setSuccessMsg(null);
        try {
            const triggerPayload: any = {
                type: triggerType,
                parameters: workflow?.trigger?.parameters || {},
            };
            if (triggerProvider) triggerPayload.provider = triggerProvider;
            if (triggerEvent) triggerPayload.event = triggerEvent;
            if (triggerCron) triggerPayload.cron = triggerCron;
            if (triggerInterval) triggerPayload.interval_seconds = Number(triggerInterval);

            // Recompute required integrations from steps and trigger
            const integrations = new Set<string>();
            if (triggerProvider) integrations.add(triggerProvider);
            steps.forEach((s) => {
                if (s.app_name) integrations.add(s.app_name);
                else if (s.tool_id.includes(':')) {
                    const parts = s.tool_id.split(':');
                    if (parts.length >= 2 && parts[1]) integrations.add(parts[1]);
                }
            });

            const updated = await apiUpdateWorkflow(workflowId, {
                name,
                description,
                trigger: triggerPayload,
                steps,
                required_integrations: Array.from(integrations),
                change_summary: changeSummary || 'Workflow edited via Editor',
            });
            setWorkflow(updated);
            setChangeSummary('');
            setSuccessMsg(`Workflow saved successfully (v${updated.current_version}).`);
            // Refresh version history
            const vers = await apiListWorkflowVersions(workflowId);
            setVersions(vers);
        } catch (err: any) {
            setErrorMsg(err.response?.data?.detail || err.message || 'Failed to save workflow changes');
        } finally {
            setSaving(false);
        }
    };

    const handleValidate = async () => {
        if (!workflowId) return;
        setValidating(true);
        setErrorMsg(null);
        setSuccessMsg(null);
        try {
            const res = await apiValidateWorkflow(workflowId);
            setValidationResult({
                valid: res.valid,
                errors: res.errors,
            });
            if (res.valid) {
                setSuccessMsg('Deterministic validation passed! All graph nodes, registry tools, and integrations verified.');
            } else {
                setErrorMsg(`Validation detected issues: ${res.errors.join('; ')}`);
            }
            // Refresh workflow to update status (e.g. to ready or needs_attention)
            const wf = await apiGetWorkflow(workflowId);
            setWorkflow(wf);
        } catch (err: any) {
            setErrorMsg(err.response?.data?.detail || err.message || 'Validation request failed');
        } finally {
            setValidating(false);
        }
    };

    const handleActivate = async () => {
        if (!workflowId) return;
        if (!requireApprovalConfirmed) {
            setErrorMsg('Explicit user approval is required before activating this workflow.');
            return;
        }
        setActivating(true);
        setErrorMsg(null);
        setSuccessMsg(null);
        try {
            const updated = await apiActivateWorkflow(workflowId, true);
            setWorkflow(updated);
            setSuccessMsg(`Workflow activated successfully! Active version is v${updated.active_version}.`);
        } catch (err: any) {
            setErrorMsg(err.response?.data?.detail || err.message || 'Activation failed');
        } finally {
            setActivating(false);
        }
    };

    const handlePause = async () => {
        if (!workflowId) return;
        try {
            const updated = await apiPauseWorkflow(workflowId);
            setWorkflow(updated);
            setSuccessMsg('Workflow paused. Incoming triggers will be suspended.');
        } catch (err: any) {
            setErrorMsg(err.response?.data?.detail || err.message || 'Pause failed');
        }
    };

    const handleResume = async () => {
        if (!workflowId) return;
        try {
            const updated = await apiResumeWorkflow(workflowId);
            setWorkflow(updated);
            setSuccessMsg('Workflow resumed and active.');
        } catch (err: any) {
            setErrorMsg(err.response?.data?.detail || err.message || 'Resume failed');
        }
    };

    const handlePromptEdit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!workflowId || !promptInput.trim()) return;
        setPromptLoading(true);
        setErrorMsg(null);
        setSuccessMsg(null);
        setPromptClarifications([]);
        try {
            const res = await apiPromptEditWorkflow(workflowId, promptInput.trim(), true);
            if (res.result.status === 'needs_clarification' && res.result.clarifications && res.result.clarifications.length > 0) {
                setPromptClarifications(res.result.clarifications);
                setErrorMsg('AI needs clarification on missing parameters before applying changes.');
                return;
            }
            if (!res.result.success || !res.workflow) {
                setErrorMsg(res.result.errors?.join('; ') || 'AI prompt edit failed to generate a valid workflow definition.');
                return;
            }

            const updatedWf = res.workflow;
            setWorkflow(updatedWf);
            setName(updatedWf.name);
            setDescription(updatedWf.description || '');
            setTriggerType(updatedWf.trigger?.type || 'manual');
            setTriggerProvider(updatedWf.trigger?.provider || '');
            setTriggerEvent(updatedWf.trigger?.event || '');
            setTriggerCron(updatedWf.trigger?.cron || '');
            setTriggerInterval(updatedWf.trigger?.interval_seconds);
            setSteps(updatedWf.steps || []);
            setPromptInput('');

            const vers = await apiListWorkflowVersions(workflowId);
            setVersions(vers);
            setSuccessMsg(`Workflow successfully updated via prompt! (v${updatedWf.current_version})`);
        } catch (err: any) {
            setErrorMsg(err.response?.data?.detail || err.message || 'Failed to edit workflow with prompt');
        } finally {
            setPromptLoading(false);
        }
    };

    const handleArchive = async () => {
        if (!workflowId) return;
        if (!window.confirm('Are you sure you want to archive this workflow? This will permanently disable execution.')) {
            return;
        }
        try {
            const updated = await apiArchiveWorkflow(workflowId);
            setWorkflow(updated);
            setSuccessMsg('Workflow archived.');
        } catch (err: any) {
            setErrorMsg(err.response?.data?.detail || err.message || 'Archive failed');
        }
    };

    // Step operations
    const updateStep = (index: number, updated: Partial<WorkflowStep>) => {
        const copy = [...steps];
        copy[index] = { ...copy[index], ...updated };
        setSteps(copy);
    };

    const addStep = () => {
        const newId = `step_${steps.length + 1}`;
        const newStep: WorkflowStep = {
            id: newId,
            name: `Step ${steps.length + 1}`,
            tool_id: availableTools[0]?.id || 'composio:slack:send_message',
            provider: availableTools[0]?.provider || 'composio',
            arguments: {},
            data_mappings: [],
            dependencies: steps.length > 0 ? [steps[steps.length - 1].id] : [],
            conditions: [],
            error_policy: {
                strategy: 'stop',
                max_retries: 3,
                retry_delay_seconds: 5,
                backoff_multiplier: 2.0,
            },
        };
        setSteps([...steps, newStep]);
    };

    const removeStep = (index: number) => {
        const copy = [...steps];
        copy.splice(index, 1);
        setSteps(copy);
    };

    const getStatusBadge = (status: WorkflowStatus) => {
        const styles: Record<WorkflowStatus, { bg: string; color: string; label: string }> = {
            draft: { bg: '#f1f5f9', color: '#475569', label: 'Draft' },
            ready: { bg: '#e0e7ff', color: '#3730a3', label: 'Ready for Review' },
            active: { bg: '#dcfce7', color: '#166534', label: 'Active' },
            paused: { bg: '#fef3c7', color: '#92400e', label: 'Paused' },
            needs_attention: { bg: '#fee2e2', color: '#991b1b', label: 'Needs Attention' },
            archived: { bg: '#f3f4f6', color: '#6b7280', label: 'Archived' },
        };
        const s = styles[status] || styles.draft;
        return (
            <span
                style={{
                    backgroundColor: s.bg,
                    color: s.color,
                    padding: '0.25rem 0.65rem',
                    borderRadius: '9999px',
                    fontSize: '0.75rem',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                }}
            >
                {s.label}
            </span>
        );
    };

    if (loading) {
        return <div style={{ padding: '3rem', textAlign: 'center', color: '#64748b' }}>Loading workflow details...</div>;
    }

    if (!workflow) {
        return (
            <div style={{ padding: '3rem', textAlign: 'center' }}>
                <h2>Workflow Not Found</h2>
                <Link to="/workflows">Back to Workflows</Link>
            </div>
        );
    }

    const isActive = workflow.status === 'active';
    const isPaused = workflow.status === 'paused';
    const isArchived = workflow.status === 'archived';

    return (
        <div style={{ maxWidth: '1050px', margin: '0 auto', paddingBottom: '3rem' }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
                <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
                        <Link to="/workflows" style={{ color: '#64748b', textDecoration: 'none', fontSize: '0.875rem' }}>
                            &larr; Workflows
                        </Link>
                        <span style={{ color: '#cbd5e1' }}>/</span>
                        <h1 style={{ fontSize: '1.75rem', margin: 0, color: '#0f172a' }}>{workflow.name}</h1>
                        {getStatusBadge(workflow.status)}
                    </div>
                    <div style={{ fontSize: '0.85rem', color: '#64748b' }}>
                        Current Version: <strong>v{workflow.current_version}</strong>
                        {workflow.active_version ? (
                            <span style={{ marginLeft: '1rem', color: '#166534', fontWeight: 600 }}>
                                (Active Execution Version: v{workflow.active_version})
                            </span>
                        ) : (
                            <span style={{ marginLeft: '1rem', color: '#94a3b8' }}>
                                (Not yet activated)
                            </span>
                        )}
                    </div>
                </div>

                {/* Workflow Actions */}
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                    <button
                        onClick={handleValidate}
                        disabled={validating || isArchived}
                        style={{
                            padding: '0.5rem 1rem',
                            backgroundColor: '#f8fafc',
                            border: '1px solid #cbd5e1',
                            borderRadius: '6px',
                            fontWeight: 600,
                            cursor: 'pointer',
                            fontSize: '0.85rem',
                        }}
                    >
                        {validating ? 'Validating...' : '✓ Validate Graph'}
                    </button>

                    {isActive && (
                        <button
                            onClick={handlePause}
                            style={{
                                padding: '0.5rem 1rem',
                                backgroundColor: '#fef3c7',
                                border: '1px solid #fde68a',
                                color: '#92400e',
                                borderRadius: '6px',
                                fontWeight: 600,
                                cursor: 'pointer',
                                fontSize: '0.85rem',
                            }}
                        >
                            Pause Workflow
                        </button>
                    )}

                    {isPaused && (
                        <button
                            onClick={handleResume}
                            style={{
                                padding: '0.5rem 1rem',
                                backgroundColor: '#dcfce7',
                                border: '1px solid #bbf7d0',
                                color: '#166534',
                                borderRadius: '6px',
                                fontWeight: 600,
                                cursor: 'pointer',
                                fontSize: '0.85rem',
                            }}
                        >
                            Resume Workflow
                        </button>
                    )}

                    {!isArchived && (
                        <button
                            onClick={handleArchive}
                            style={{
                                padding: '0.5rem 0.75rem',
                                backgroundColor: '#fff',
                                border: '1px solid #e2e8f0',
                                color: '#64748b',
                                borderRadius: '6px',
                                fontSize: '0.85rem',
                                cursor: 'pointer',
                            }}
                        >
                            Archive
                        </button>
                    )}
                </div>
            </div>

            {/* Notification messages */}
            {errorMsg && (
                <div style={{ padding: '0.75rem 1rem', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '6px', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
                    <strong>Error:</strong> {errorMsg}
                </div>
            )}
            {successMsg && (
                <div style={{ padding: '0.75rem 1rem', backgroundColor: '#dcfce7', color: '#166534', borderRadius: '6px', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
                    <strong>Success:</strong> {successMsg}
                </div>
            )}

            {/* Activation & Approval Bar (Requirement 8) */}
            {!isActive && !isArchived && (
                <div style={{ backgroundColor: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '8px', padding: '1.25rem', marginBottom: '2rem' }}>
                    <h3 style={{ margin: '0 0 0.5rem 0', color: '#166534', fontSize: '1.1rem' }}>
                        Workflow Activation & Approval
                    </h3>
                    <p style={{ margin: '0 0 1rem 0', color: '#15803d', fontSize: '0.875rem' }}>
                        Activating this workflow will bind triggers to background execution workers. Review all steps, permissions, and mappings below before confirming approval.
                    </p>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.875rem', color: '#14532d', fontWeight: 600, cursor: 'pointer' }}>
                            <input
                                type="checkbox"
                                checked={requireApprovalConfirmed}
                                onChange={(e) => setRequireApprovalConfirmed(e.target.checked)}
                            />
                            I have reviewed the trigger, step dependencies, and permissions and explicitly approve activating version v{workflow.current_version}.
                        </label>
                        <button
                            onClick={handleActivate}
                            disabled={activating || !requireApprovalConfirmed}
                            style={{
                                padding: '0.5rem 1.25rem',
                                backgroundColor: requireApprovalConfirmed ? '#16a34a' : '#cbd5e1',
                                color: '#fff',
                                border: 'none',
                                borderRadius: '6px',
                                fontWeight: 600,
                                fontSize: '0.875rem',
                                cursor: requireApprovalConfirmed ? 'pointer' : 'not-allowed',
                            }}
                        >
                            {activating ? 'Activating...' : 'Approve & Activate Workflow'}
                        </button>
                    </div>
                </div>
            )}

            {isActive && (
                <div style={{ backgroundColor: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: '8px', padding: '1rem 1.25rem', marginBottom: '2rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                        <span style={{ fontSize: '1.25rem' }}>⚡</span>
                        <div>
                            <strong style={{ color: '#1e40af' }}>Workflow is Active:</strong> New triggers execute version v{workflow.active_version}.
                            <div style={{ fontSize: '0.8rem', color: '#3b82f6', marginTop: '0.2rem' }}>
                                Any edits saved below will create version v{workflow.current_version + 1} without modifying active execution v{workflow.active_version}.
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* Validation Panel */}
            {validationResult && (
                <div
                    style={{
                        backgroundColor: validationResult.valid ? '#f0fdf4' : '#fef2f2',
                        border: `1px solid ${validationResult.valid ? '#bbf7d0' : '#fecaca'}`,
                        borderRadius: '8px',
                        padding: '1rem 1.25rem',
                        marginBottom: '2rem',
                    }}
                >
                    <div style={{ fontWeight: 600, color: validationResult.valid ? '#166534' : '#991b1b', marginBottom: '0.5rem' }}>
                        {validationResult.valid ? '✓ Validation Passed' : '✗ Validation Failed'}
                    </div>
                    {validationResult.errors.length > 0 && (
                        <ul style={{ margin: 0, paddingLeft: '1.25rem', color: '#b91c1c', fontSize: '0.85rem' }}>
                            {validationResult.errors.map((e, idx) => (
                                <li key={idx}>{e}</li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {/* AI Prompt-Driven Workflow Editing (Requirement: Edit through prompt) */}
            {!isArchived && (
                <div style={{ backgroundColor: '#f8fafc', border: '1px solid #cbd5e1', borderRadius: '8px', padding: '1.25rem', marginBottom: '2rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.35rem' }}>
                        <span style={{ fontSize: '1.1rem' }}>✨</span>
                        <h3 style={{ margin: 0, fontSize: '1.05rem', color: '#0f172a' }}>Edit Workflow with AI Prompt</h3>
                    </div>
                    <p style={{ margin: '0 0 1rem 0', color: '#64748b', fontSize: '0.85rem' }}>
                        Instruct the AI to modify triggers, add or adjust steps, update data mappings, or change retry policies.
                    </p>
                    <form onSubmit={handlePromptEdit}>
                        <textarea
                            rows={3}
                            value={promptInput}
                            onChange={(e) => setPromptInput(e.target.value)}
                            placeholder="e.g. Change trigger to webhook on 'pull_request.closed', and add a step to send a summary to Slack #deployments"
                            disabled={promptLoading}
                            style={{ width: '100%', padding: '0.6rem', border: '1px solid #cbd5e1', borderRadius: '6px', fontSize: '0.875rem', marginBottom: '0.75rem', boxSizing: 'border-box' }}
                        />
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
                            <div style={{ fontSize: '0.8rem', color: '#64748b' }}>
                                {isActive && (
                                    <span>
                                        Editing will create <strong>v{workflow.current_version + 1}</strong> as a draft without interrupting active v{workflow.active_version}.
                                    </span>
                                )}
                            </div>
                            <button
                                type="submit"
                                disabled={promptLoading || !promptInput.trim()}
                                style={{
                                    padding: '0.5rem 1.25rem',
                                    backgroundColor: '#2563eb',
                                    color: '#fff',
                                    border: 'none',
                                    borderRadius: '6px',
                                    fontWeight: 600,
                                    fontSize: '0.875rem',
                                    cursor: promptLoading || !promptInput.trim() ? 'not-allowed' : 'pointer',
                                }}
                            >
                                {promptLoading ? 'Applying AI Edits...' : 'Apply Prompt Changes'}
                            </button>
                        </div>
                    </form>

                    {promptClarifications.length > 0 && (
                        <div style={{ marginTop: '1rem', padding: '0.75rem 1rem', backgroundColor: '#fef3c7', border: '1px solid #fde68a', borderRadius: '6px' }}>
                            <strong style={{ color: '#92400e', fontSize: '0.85rem' }}>Clarification Needed:</strong>
                            <ul style={{ margin: '0.35rem 0 0 0', paddingLeft: '1.25rem', fontSize: '0.8rem', color: '#78350f' }}>
                                {promptClarifications.map((c, i) => (
                                    <li key={i}>{c.prompt}</li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            )}

            {/* Editor Form */}
            <form onSubmit={handleSave}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.5rem', marginBottom: '1.5rem' }}>
                    {/* General Configuration */}
                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.25rem' }}>
                        <h3 style={{ margin: '0 0 1rem 0', fontSize: '1rem', color: '#0f172a' }}>General Information</h3>
                        <div style={{ marginBottom: '1rem' }}>
                            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, color: '#334155', marginBottom: '0.35rem' }}>
                                Workflow Name *
                            </label>
                            <input
                                type="text"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                required
                                disabled={isArchived}
                                style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '6px', fontSize: '0.875rem' }}
                            />
                        </div>
                        <div>
                            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, color: '#334155', marginBottom: '0.35rem' }}>
                                Description
                            </label>
                            <textarea
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                                rows={3}
                                disabled={isArchived}
                                style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '6px', fontSize: '0.875rem' }}
                            />
                        </div>
                    </div>

                    {/* Trigger Configuration (Requirement 1) */}
                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.25rem' }}>
                        <h3 style={{ margin: '0 0 1rem 0', fontSize: '1rem', color: '#0f172a' }}>Trigger Configuration</h3>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '0.75rem' }}>
                            <div>
                                <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: '#334155', marginBottom: '0.3rem' }}>
                                    Trigger Type
                                </label>
                                <select
                                    value={triggerType}
                                    onChange={(e) => setTriggerType(e.target.value)}
                                    disabled={isArchived}
                                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '6px', fontSize: '0.85rem' }}
                                >
                                    <option value="manual">Manual</option>
                                    <option value="webhook">Webhook</option>
                                    <option value="polling">Polling</option>
                                    <option value="schedule">Schedule</option>
                                </select>
                            </div>
                            <div>
                                <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: '#334155', marginBottom: '0.3rem' }}>
                                    App Provider
                                </label>
                                <input
                                    type="text"
                                    value={triggerProvider}
                                    placeholder="e.g. github, slack"
                                    onChange={(e) => setTriggerProvider(e.target.value)}
                                    disabled={isArchived}
                                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '6px', fontSize: '0.85rem' }}
                                />
                            </div>
                        </div>

                        {triggerType === 'schedule' && (
                            <div style={{ marginBottom: '0.75rem' }}>
                                <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: '#334155', marginBottom: '0.3rem' }}>
                                    Cron Expression
                                </label>
                                <input
                                    type="text"
                                    value={triggerCron}
                                    placeholder="0 9 * * 1-5"
                                    onChange={(e) => setTriggerCron(e.target.value)}
                                    disabled={isArchived}
                                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '6px', fontSize: '0.85rem' }}
                                />
                            </div>
                        )}

                        {triggerType === 'polling' && (
                            <div style={{ marginBottom: '0.75rem' }}>
                                <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: '#334155', marginBottom: '0.3rem' }}>
                                    Interval (seconds)
                                </label>
                                <input
                                    type="number"
                                    min={10}
                                    value={triggerInterval || ''}
                                    placeholder="60"
                                    onChange={(e) => setTriggerInterval(e.target.value ? Number(e.target.value) : undefined)}
                                    disabled={isArchived}
                                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '6px', fontSize: '0.85rem' }}
                                />
                            </div>
                        )}

                        <div>
                            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: '#334155', marginBottom: '0.3rem' }}>
                                Event Name
                            </label>
                            <input
                                type="text"
                                value={triggerEvent}
                                placeholder="e.g. issue_opened, message_posted"
                                onChange={(e) => setTriggerEvent(e.target.value)}
                                disabled={isArchived}
                                style={{ width: '100%', padding: '0.5rem', border: '1px solid #cbd5e1', borderRadius: '6px', fontSize: '0.85rem' }}
                            />
                        </div>
                    </div>
                </div>

                {/* Steps & Actions (Requirements 1 & 2) */}
                <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem', marginBottom: '1.5rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                        <div>
                            <h3 style={{ margin: 0, fontSize: '1.1rem', color: '#0f172a' }}>Workflow Steps & DAG</h3>
                            <p style={{ margin: '0.2rem 0 0 0', fontSize: '0.85rem', color: '#64748b' }}>
                                Configure actions, input arguments, dynamic data mappings, and execution dependencies.
                            </p>
                        </div>
                        <button
                            type="button"
                            onClick={addStep}
                            disabled={isArchived}
                            style={{
                                padding: '0.4rem 0.8rem',
                                backgroundColor: '#f1f5f9',
                                border: '1px solid #cbd5e1',
                                borderRadius: '6px',
                                fontSize: '0.8rem',
                                fontWeight: 600,
                                cursor: 'pointer',
                            }}
                        >
                            + Add Step
                        </button>
                    </div>

                    {steps.length === 0 ? (
                        <div style={{ padding: '2rem', textAlign: 'center', color: '#94a3b8' }}>
                            No steps in this workflow. Click &quot;Add Step&quot; to configure an action.
                        </div>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                            {steps.map((step, idx) => (
                                <div
                                    key={step.id || idx}
                                    style={{
                                        border: '1px solid #e2e8f0',
                                        borderRadius: '6px',
                                        padding: '1rem',
                                        backgroundColor: '#fafbfc',
                                    }}
                                >
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                            <span style={{ fontWeight: 700, fontSize: '0.85rem', color: '#2563eb' }}>
                                                #{idx + 1} [{step.id}]
                                            </span>
                                            <input
                                                type="text"
                                                value={step.name}
                                                onChange={(e) => updateStep(idx, { name: e.target.value })}
                                                disabled={isArchived}
                                                style={{ padding: '0.25rem 0.5rem', border: '1px solid #cbd5e1', borderRadius: '4px', fontSize: '0.85rem', fontWeight: 600 }}
                                            />
                                        </div>
                                        <button
                                            type="button"
                                            onClick={() => removeStep(idx)}
                                            disabled={isArchived}
                                            style={{
                                                padding: '0.2rem 0.5rem',
                                                backgroundColor: '#fee2e2',
                                                color: '#b91c1c',
                                                border: 'none',
                                                borderRadius: '4px',
                                                fontSize: '0.75rem',
                                                cursor: 'pointer',
                                            }}
                                        >
                                            Delete
                                        </button>
                                    </div>

                                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '0.75rem', marginBottom: '0.75rem' }}>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: '#475569', marginBottom: '0.2rem' }}>
                                                Tool ID (Registry)
                                            </label>
                                            <input
                                                type="text"
                                                value={step.tool_id}
                                                onChange={(e) => updateStep(idx, { tool_id: e.target.value })}
                                                disabled={isArchived}
                                                list="tool-options"
                                                style={{ width: '100%', padding: '0.4rem', border: '1px solid #cbd5e1', borderRadius: '4px', fontSize: '0.8rem' }}
                                            />
                                        </div>

                                        <div>
                                            <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: '#475569', marginBottom: '0.2rem' }}>
                                                Dependencies (Step IDs, comma-separated)
                                            </label>
                                            <input
                                                type="text"
                                                value={step.dependencies.join(', ')}
                                                onChange={(e) => {
                                                    const deps = e.target.value.split(',').map((s) => s.trim()).filter(Boolean);
                                                    updateStep(idx, { dependencies: deps });
                                                }}
                                                disabled={isArchived}
                                                placeholder="e.g. step_1"
                                                style={{ width: '100%', padding: '0.4rem', border: '1px solid #cbd5e1', borderRadius: '4px', fontSize: '0.8rem' }}
                                            />
                                        </div>
                                    </div>

                                    {/* Arguments JSON */}
                                    <div style={{ marginBottom: '0.75rem' }}>
                                        <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: '#475569', marginBottom: '0.2rem' }}>
                                            Static Arguments (JSON)
                                        </label>
                                        <textarea
                                            rows={2}
                                            value={JSON.stringify(step.arguments || {}, null, 2)}
                                            onChange={(e) => {
                                                try {
                                                    const parsed = JSON.parse(e.target.value);
                                                    updateStep(idx, { arguments: parsed });
                                                } catch {
                                                    // Invalid JSON while typing
                                                }
                                            }}
                                            disabled={isArchived}
                                            style={{ width: '100%', padding: '0.4rem', border: '1px solid #cbd5e1', borderRadius: '4px', fontFamily: 'monospace', fontSize: '0.75rem' }}
                                        />
                                    </div>

                                    {/* Data Mappings (Requirement 1 & 2) */}
                                    <div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.3rem' }}>
                                            <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#475569' }}>
                                                Data Mappings ({step.data_mappings?.length || 0})
                                            </label>
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    const newMappings: DataMapping[] = [
                                                        ...(step.data_mappings || []),
                                                        { target_field: 'input_arg', source: 'trigger.data' },
                                                    ];
                                                    updateStep(idx, { data_mappings: newMappings });
                                                }}
                                                disabled={isArchived}
                                                style={{ fontSize: '0.7rem', padding: '0.15rem 0.4rem', cursor: 'pointer', borderRadius: '4px', border: '1px solid #cbd5e1' }}
                                            >
                                                + Add Mapping
                                            </button>
                                        </div>

                                        {step.data_mappings && step.data_mappings.length > 0 && (
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                                                {step.data_mappings.map((m, mIdx) => (
                                                    <div key={mIdx} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                                                        <input
                                                            type="text"
                                                            placeholder="Target field"
                                                            value={m.target_field}
                                                            onChange={(e) => {
                                                                const copy = [...step.data_mappings];
                                                                copy[mIdx].target_field = e.target.value;
                                                                updateStep(idx, { data_mappings: copy });
                                                            }}
                                                            disabled={isArchived}
                                                            style={{ flex: 1, padding: '0.3rem', fontSize: '0.75rem', border: '1px solid #cbd5e1', borderRadius: '4px' }}
                                                        />
                                                        <span style={{ fontSize: '0.75rem', color: '#64748b' }}>&larr;</span>
                                                        <input
                                                            type="text"
                                                            placeholder="Source (e.g. steps.step_1.output.id or trigger.data.id)"
                                                            value={m.source}
                                                            onChange={(e) => {
                                                                const copy = [...step.data_mappings];
                                                                copy[mIdx].source = e.target.value;
                                                                updateStep(idx, { data_mappings: copy });
                                                            }}
                                                            disabled={isArchived}
                                                            style={{ flex: 2, padding: '0.3rem', fontSize: '0.75rem', border: '1px solid #cbd5e1', borderRadius: '4px' }}
                                                        />
                                                        <button
                                                            type="button"
                                                            onClick={() => {
                                                                const copy = [...step.data_mappings];
                                                                copy.splice(mIdx, 1);
                                                                updateStep(idx, { data_mappings: copy });
                                                            }}
                                                            disabled={isArchived}
                                                            style={{ color: '#ef4444', border: 'none', background: 'none', cursor: 'pointer' }}
                                                        >
                                                            &times;
                                                        </button>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Tool registry datalist */}
                <datalist id="tool-options">
                    {availableTools.map((t) => (
                        <option key={t.id} value={t.id}>
                            {t.name} ({t.provider})
                        </option>
                    ))}
                </datalist>

                {/* Save Changes Bar */}
                {!isArchived && (
                    <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.25rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem', marginBottom: '2rem' }}>
                        <div style={{ flex: '1 1 300px' }}>
                            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: '#334155', marginBottom: '0.2rem' }}>
                                Change Summary (Immutable Version Audit)
                            </label>
                            <input
                                type="text"
                                placeholder="Describe your modifications (e.g. updated parameter mapping)"
                                value={changeSummary}
                                onChange={(e) => setChangeSummary(e.target.value)}
                                style={{ width: '100%', padding: '0.45rem', border: '1px solid #cbd5e1', borderRadius: '6px', fontSize: '0.85rem' }}
                            />
                        </div>
                        <button
                            type="submit"
                            disabled={saving}
                            style={{
                                padding: '0.65rem 1.5rem',
                                backgroundColor: '#2563eb',
                                color: '#fff',
                                border: 'none',
                                borderRadius: '6px',
                                fontWeight: 600,
                                fontSize: '0.875rem',
                                cursor: 'pointer',
                            }}
                        >
                            {saving ? 'Saving...' : isActive ? 'Save Changes as New Version' : 'Save Workflow Draft'}
                        </button>
                    </div>
                )}
            </form>

            {/* Version History (Requirements 6 & 7) */}
            <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                <h3 style={{ margin: '0 0 1rem 0', fontSize: '1.1rem', color: '#0f172a' }}>Immutable Version History</h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                    {versions.map((v) => {
                        const isCurrent = v.version === workflow.current_version;
                        const isExecutionActive = v.version === workflow.active_version;
                        return (
                            <div
                                key={v.id}
                                style={{
                                    border: '1px solid #e2e8f0',
                                    borderRadius: '6px',
                                    padding: '0.75rem 1rem',
                                    display: 'flex',
                                    justifyContent: 'space-between',
                                    alignItems: 'center',
                                    backgroundColor: isExecutionActive ? '#f0fdf4' : '#fff',
                                }}
                            >
                                <div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.2rem' }}>
                                        <strong style={{ fontSize: '0.9rem', color: '#0f172a' }}>Version {v.version}</strong>
                                        {isCurrent && (
                                            <span style={{ fontSize: '0.7rem', backgroundColor: '#e0e7ff', color: '#3730a3', padding: '0.1rem 0.4rem', borderRadius: '4px', fontWeight: 600 }}>
                                                CURRENT DRAFT
                                            </span>
                                        )}
                                        {isExecutionActive && (
                                            <span style={{ fontSize: '0.7rem', backgroundColor: '#dcfce7', color: '#166534', padding: '0.1rem 0.4rem', borderRadius: '4px', fontWeight: 600 }}>
                                                ACTIVE EXECUTION VERSION
                                            </span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: '0.8rem', color: '#64748b' }}>
                                        {v.change_summary || 'No change description recorded.'}
                                    </div>
                                </div>
                                <div style={{ fontSize: '0.75rem', color: '#94a3b8', textAlign: 'right' }}>
                                    <div>{new Date(v.created_at).toLocaleString()}</div>
                                    <div>{v.definition?.steps?.length || 0} steps</div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
};
