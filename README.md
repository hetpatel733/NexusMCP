# NexusMCP

NexusMCP is an AI‑powered, self‑hosted workflow automation platform.

## Project Structure (Phase 1)

```
NexusMCP/
├─ backend/          # FastAPI backend
│  ├─ app/
│  │  ├─ __init__.py
│  │  ├─ main.py
│  │  ├─ config.py
│  │  ├─ logger.py
│  │  ├─ db.py
│  │  ├─ api/
│  │  │  └─ v1/
│  │  │     └─ health.py
│  └─ requirements.txt
├─ frontend/         # React + Vite frontend
│  ├─ src/
│  │  ├─ main.tsx
│  │  └─ App.tsx
│  ├─ index.html
│  ├─ vite.config.ts
│  └─ package.json
├─ deployment/       # Dockerfiles and Compose
│  ├─ Dockerfile.backend
│  ├─ Dockerfile.frontend
│  └─ docker-compose.yml
├─ docs/             # Documentation
│  └─ SETUP.md
├─ .env.example
└─ .gitignore
```

The repository contains a minimal, runnable stack. See `docs/SETUP.md` for instructions.
