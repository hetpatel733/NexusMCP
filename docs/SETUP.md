# NexusMCP Setup & Quickstart Guide

This guide details how to launch NexusMCP Phase 1 locally and using Docker Compose.

---

## Prerequisites

- **Docker & Docker Compose** (Docker v24+ recommended)
- **Python 3.11+** (for local backend development)
- **Node.js 20+** (for local frontend development)

---

## 1. Quickstart with Docker Compose (Single Command)

To run the entire NexusMCP stack (Frontend, Backend API, Worker, MongoDB, Temporal Server, Temporal UI, and PostgreSQL):

1. **Clone the repository and copy the environment configuration:**
   ```bash
   cp .env.example .env
   ```

2. **Start the stack using Docker Compose:**
   ```bash
   docker compose -f deployment/docker-compose.yml up --build -d
   ```

3. **Verify the services:**
   - **Frontend App:** [http://localhost:3000](http://localhost:3000)
   - **FastAPI Health Endpoint:** [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)
   - **FastAPI Readiness Endpoint:** [http://localhost:8000/api/v1/ready](http://localhost:8000/api/v1/ready)
   - **FastAPI Interactive Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
   - **Temporal Web UI:** [http://localhost:8080](http://localhost:8080)

4. **Stop the stack:**
   ```bash
   docker compose -f deployment/docker-compose.yml down
   ```
   To also remove persistent volumes:
   ```bash
   docker compose -f deployment/docker-compose.yml down -v
   ```

---

## 2. Local Development Setup

### Backend (FastAPI + Worker)

1. Navigate to the `backend/` directory:
   ```bash
   cd backend
   ```

2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run linting, type checks, and tests:
   ```bash
   ruff check .
   mypy app
   pytest
   ```

5. Run the API locally:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

6. Run the Worker locally:
   ```bash
   python -m app.worker
   ```

### Frontend (React + Vite)

1. Navigate to the `frontend/` directory:
   ```bash
   cd frontend
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Run the development server:
   ```bash
   npm run dev
   ```

4. Build and type-check:
   ```bash
   npm run build
   ```
