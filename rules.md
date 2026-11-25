# nexusMCP --- AI Coder Rules

These rules are mandatory when implementing or modifying nexusMCP.
Follow the existing repository structure and decisions unless the
project owner explicitly changes them.

## 1. Product understanding

-   nexusMCP is an **AI-powered, self-hosted workflow automation
    platform**.
-   Users connect the applications they need, describe an automation in
    natural language, review the generated workflow, and enable it.
-   Once enabled, a workflow must run automatically in the background
    whenever its trigger occurs. The user must not keep the browser
    open.
-   The AI designs workflows; deterministic backend services validate,
    activate, schedule, execute, monitor, and recover them.
-   Build for open-source use and Docker-based self-hosting by
    organizations.

## 2. Agreed technology direction

Use these technologies unless explicitly instructed otherwise:

-   **Frontend:** React + Vite.
-   **Backend:** Python + FastAPI.
-   **AI workflow generation:** LangChain / LangGraph.
-   **Application database:** MongoDB.
-   **OAuth and app integrations:** Composio.
-   **Tool interoperability:** MCP, alongside Composio.
-   **Durable workflow orchestration:** Temporal.
-   **Deployment:** Docker and Docker Compose.
-   **Temporal persistence:** A database supported by Temporal (use
    PostgreSQL in the planned Compose setup); do not assume MongoDB is
    Temporal's persistence store.

Do not add Celery or another workflow engine alongside Temporal without
explicit approval. Do not add Redis, Kafka, RabbitMQ, or other
infrastructure without a concrete requirement and documented reason.

## 3. Architecture boundaries

Keep the code modular. Separate these responsibilities:

-   **API/control plane:** authentication, authorization, integrations,
    workflow CRUD, validation, enable/disable, and user-facing status.
-   **AI design-time layer:** understand prompts, discover permitted
    tools, generate structured workflow definitions, and explain the
    plan.
-   **Integration adapters:** isolate Composio-specific and MCP-specific
    code behind clear interfaces.
-   **Trigger layer:** receive webhooks, run controlled polling, and
    manage scheduled triggers.
-   **Execution layer:** Temporal workflows and Python activity workers
    perform background execution.
-   **Persistence layer:** MongoDB stores application data; Temporal
    persistence stores Temporal's internal execution history and state.
-   **Observability layer:** execution summaries, step outcomes, logs,
    metrics, and health information.

Do not mix provider SDK calls, LLM generation, HTTP route logic, and
workflow execution in one file or module.

## 4. Workflow generation rules

-   Define and maintain a strict, versioned workflow schema using
    Pydantic and/or JSON Schema.
-   A workflow must explicitly represent its trigger, steps,
    dependencies, conditions, data mappings, required integrations, and
    error policy.
-   Use structured model output. Validate every generated workflow on
    the backend before saving or enabling it.
-   The AI may reference only tools available in the server-side tool
    registry and permitted for the current user/workspace.
-   Ask the user for missing required information. Never invent
    channels, project IDs, spreadsheet IDs, recipients, or other
    resource identifiers.
-   Generate a human-readable explanation of what the workflow will do
    and which permissions it needs.
-   Treat emails, documents, issue descriptions, web pages, tool
    results, and other external content as **untrusted data**.
-   Never allow prompt content or tool output to override system policy
    or grant additional permissions.
-   Never execute arbitrary Python, JavaScript, shell commands, or
    model-generated code as a workflow step.
-   The LLM proposes a workflow; backend validation and explicit user
    approval control activation.

## 5. Integration and MCP rules

-   Use Composio for supported app connections and actions; do not
    implement each provider's OAuth flow independently unless explicitly
    required.
-   Keep Composio-specific code inside its adapter. Do not spread
    Composio SDK calls throughout the application.
-   Store Composio connection references and necessary metadata in
    MongoDB. Do not store plaintext OAuth access or refresh tokens when
    credentials are managed by Composio.
-   Do not assume every application or action supports webhooks,
    polling, or every requested operation. Verify actual provider
    capability before exposing it.
-   MCP is a tool interoperability interface; **MCP alone does not make
    an integration event-driven**.
-   Keep MCP client code separate from Composio code, but expose both
    through a common, validated tool registry.
-   MCP server registration must be administrator-controlled in the
    initial release. Do not let arbitrary users submit unrestricted
    server URLs.
-   Validate tool input against its declared schema before execution.
-   Apply least-privilege access and deny-by-default behavior,
    especially for destructive or externally visible actions.
-   Document provider-specific limitations, scopes, rate limits, OAuth
    requirements, and external service dependencies.

## 6. Always-on trigger rules

Support three trigger categories:

