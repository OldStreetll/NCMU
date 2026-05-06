# Backup / Restore Runbook (Phase 2A H1)

> Audience: NCMU operators running `scripts/backup.sh`, `scripts/restore.sh`,
> and `scripts/docker-compose-safe.sh`.
> Status: active (Phase 2A H1, 2026-05-06).
> Source-of-truth: `scripts/backup.sh`, `scripts/restore.sh`,
> `scripts/docker-compose-safe.sh` — diverge from those, re-read those.

## 1. Overview & dump scope

NCMU runs four stateful databases across three product-layer services
(Dify, FastGPT, NCMU backend). A complete environment snapshot covers
all four; partial backups are not supported on purpose — a Phase 1 [KB]
App lives across Dify-pg + FastGPT-mongo + FastGPT-pg simultaneously,
and a half snapshot is a debugging trap.

| # | Container             | Engine     | Dumped artifact            | Carries                                     |
|---|-----------------------|------------|----------------------------|---------------------------------------------|
| 1 | `ncmu-pg-dify`        | postgres   | `dify-pg.sql.gz`           | Dify apps / DSL / conversations / API keys  |
| 2 | `ncmu-fastgpt-mongo`  | mongo      | `fastgpt-mongo.archive.gz` | FastGPT datasets / collections / chat metadata |
| 3 | `ncmu-pg-fastgpt`     | pgvector   | `fastgpt-pg.sql.gz`        | FastGPT vector store                        |
| 4 | `ncmu-pg-ncmu`        | postgres   | `ncmu-pg.sql.gz`           | NCMU sessions / messages / users / RBAC     |

Every snapshot lands under `data/backups/<YYYYMMDD-HHMMSS>/` together
with a `manifest.json` containing per-file sha256 + byte size.

Quick check:

```bash
ls -lh data/backups/ | tail -20
```

## 2. Taking a backup — `scripts/backup.sh`

### 2.1 Usage

```bash
./scripts/backup.sh
```

No arguments. The timestamp directory name is generated from `date +%Y%m%d-%H%M%S`
at start time. All four containers must be running.

### 2.2 Expected STDOUT

```
Backup snapshot target: ./data/backups/20260506-120000
  -> dump ncmu-pg-dify (-U postgres dify) -> dify-pg.sql.gz
  -> dump ncmu-fastgpt-mongo (-u root --db fastgpt) -> fastgpt-mongo.archive.gz
  -> dump ncmu-pg-fastgpt (-U postgres postgres) -> fastgpt-pg.sql.gz
  -> dump ncmu-pg-ncmu (-U ncmu ncmu) -> ncmu-pg.sql.gz

Backup created: ./data/backups/20260506-120000
  dify-pg.sql.gz                       412345 bytes
  fastgpt-mongo.archive.gz             891234 bytes
  fastgpt-pg.sql.gz                    234567 bytes
  ncmu-pg.sql.gz                        56789 bytes

Total duration: 14s (retention=10, pruned this run=0)
```

### 2.3 manifest.json schema

```json
{
  "timestamp": "20260506-120000",
  "created_at": "2026-05-06T12:00:14+08:00",
  "files": [
    {"path": "dify-pg.sql.gz",           "size_bytes": 412345, "sha256": "9a3f..."},
    {"path": "fastgpt-mongo.archive.gz", "size_bytes": 891234, "sha256": "c7b1..."},
    {"path": "fastgpt-pg.sql.gz",        "size_bytes": 234567, "sha256": "e2d9..."},
    {"path": "ncmu-pg.sql.gz",           "size_bytes":  56789, "sha256": "1f8a..."}
  ]
}
```

Spot-verify a single file's sha256 manually:

```bash
sha256sum data/backups/20260506-120000/dify-pg.sql.gz
```

The first column must match the corresponding `sha256` field in
`manifest.json`. `restore.sh` does this for all four files automatically
(see §3.4).

### 2.4 Credentials handling

`backup.sh` reads `POSTGRES_USER` / `POSTGRES_DB` /
`MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` from each
container's environment via `docker exec ... printenv`. There is no
copy in `.env` for the operator to keep in sync — credentials follow
whatever `docker-compose.yml` actually injected at container start.

