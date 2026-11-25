# Operations & Maintenance Guide

## Independent Backups

Per `rules.md` (Section 13), **MongoDB and Temporal persistence must be backed up independently**. Never assume that a MongoDB backup alone can restore active Temporal execution state.

### 1. MongoDB Backup & Restore

#### Backup:
```bash
docker exec -t nexusmcp_mongodb mongodump \
  -u root -p example --authenticationDatabase admin \
  --db nexusmcp --archive > nexusmcp_mongo_backup.archive
```

#### Restore:
```bash
cat nexusmcp_mongo_backup.archive | docker exec -i nexusmcp_mongodb mongorestore \
  -u root -p example --authenticationDatabase admin \
  --archive --drop
```

---

### 2. Temporal PostgreSQL Backup & Restore

#### Backup:
```bash
docker exec -t nexusmcp_postgres pg_dump -U temporal temporal > temporal_pg_backup.sql
```

#### Restore:
```bash
cat temporal_pg_backup.sql | docker exec -i nexusmcp_postgres psql -U temporal -d temporal
```

---

## Upgrade and Rollback

1. Pull the new version of the codebase or image.
2. Stop the worker to allow in-flight activities to complete:
   ```bash
   docker compose -f deployment/docker-compose.yml stop worker
   ```
3. Upgrade database schemas if migrations exist.
4. Redeploy containers:
   ```bash
   docker compose -f deployment/docker-compose.yml up --build -d
   ```
5. In case of rollback, stop the stack and restore both MongoDB and PostgreSQL snapshots taken prior to upgrade.