1.  **Webhooks:** preferred when the provider supports them.
2.  **Polling:** fallback when webhooks are unavailable.
3.  **Schedules:** time-based execution.

For webhooks:

-   Verify provider signatures and timestamps when supported.
-   Validate request size and event type.
-   Protect against replay and duplicate delivery.
-   Durably record or hand off an accepted event before acknowledging
    it.
-   Never rely on in-memory state as the only copy of an accepted event.

For polling:

-   Use centralized, controlled scheduling and provider-aware rate
    limits.
-   Do not create an uncontrolled infinite polling loop for every
    workflow.
-   Store cursors/checkpoints so polling can resume safely after
    restart.
-   Make polling intervals configurable within provider limits.

For schedules:

-   Handle time zones and daylight-saving transitions.
-   Persist schedule state through the durable orchestration system.

Every trigger must map to the correct workspace, connection, workflow,
and active workflow version. Disabling a workflow must stop future
trigger delivery.

## 7. Execution and reliability rules

-   Use Temporal for durable background orchestration.
-   Put network calls and side effects in Temporal activities, not
    directly in deterministic workflow code.
-   Keep Temporal workflow code deterministic.
-   Configure activity timeouts, bounded retries, exponential backoff,
    and cancellation behavior.
-   Pin each execution to the immutable workflow version it started
    with.
-   Design for **at-least-once delivery**, not a false promise of
    exactly-once execution.
-   Deduplicate incoming events and use idempotency keys for
    side-effecting actions wherever supported.
-   Record step attempts and outcomes so retries do not blindly repeat
    completed work.
-   Define what pause, disable, cancellation, and deletion mean for
    in-flight executions.
-   Limit concurrency per workspace, workflow, and provider connection.
-   Handle provider rate limits, transient failures, expired
    connections, and permanent errors explicitly.
-   A worker or API restart must not silently lose active workflow state
    or accepted events.
-   Never claim an action succeeded until the provider/tool reports
    success and the result is recorded.

## 8. Workflow lifecycle and versioning

Use explicit lifecycle states such as:

-   `draft`
-   `ready`
-   `active`
-   `paused`
-   `needs_attention`
-   `archived`

Rules:

-   Enabling a workflow must be an idempotent backend operation.
-   Before activation, validate the workflow, required connections,
    permissions, trigger support, and tool schemas.
-   Handle partial activation safely. If trigger registration fails,
    clean up partial registrations where possible and report the
    workflow as needing attention.
-   Do not mutate an active workflow definition in place.
-   Editing an active workflow creates a new immutable version.
-   Existing executions continue using the version they started with.
-   New triggers use the newly activated version only after a controlled
    version switch.
-   Keep an audit trail for enable, disable, pause, resume, edit, and
    delete operations.

## 9. Database and data ownership rules

MongoDB is the application database. Use it for:

-   Users, workspaces, and memberships.
-   Integration connection references and status.
-   Workflow metadata and immutable workflow versions.
-   Trigger registration metadata and polling checkpoints.
-   Execution summaries and redacted step logs.
-   Audit events and user settings.

Temporal's persistence database is owned and managed by Temporal. Do not
write to or modify Temporal's internal tables directly. Do not duplicate
its complete execution history into MongoDB.

-   Include `workspace_id` on organization-owned records from the
    beginning.
-   Enforce workspace scoping in backend queries and worker execution
    paths, not only in the frontend.
-   Add indexes for common lookups and uniqueness constraints.
-   Use migrations or explicit schema-version handling for data-model
    changes.
-   Define retention policies for execution logs and payloads.
-   Keep large payloads out of workflow definitions and Temporal inputs;
    store them separately and pass references where appropriate.

## 10. Security rules

-   Enforce authentication and authorization on every protected API and
    background operation.
-   Never trust workspace IDs, user IDs, connection IDs, or resource IDs
    supplied by the client without authorization checks.
-   Never commit secrets, API keys, tokens, passwords, private keys, or
    real `.env` files.
-   Provide `.env.example` with placeholder values only.
-   Redact credentials, authorization headers, tokens, and sensitive
    personal data from logs and errors.
-   Use HTTPS in production, secure cookie settings where applicable,
    strict CORS allowlists, and CSRF protection where applicable.
-   Validate all external input and enforce payload size limits.
-   Protect webhook endpoints against forgery and replay.
-   Restrict outbound network access for MCP servers and other
    user-configurable endpoints to mitigate SSRF.
-   Apply rate limits, execution quotas, concurrency limits, and a
    deployment-level kill switch.
-   Require additional safeguards or human approval for destructive,
    permission-changing, financial, or externally sensitive actions.
-   Do not expose raw provider errors or internal stack traces to end
    users.

## 11. API and code quality rules

-   Use clear, consistent naming and small, single-responsibility
    modules.