This avoids drift between `docker-compose.yml` and `.env` that would
otherwise silently break dumps when one side is bumped.

## 3. Restoring from a snapshot — `scripts/restore.sh`

### 3.1 Usage

```bash
./scripts/restore.sh 20260506-120000
```

Argument is the snapshot directory name (no path prefix). Missing
argument → `Usage: restore.sh <timestamp>` and exit 1.

### 3.2 Pre-restore checklist

1. All four target containers must be running and accept connections —
   restore writes through `docker exec`, it does not start containers.
2. `data/backups/<ts>/manifest.json` must exist and be parseable.
3. The four dump files referenced in `manifest.json` must exist beside it.

If any of those fail, the script exits early with `Snapshot not found: ...`
on STDERR and does not touch any database.

### 3.3 Confirmation prompt

After sha256 passes, the script writes the destructive warning to STDERR
and reads from stdin:

```
⚠️  即将从 data/backups/20260506-120000 恢复 4 个数据库，将覆盖所有现有数据：
    - ncmu-pg-dify        <- dify-pg.sql.gz
    - ncmu-fastgpt-mongo  <- fastgpt-mongo.archive.gz
    - ncmu-pg-fastgpt     <- fastgpt-pg.sql.gz
    - ncmu-pg-ncmu        <- ncmu-pg.sql.gz

输入 yes 确认恢复（覆盖当前数据）:
```

Match is strict: only the literal three-letter string `yes` proceeds.
Anything else (including `Y`, `YES`, `y`, blank line, EOF) → STDERR
`Cancelled` + exit 1, no databases written.

### 3.4 sha256 failure mode

If any file's sha256 does not match the manifest, restore aborts before
touching any database:

```
Verifying sha256 against manifest.json...
ERROR: sha256 verification failed:
  - dify-pg.sql.gz (sha256 mismatch: expected=9a3f... actual=8b2e...)
exit 1
```

Recovery: pick a different snapshot, or re-take a backup if the source
data is still authoritative.

```bash
# inspect which snapshot directories are candidates
ls data/backups/ | grep -E '^[0-9]{8}-[0-9]{6}$'
./scripts/restore.sh 20260505-093000
```

A missing dump file produces the same failure path with
`<file> (missing)` in place of the mismatch line.

### 3.5 Successful restore output

```
Verifying sha256 against manifest.json...
  -> sha256 OK (4 files)

⚠️  即将从 data/backups/20260506-120000 恢复 4 个数据库，将覆盖所有现有数据：
    ...
输入 yes 确认恢复（覆盖当前数据）: yes

Starting restore from data/backups/20260506-120000 (4 databases serial)...
  -> restore ncmu-pg-dify <- dify-pg.sql.gz
  -> restore ncmu-fastgpt-mongo <- fastgpt-mongo.archive.gz
  -> restore ncmu-pg-fastgpt <- fastgpt-pg.sql.gz
  -> restore ncmu-pg-ncmu <- ncmu-pg.sql.gz

Restore complete from 20260506-120000
Total duration: 22s

请重启相关容器: docker compose restart ncmu-backend ncmu-pg-dify ncmu-fastgpt-mongo ncmu-pg-fastgpt ncmu-pg-ncmu
```

The trailing `docker compose restart` line is **a recommendation, not
an automatic action** — the operator is expected to run it. Some
clients (Dify worker, FastGPT app, NCMU backend) cache schema / connection
state and do not pick up restored rows until restarted.

```bash
# Run after a successful restore returns:
docker compose restart ncmu-backend ncmu-pg-dify ncmu-fastgpt-mongo ncmu-pg-fastgpt ncmu-pg-ncmu
```

## 4. Destructive-op wrapper — `scripts/docker-compose-safe.sh`

`docker compose down -v` permanently destroys all named volumes — the
only mechanism in this stack that wipes user data without a separate
confirmation. The wrapper enforces a long-phrase confirmation and an
audit log on the destructive variant only; everything else passes
through.

### 4.1 Recommended alias

Set this in your shell rc file so the wrapper sits in front of every
`docker compose` invocation in this repo:

```bash
alias dc='./scripts/docker-compose-safe.sh'
```

