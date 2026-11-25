import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { apiClient } from '../api/client';
import { useAuth } from '../context/AuthContext';

interface WorkflowStep {
    id: string;
    name: string;
    tool_id: string;
    provider: string;
    arguments: Record<string, any>;
    dependencies: string[];
    error_policy?: {
        strategy: string;
        max_retries: number;
    };
}

interface StepExplanation {
    step_id: string;
    step_name: string;
    tool_id: string;
    action_summary: string;
    permissions_required: string[];
    risk_level: string;
}

interface WorkflowExplanation {
    overview: string;
    trigger_summary: string;
    steps: StepExplanation[];
    required_integrations: string[];
    potential_risks: string[];
}

interface ClarificationQuestion {
    parameter: string;
    target: string;
    prompt: string;
    description?: string;
}

interface GenerationResult {
    success: boolean;
    status: string;
    workflow?: {
        schema_version: string;
        name: string;
        description?: string;
        required_integrations: string[];
        trigger: {
            type: string;
            provider?: string;
            event?: string;
        };
        steps: WorkflowStep[];
    };
    clarifications?: ClarificationQuestion[];
    explanation?: WorkflowExplanation;
    errors?: string[];
    sanitized_prompt?: string;
    model_metadata?: Record<string, any>;
}

export const WorkflowGenerationPage: React.FC = () => {
    const { isAuthenticated } = useAuth();
    const [prompt, setPrompt] = useState('');
    const [saveDraft, setSaveDraft] = useState(false);
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<GenerationResult | null>(null);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    const [, setGenerationId] = useState<string | null>(null);
    const [workflowId, setWorkflowId] = useState<string | null>(null);

    const handleGenerate = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!prompt.trim()) return;

        setLoading(true);
        setErrorMsg(null);
        setResult(null);
        setGenerationId(null);
        setWorkflowId(null);

        try {
            const res = await apiClient.post('/workflows/generate', {
                prompt: prompt.trim(),
                save_draft: saveDraft,
            });
            setGenerationId(res.data.generation_id);
            setWorkflowId(res.data.workflow_id);
            setResult(res.data.result);
        } catch (err: any) {
            setErrorMsg(err.response?.data?.detail || err.message || 'Generation failed');
        } finally {
            setLoading(false);
        }
    };

    if (!isAuthenticated) {
        return (
            <div style={{ textAlign: 'center', padding: '3rem' }}>
                <h2>Authentication Required</h2>
                <p>Please log in to design workflows with AI.</p>
            </div>
        );
    }

    return (
        <div style={{ maxWidth: '900px', margin: '0 auto' }}>
            <div style={{ marginBottom: '1.5rem' }}>
                <h1 style={{ fontSize: '1.75rem', margin: '0 0 0.5rem 0' }}>AI Workflow Designer</h1>
                <p style={{ color: '#475569', margin: 0 }}>
                    Convert natural language descriptions into bounded, schema-validated, and reviewable workflow definitions.
                </p>
            </div>

            <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem', marginBottom: '2rem' }}>
                <form onSubmit={handleGenerate}>
                    <label style={{ display: 'block', fontWeight: 600, marginBottom: '0.5rem', color: '#0f172a' }}>
                        Describe your desired automation:
                    </label>
                    <textarea
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        placeholder="e.g. When a new issue is opened on GitHub, create a follow-up task and alert the engineering team."
                        rows={4}
                        style={{
                            width: '100%',
                            boxSizing: 'border-box',
                            padding: '0.75rem',
                            borderRadius: '6px',
                            border: '1px solid #cbd5e1',
                            fontFamily: 'inherit',
                            fontSize: '0.95rem',
                            marginBottom: '1rem',
                        }}
                    />

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.875rem', color: '#475569' }}>
                            <input
                                type="checkbox"
                                checked={saveDraft}
                                onChange={(e) => setSaveDraft(e.target.checked)}
                            />
                            Automatically save valid workflow as draft
                        </label>

                        <button
                            type="submit"
                            disabled={loading || !prompt.trim()}
                            style={{
                                padding: '0.6rem 1.25rem',
                                backgroundColor: loading ? '#93c5fd' : '#2563eb',
                                color: '#fff',
                                border: 'none',
                                borderRadius: '6px',
                                fontWeight: 600,
                                cursor: loading ? 'not-allowed' : 'pointer',
                            }}
                        >
                            {loading ? 'Analyzing & Generating...' : 'Generate Workflow'}
                        </button>
                    </div>
                </form>
            </div>

            {errorMsg && (
                <div style={{ padding: '1rem', backgroundColor: '#fef2f2', border: '1px solid #fecaca', borderRadius: '6px', color: '#991b1b', marginBottom: '1.5rem' }}>
                    <strong>Error:</strong> {errorMsg}
                </div>
            )}

            {result && (
                <div style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1.5rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                        <h2 style={{ fontSize: '1.25rem', margin: 0 }}>Generation Result</h2>
                        <span
                            style={{
                                padding: '0.25rem 0.75rem',
                                borderRadius: '9999px',
                                fontSize: '0.75rem',
                                fontWeight: 700,
                                textTransform: 'uppercase',
                                backgroundColor:
                                    result.status === 'complete' ? '#dcfce7' :
                                        result.status === 'needs_clarification' ? '#fef9c3' : '#fee2e2',
                                color:
                                    result.status === 'complete' ? '#166534' :
                                        result.status === 'needs_clarification' ? '#854d0e' : '#991b1b',
                            }}
                        >
                            {result.status.replace('_', ' ')}
                        </span>
                    </div>

                    {/* Clarification questions */}
                    {result.clarifications && result.clarifications.length > 0 && (
                        <div style={{ backgroundColor: '#fffbeb', border: '1px solid #fde68a', borderRadius: '6px', padding: '1rem', marginBottom: '1.5rem' }}>
                            <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1rem', color: '#92400e' }}>
                                Missing Required Information
                            </h3>
                            <p style={{ margin: '0 0 0.75rem 0', fontSize: '0.875rem', color: '#78350f' }}>
                                The AI cannot invent parameters or identifiers. Please refine your prompt with:
                            </p>
                            <ul style={{ margin: 0, paddingLeft: '1.25rem', color: '#78350f', fontSize: '0.875rem' }}>
                                {result.clarifications.map((c, idx) => (
                                    <li key={idx} style={{ marginBottom: '0.25rem' }}>
                                        <strong>{c.parameter}</strong>: {c.prompt}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}

                    {/* Errors or validation issues */}
                    {result.errors && result.errors.length > 0 && (
                        <div style={{ backgroundColor: '#fef2f2', border: '1px solid #fecaca', borderRadius: '6px', padding: '1rem', marginBottom: '1.5rem' }}>
                            <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1rem', color: '#991b1b' }}>Validation & Policy Issues</h3>
                            <ul style={{ margin: 0, paddingLeft: '1.25rem', color: '#991b1b', fontSize: '0.875rem' }}>
                                {result.errors.map((e, idx) => (
                                    <li key={idx}>{e}</li>
                                ))}
                            </ul>
                        </div>
                    )}

                    {/* Explanation */}
                    {result.explanation && (
                        <div style={{ backgroundColor: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '6px', padding: '1.25rem', marginBottom: '1.5rem' }}>
                            <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.1rem', color: '#0f172a' }}>Human-Readable Explanation</h3>
                            <p style={{ margin: '0 0 1rem 0', color: '#334155', fontSize: '0.9rem' }}>
                                {result.explanation.overview}
                            </p>

                            <div style={{ marginBottom: '1rem' }}>
                                <span style={{ fontWeight: 600, fontSize: '0.85rem', color: '#64748b' }}>TRIGGER: </span>
                                <span style={{ fontSize: '0.875rem', color: '#1e293b' }}>{result.explanation.trigger_summary}</span>
                            </div>

                            <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.95rem', color: '#1e293b' }}>Workflow Steps & Permissions:</h4>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                {result.explanation.steps.map((s, idx) => (
                                    <div key={idx} style={{ backgroundColor: '#fff', border: '1px solid #e2e8f0', borderRadius: '6px', padding: '0.75rem' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                                            <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{s.step_name} ({s.step_id})</span>
                                            <span style={{ fontSize: '0.75rem', padding: '0.15rem 0.5rem', borderRadius: '4px', backgroundColor: s.risk_level === 'high' ? '#fee2e2' : '#f1f5f9', color: s.risk_level === 'high' ? '#991b1b' : '#475569', fontWeight: 600 }}>
                                                {s.risk_level.toUpperCase()} RISK
                                            </span>
                                        </div>
                                        <div style={{ fontSize: '0.85rem', color: '#475569', marginBottom: '0.25rem' }}>{s.action_summary}</div>
                                        <div style={{ fontSize: '0.75rem', color: '#64748b' }}>
                                            Tool: <code>{s.tool_id}</code> | Required: {s.permissions_required.join(', ') || 'Standard read'}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Structured JSON Definition preview */}
                    {result.workflow && (
                        <div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem', flexWrap: 'wrap', gap: '0.5rem' }}>
                                <h3 style={{ margin: 0, fontSize: '1rem', color: '#0f172a' }}>
                                    Schema Definition (v{result.workflow.schema_version})
                                </h3>
                                {workflowId && (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                                        <span style={{ fontSize: '0.8rem', color: '#16a34a', fontWeight: 600 }}>
                                            Saved to Workspace Drafts
                                        </span>
                                        <Link
                                            to={`/workflows/${workflowId}`}
                                            style={{
                                                padding: '0.35rem 0.75rem',
                                                backgroundColor: '#2563eb',
                                                color: '#fff',
                                                textDecoration: 'none',
                                                borderRadius: '4px',
                                                fontSize: '0.8rem',
                                                fontWeight: 600,
                                            }}
                                        >
                                            Open in Workflow Editor &rarr;
                                        </Link>
                                    </div>
                                )}
                            </div>
                            <pre style={{ backgroundColor: '#0f172a', color: '#f8fafc', padding: '1rem', borderRadius: '6px', overflowX: 'auto', fontSize: '0.85rem' }}>
                                {JSON.stringify(result.workflow, null, 2)}
                            </pre>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