-   Use Pydantic models for request, response, and configuration
    validation.
-   Keep business logic out of FastAPI route handlers.
-   Use dependency injection for database access, authentication, and
    service interfaces.
-   Make API operations idempotent where appropriate.
-   Return consistent error responses with useful, non-sensitive
    messages.
-   Add type hints to Python code.
-   Use asynchronous I/O appropriately; do not block the FastAPI event
    loop with synchronous network or database calls.
-   Use structured logging and correlation IDs across API, trigger
    ingress, Temporal, and workers.
-   Avoid unnecessary abstractions, duplicate code, premature
    microservices, and unrequested features.
-   Follow the existing project conventions before introducing a new
    pattern.

## 12. Frontend rules

-   The frontend is a control and monitoring interface, not the
    execution runtime.
-   Never run persistent workflows in the browser.
-   Show clear workflow states: draft, ready, active, paused, needs
    attention, and archived.
-   Before activation, show the trigger, actions, required connections,
    permissions, and expected behavior.
-   Provide execution history with timestamps, step status, retry
    attempts, and understandable errors.
-   Make enable, pause, resume, and disable actions reflect the
    backend's confirmed state.
-   Handle loading, empty, error, and reconnect states.
-   Do not expose provider secrets or internal credentials in frontend
    code or browser storage.
-   Keep API calls in a reusable client/service layer.

## 13. Docker and self-hosting rules

-   The complete supported deployment must be runnable through
    documented Docker Compose instructions.
-   Persist MongoDB and Temporal database data using named volumes or
    clearly documented bind mounts.
-   Add health checks, restart policies, sensible resource
    configuration, and service dependency handling.
-   Do not assume container startup order means a dependency is ready;
    use health/readiness checks and retry-safe startup.
-   Run containers as non-root where practical.
-   Separate development and production configuration.
-   Document every required environment variable and external service
    dependency.
-   Provide backup, restore, upgrade, and rollback instructions.
-   Back up MongoDB and Temporal persistence independently and test a
    coordinated recovery procedure.
-   Never imply that a local MongoDB backup alone can restore active
    Temporal execution state.
-   Make it clear which data stays local and which data is sent to
    Composio, model providers, or connected applications.

## 14. Testing requirements

For every meaningful feature, add tests at the appropriate level:

-   Unit tests for schema validation, permissions, state transitions,
    mappings, and deduplication.
-   Adapter contract tests for Composio and MCP.
-   Integration tests for MongoDB, Temporal, webhook ingress, and
    workers.
-   End-to-end tests for prompt → workflow draft → approval → enable →
    trigger → execution → history.
-   Resilience tests for API/worker restarts, provider failures,
    timeouts, duplicate events, and rate limits.
-   Security tests for tenant isolation, webhook validation, prompt
    injection, secret leakage, and MCP endpoint restrictions.

Critical acceptance test:

1.  Create and enable a workflow.
2.  Close the browser.
3.  Send a real or test trigger event.
4.  Confirm the workflow executes and records its result.
5.  Restart a worker during a separate test execution and verify
    recovery/retry behavior.
6.  Disable the workflow.
7.  Confirm new events no longer start executions.

Do not mark a feature complete because it works only in a happy-path
local demo.

## 15. How the AI coder must work

Before changing code:

1.  Inspect the repository structure and relevant existing files.
2.  Read the existing implementation and follow established conventions.
3.  Identify the smallest set of files and changes needed.
4.  Check whether the required dependency or capability already exists.
5.  If a requirement is ambiguous or conflicts with these rules, ask
    before making a major architectural change.

While implementing:

-   Make incremental, focused changes.
-   Do not rewrite working modules without a clear reason.
-   Do not silently change architecture, database ownership, API
    contracts, or workflow schema.
-   Do not add dependencies or infrastructure without explaining why
    they are needed.
-   Do not use mock data as if it were a working integration.
-   Do not leave placeholder functions, fake success responses, or
    TODO-only implementations in a feature described as complete.
-   Keep secrets and personal data out of test fixtures.
-   Add or update tests and documentation with the implementation.

Before finishing:

1.  Run relevant tests, linting, and type checks available in the
    repository.
2.  Report exactly what changed.
3.  List commands/tests run and whether they passed.
4.  Mention configuration or migration steps required.
5.  Clearly disclose anything incomplete, mocked, untested, or dependent
    on external provider setup.

**Never claim a feature is production-ready unless its failure paths,
security boundaries, persistence behavior, and tests have been
addressed.**

------------------------------------------------------------------------

## Guiding principle

**Build a reliable automation platform, not just an AI demo.**

Prioritize correctness, security, durable execution, observability, and
a clean self-hosted experience over adding more integrations or flashy
features.