Then:

```bash
dc ps             # passes through to: docker compose ps
dc up -d          # passes through to: docker compose up -d
dc down           # passes through to: docker compose down (volumes safe)
dc down -v        # intercepted — see §4.2
dc down --volumes # also intercepted (long form)
```

### 4.2 Destructive intercept transcript

```
$ dc down -v
⚠️  即将执行 docker compose down -v，会删除所有 named volume 数据

当前 volume 列表：
ncmu_pg-ncmu          local
ncmu_pg-dify          local
ncmu_pg-fastgpt       local
ncmu_fastgpt-mongo    local

如确定，输入完整字符串 yes-i-want-to-destroy-all-volumes 继续：
yes-i-want-to-destroy-all-volumes
[ docker compose down -v output... ]
```

Anything other than the literal phrase
`yes-i-want-to-destroy-all-volumes` → STDERR `Cancelled` + exit 1,
volumes untouched.

### 4.3 Audit log

`data/backups/destructive.log` records every intercepted invocation.
Format (one line per attempt):

```
2026-05-06 12:34:56 user=lyhwslubuntu cmd="down -v" result=CONFIRMED
2026-05-06 13:01:09 user=lyhwslubuntu cmd="down -v" result=CANCELLED
```

Tail it after an incident:

```bash
tail -20 data/backups/destructive.log
```

The wrapper auto-creates `data/backups/` on first write — the audit
log works even when `backup.sh` has never run.

## 5. Retention policy

`backup.sh` keeps the **most recent 10** snapshots and prunes older
ones at the end of every successful run.

- Constant: `RETENTION_COUNT=10` in `scripts/backup.sh:23`.
- Match pattern: only directories matching `^[0-9]{8}-[0-9]{6}$` are
  considered for pruning. Operator-named directories (e.g.
  `pre-bump-test`) are left alone.
- A failed `backup.sh` run does **not** prune — it cleans only its own
  half-written snapshot via the ERR trap, so a corrupted run can never
  push a known-good snapshot off the retention window.

Inspect what's currently on disk:

```bash
ls -1 data/backups/ | grep -E '^[0-9]{8}-[0-9]{6}$' | sort
```

Manual pruning (e.g. before reclaiming disk):

```bash
# Drop everything older than a date — DESTRUCTIVE, double-check first.
find data/backups/ -maxdepth 1 -type d \
    -regextype posix-extended -regex '.*/[0-9]{8}-[0-9]{6}' \
    -name '20260401-*' -exec rm -rf {} +
```

## 6. Failure handling

### 6.1 backup.sh half-snapshot cleanup

If any of the four dumps fails, the ERR trap (`scripts/backup.sh:35-43`)
removes the partially-written snapshot directory and exits non-zero.
You will see on STDERR:

```
ERROR: backup aborted (exit N); cleaning partial snapshot ./data/backups/<ts>
```

No further action is required — the half snapshot is gone, retention
window is untouched, and the next successful run starts fresh.

If the trap itself is interrupted (kill -9, host crash mid-run), the
half directory may remain. To clean up manually:

```bash
ls -1 data/backups/ | grep -E '^[0-9]{8}-[0-9]{6}$' | while read ts; do
    [[ -f "data/backups/$ts/manifest.json" ]] || \
        echo "INCOMPLETE: data/backups/$ts (no manifest.json)"
done
# review the list, then:
rm -rf data/backups/<incomplete-ts>
```

### 6.2 restore.sh partial-failure transcript

`restore.sh` runs the four restores serially in this order:
**dify-pg → fastgpt-mongo → fastgpt-pg → ncmu-pg**. If a later restore
fails, the earlier ones have already overwritten their target databases
and cannot be undone in-place.

The script tracks already-restored containers in `RESTORED[]` and on
failure prints a partial-failure block to STDERR:

```
  -> restore ncmu-pg-dify <- dify-pg.sql.gz
  -> restore ncmu-fastgpt-mongo <- fastgpt-mongo.archive.gz
  -> restore ncmu-pg-fastgpt <- fastgpt-pg.sql.gz

ERROR: restore failed at ncmu-pg-fastgpt
  Restored (data already overwritten):
    - ncmu-pg-dify
    - ncmu-fastgpt-mongo
  Failed: ncmu-pg-fastgpt

  建议：检查 docker logs ncmu-pg-fastgpt 后从更早 snapshot 重试整体 restore
```

