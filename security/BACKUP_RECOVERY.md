# Backup & Recovery Runbook — NexusMCP Phase 10

## Overview

NexusMCP uses two persistence stores:

1. **MongoDB** — Application data (users, workspaces, workflows, executions, audit logs, connections, trigger events)
2. **PostgreSQL** — Temporal persistence (workflow execution history and state)

Both must be backed up independently and restored in a coordinated manner.

## Recovery Objectives

| Metric | Target | Description |
|--------|--------|-------------|
| **RPO** (Recovery Point Objective) | 1 hour | Maximum acceptable data loss measured in time |
| **RTO** (Recovery Time Objective) | 4 hours | Maximum acceptable downtime before service restoration |

## Backup Strategy

### MongoDB Backups

**Full backup** (daily, off-peak):
```bash
mongodump \
  --uri="mongodb://root:example@localhost:27017/nexusmcp?authSource=admin" \
  --archive=/backups/mongodb/nexusmcp-$(date +%Y%m%d-%H%M%S).archive \
  --gzip
```

**Incremental backup** (hourly via oplog):
```bash
mongodump \
  --uri="mongodb://root:example@localhost:27017/nexusmcp?authSource=admin" \
  --oplog \
  --archive=/backups/mongodb/oplog-$(date +%Y%m%d-%H%M%S).archive \
  --gzip
```

### PostgreSQL (Temporal) Backups

**Full backup** (daily):
```bash
pg_dump \
  -h localhost \
  -U temporal \
  -d temporal \
  -f /backups/postgres/temporal-$(date +%Y%m%d-%H%M%S).sql
```

**Compressed backup:**
```bash
pg_dump \
  -h localhost \
  -U temporal \
  -d temporal \
  | gzip > /backups/postgres/temporal-$(date +%Y%m%d-%H%M%S).sql.gz
```

### Docker Volume Backups

Since MongoDB and PostgreSQL data persist in named Docker volumes:

```bash
# Backup MongoDB data volume
docker run --rm \
  -v nexusmcp_mongo_data:/source \
  -v $(pwd)/backups:/backup \
  alpine tar czf /backup/mongo_data-$(date +%Y%m%d).tar.gz -C /source .

# Backup PostgreSQL data volume
docker run --rm \
  -v nexusmcp_postgres_data:/source \
  -v $(pwd)/backups:/backup \
  alpine tar czf /backup/postgres_data-$(date +%Y%m%d).tar.gz -C /source .
```

## Restore Procedure

### Prerequisites

1. Docker and Docker Compose installed
2. Backup files accessible
3. Clean environment (or target environment prepared)

### Step 1: Start Infrastructure Only

```bash
cd deployment
docker-compose up -d mongodb postgres temporal
```

Wait for health checks to pass:
```bash
docker-compose ps
```

### Step 2: Restore MongoDB

```bash
# Option A: Restore from mongodump archive
mongorestore \
  --uri="mongodb://root:example@localhost:27017/admin" \
  --archive=/backups/mongodb/nexusmcp-YYYYMMDD-HHMMSS.archive \
  --gzip \
  --drop

# Option B: Restore from volume backup
docker run --rm \
  -v nexusmcp_mongo_data:/target \
  -v $(pwd)/backups:/backup \
  alpine sh -c "rm -rf /target/* && tar xzf /backup/mongo_data-YYYYMMDD.tar.gz -C /target"
```

### Step 3: Restore PostgreSQL (Temporal)

```bash
psql -h localhost -U temporal -d temporal -f /backups/postgres/temporal-YYYYMMDD-HHMMSS.sql

# Or from compressed
gunzip -c /backups/postgres/temporal-YYYYMMDD-HHMMSS.sql.gz | \
  psql -h localhost -U temporal -d temporal
```

### Step 4: Start Application Services

```bash
docker-compose up -d backend worker
```

### Step 5: Verify Restoration

```bash
# Check backend health
curl -f http://localhost:8000/api/v1/health

# Verify Temporal connectivity
curl -f http://localhost:7233/

# Check MongoDB collections
mongosh "mongodb://root:example@localhost:27017/nexusmcp?authSource=admin" \
  --eval "db.workflows.countDocuments()"
```

## Recovery Testing

### Test Procedure

1. Deploy NexusMCP to a clean test environment
2. Create test data: users, workspaces, workflows, connections, executions
3. Trigger a real workflow execution and verify it completes
4. Take backups of MongoDB and PostgreSQL
5. Destroy the test environment
6. Restore from backups into a fresh environment
7. Verify all data is intact and workflows can execute

### Validation Checklist

- [ ] All users and workspaces restored
- [ ] All workflow definitions and versions restored
- [ ] All connection references restored
- [ ] Execution history intact
- [ ] Audit logs intact
- [ ] Temporal can resume in-flight workflows
- [ ] Webhook subscriptions still active
- [ ] Polling checkpoints preserved

## Retention Policies for Backups

| Backup Type | Retention | Frequency |
|-------------|-----------|-----------|
| MongoDB full dump | 30 days | Daily |
| MongoDB oplog | 7 days | Hourly |
| PostgreSQL full dump | 30 days | Daily |
| Docker volume snapshot | 7 days | Weekly |

## Disaster Recovery Scenarios

### Single Database Failure

If only MongoDB fails:
1. Restore MongoDB from latest backup
2. PostgreSQL (Temporal) remains intact
3. Temporal workflows can resume once MongoDB is back

### Complete Infrastructure Loss

1. Provision new infrastructure
2. Restore PostgreSQL first (Temporal state)
3. Restore MongoDB (application data)
4. Start application services
5. Verify health checks pass

### Temporal State Loss

If Temporal PostgreSQL is lost but MongoDB is intact:
1. Temporal execution history is lost
2. In-flight workflows cannot be resumed
3. MongoDB execution records show the last known state
4. Users must re-trigger affected workflows
5. This is why RPO of 1 hour is critical — at most 1 hour of execution history lost

## Monitoring Backup Health

- Backup jobs should send alerts on failure
- Weekly test restore to staging environment
- Monitor backup size trends for unexpected growth
- Verify backup integrity (restore test) monthly
