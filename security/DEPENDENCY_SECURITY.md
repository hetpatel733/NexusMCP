# Dependency Security — NexusMCP Phase 10

## Pinned Dependencies

All Python dependencies are pinned to exact versions in `backend/requirements.txt`:

```
fastapi==0.116.1
uvicorn[standard]==0.30.6
motor==3.6.0
pydantic==2.9.1
pydantic-settings==2.5.2
python-dotenv==1.0.0
structlog==24.4.0
temporalio==1.4.0
composio-core==0.4.0
mcp==1.1.0
pytest==8.3.2
pytest-asyncio==0.24.0
httpx==0.27.2
ruff==0.6.8
mypy==1.11.2
pyjwt==2.9.0
bcrypt==4.2.0
email-validator==2.2.0
cryptography==42.0.0
```

Container images are pinned to specific versions in `deployment/docker-compose.yml`:

- `mongo:7.0`
- `postgres:15`
- `temporalio/auto-setup:1.24.2`
- `temporalio/ui:2.30.0`

## Vulnerability Scanning

### Python Dependency Scanning

Run `pip-audit` to check for known vulnerabilities:

```bash
# Install pip-audit
pip install pip-audit

# Scan all dependencies
pip-audit -r backend/requirements.txt

# Scan with severity output
pip-audit -r backend/requirements.txt --vuln
```

### Container Image Scanning

Container images should be scanned using a tool like Trivy:

```bash
# Scan MongoDB image
trivy image mongo:7.0

# Scan Postgres image
trivy image postgres:15

# Scan Temporal image
trivy image temporalio/auto-setup:1.24.2

# Scan built backend image
trivy image nexusmcp_backend:latest
```

## Vulnerability Response Process

1. **Detection:** Weekly scheduled `pip-audit` and Trivy scans (CI pipeline)
2. **Triage:** Security team reviews findings within 24 hours
3. **Remediation:**
   - Critical: Patch within 24 hours, release emergency update
   - High: Patch within 72 hours
   - Medium: Patch in next regular release cycle
   - Low: Document and track
4. **Verification:** Re-run scans after patching to confirm fix
5. **Disclosure:** Publish security advisories for user-facing vulnerabilities

## Supply Chain Best Practices

- Never use `*` or unpinned versions in `requirements.txt`
- Review all new dependencies before adding to the project
- Prefer well-maintained packages with active security patching
- Use `pip-audit --dry-run` in CI to fail builds on new vulnerabilities
- Keep Docker base images up-to-date with security patches

## Environment Isolation

- Development: `.env` file with placeholder values (never committed)
- Production: Secrets injected via environment variables or Docker secrets
- Never commit `.env` files containing real credentials to version control
- `.env.example` provided with placeholder values only