Recovery procedure when this fires:

1. **Do not retry only the failed DB.** The product layer expects the
   four databases to come from the same snapshot timestamp; mixing a
   restored snapshot with a half-restored one breaks Phase 1 [KB] App
   referential integrity.

2. Diagnose first:

   ```bash
   docker logs ncmu-pg-fastgpt --tail 100
   ```

   Common causes: container restarted mid-restore, disk full, schema
   incompatibility (restoring across an alembic upgrade boundary).

3. Re-run `restore.sh` against the same or an earlier snapshot once the
   underlying issue is fixed:

   ```bash
   ./scripts/restore.sh 20260505-093000
   ```

   The restore is idempotent at the database level (`--drop` for mongo,
   `pg_dump` writes `CREATE` after `DROP`) so re-running it on the
   already-restored containers is safe.

### 6.3 Container not running

`docker exec` against a stopped container exits non-zero immediately;
both `backup.sh` and `restore.sh` propagate that and abort. Bring the
container up first:

```bash
docker compose up -d ncmu-pg-dify ncmu-fastgpt-mongo ncmu-pg-fastgpt ncmu-pg-ncmu
```

## 7. Production upgrade path (placeholder)

The current implementation is **dev-aware**: snapshots live on the host
filesystem next to the repo (`data/backups/`). For production the
operator will eventually need:

- [ ] **TODO** — upload step that pushes each new snapshot directory
      (or its tarball) to S3 / Aliyun OSS / on-prem MinIO with
      server-side encryption. Suggested hook: append after the manifest
      write in `backup.sh:111`, before `trap - ERR`.
- [ ] **TODO** — retention split: keep `RETENTION_COUNT` snapshots
      locally for fast restore, but apply a longer retention (e.g. 30
      days) on the remote object store; local pruning must not touch
      remote.
- [ ] **TODO** — `restore.sh --from-remote <ts>` variant that pulls a
      snapshot back from object storage before verifying sha256. The
      sha256 check then doubles as transport-corruption detection.
- [ ] **TODO** — encrypted-at-rest for `manifest.json` if it ever grows
      to include credentials or PII (today it carries only file names,
      sizes, and hashes — safe to keep plaintext).
- [ ] **TODO** — off-host audit log shipping for
      `data/backups/destructive.log` (Loki / CloudWatch / ELK), since
      the on-host file is itself destroyed by `down -v` when the volume
      is the host directory.

These are intentionally out of scope for Phase 2A H1; the dev-friendly
local-disk implementation is the agreed baseline (see
`NCMU-Wiki/roadmap/phase2a-hardening-spec.md` "Dev-friendly +
prod-aware" section).

Inspect what the *current* (dev) implementation has on disk — useful
sanity check before drafting the prod migration:

```bash
# Today: snapshots are local-only, audit log is local-only.
du -sh data/backups/ && tail -5 data/backups/destructive.log 2>/dev/null

# Stub for the future remote-upload hook (do NOT run today — bucket
# does not exist; included to anchor what shape the TODO will take):
#   aws s3 sync data/backups/<ts>/ s3://ncmu-backups/<ts>/ --sse aws:kms
```

## 8. References

- `scripts/backup.sh` — full-dump implementation (TASK-37).
- `scripts/restore.sh` — restore + sha256 verify + partial-failure
  handler (TASK-38, REWORK-38 mongo single-decompress fix).
- `scripts/docker-compose-safe.sh` — destructive-op wrapper (TASK-39).
- `NCMU-Wiki/roadmap/phase2a-hardening-spec.md` — Phase 2A H1 / H2
  scope and rationale.
- `NCMU-Wiki/roadmap/phase2a-hardening-plan.md` — TASK-37 / 38 / 39
  acceptance criteria (source of truth for the command transcripts above).

When in doubt, re-read the scripts directly — they are the authoritative
reference, this runbook lags them:

```bash
ls -1 scripts/backup.sh scripts/restore.sh scripts/docker-compose-safe.sh
```
