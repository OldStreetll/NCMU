# DingTalk Contact-Sync LIVE Runbook (2D-A2-同步 转 LIVE)

> Audience: NCMU operators turning the **already-merged** DingTalk contact
> sync (Phase 2D-A2-同步) from "tested against respx mocks" into a **real
> sync against the live DingTalk org**, the moment the DingTalk
> 部门读+成员读 permissions are published. This is a *do-this-in-order*
> checklist — copy/paste each command, read the expected output, stop on
> the documented failure signatures.
> Status: active / **blocked on DingTalk permission approval** (2026-06-03,
> base `71ed77b`). Sync code is done (merged to main, full respx-mock suite
> + 509-passed regression); the ONLY blocker is the DingTalk permission
> publish (see §2).
> Source-of-truth (re-read these if this doc drifts):
> `ncmu-backend/src/ncmu_backend/dingtalk/routes.py` (endpoints) ·
> `ncmu-backend/src/ncmu_backend/dingtalk/sync_service.py` (B-strategy +
> `SyncResult`) · `ncmu-backend/src/ncmu_backend/db/models.py`
> (`DepartmentTagMapping` / `User` / `UserTag` / `Tag`) ·
> `ncmu-backend/alembic/versions/0011_*.py` (table) ·
> `ncmu-backend/src/ncmu_backend/config.py` (DingTalk Settings).

## 1. Overview

The sync is an **admin-triggered, low-frequency backend action** — there is
no SPA UI for it (this batch is curl/HTTP-driven by design). It pulls the
**「软件开发部」department subtree** from DingTalk and, for every employee in
that subtree, **creates-or-updates** an NCMU `users` row (B-strategy,
zero-credential) and writes its `user_tags` from a manually-seeded
`department_tag_mappings` table. Everything outside that subtree is left
untouched.

Going LIVE is four steps once the permissions are published:

| # | Action | Endpoint / surface | Reads / writes |
|---|---|---|---|
| ① | Discover the dept tree → find 「软件开发部」`dept_id` | `GET /api/v1/ncmu/admin/dingtalk/departments` | reads DingTalk (no DB write) |
| ② | Seed `department_tag_mappings` (`dept_id` → `tag_id`) | psql on `ncmu-pg-ncmu` (+ `GET/POST /api/v1/ncmu/admin/tags`) | writes `department_tag_mappings` |
| ③ | Trigger the sync → read `SyncResult` | `POST /api/v1/ncmu/admin/dingtalk/sync` | writes `users` + `user_tags` |
| ④ | Verify synced users / tags / (sample) login | psql + (optional) 钉钉扫码登录 | reads DB |

> **Line numbers / paths below are anchors against base `71ed77b`. The
> endpoint path / table name / column name is the authoritative lookup
> anchor — if a file moved, re-`grep` the symbol, don't trust a stale
> path.** All four endpoints are `Depends(require_admin)`; the caller needs
> an admin JWT (§3.0).

## 2. Prerequisites checklist

Tick **all** before step ①, or steps ①/③ fail at the DingTalk call:

- [ ] **DingTalk 「部门读」+「成员读」permissions are PUBLISHED & effective.**
      `qyapi_get_department_list` + `qyapi_get_department_member` must be
      live via the DingTalk developer backend → **「版本管理与发布」→ 发布**.
      "待发布 / 需审批" status ⇒ the oapi returns `errcode≠0` ⇒ the endpoint
      surfaces **HTTP 502 `{"detail":{"code":1311,...}}`** (see §5).
- [ ] **DingTalk app credentials are real in `.env`** (not `CHANGE_ME`):
      `DINGTALK_APP_KEY` (.env.example:404), `DINGTALK_APP_SECRET` (:406).
      `DINGTALK_CORP_ID` (:400) — paste the real CorpId if not yet filled.
      (See `production-secrets-runbook.md` §2.6.)
- [ ] **Backend can reach the internet** to call `oapi.dingtalk.com`
      (`DINGTALK_OAPI_BASE` default `https://oapi.dingtalk.com`).
- [ ] **Backend container healthy**:
      `docker compose ps ncmu-backend` shows healthy, and
      `curl -fsS http://localhost/healthz` (via `ncmu-nginx`) returns `ok`.
- [ ] **DB migrated to head** (table `department_tag_mappings` exists,
      migration `0011`):
      `docker exec ncmu-pg-ncmu psql -U ncmu_app -d ncmu -c '\d department_tag_mappings'`
- [ ] **At least one `tags` row exists** to map the department onto
      (create via `POST /api/v1/ncmu/admin/tags` or check `SELECT * FROM tags;`).

> **Base URL note:** `ncmu-backend` is `expose: 8000` only (no host port);
> reach it through the **`ncmu-nginx` gateway** which proxies
> `/api/v1/ncmu/*` → `ncmu-backend:8000`. Host port defaults to `80`
> (`NCMU_NGINX_HTTP_HOST_PORT`). All curls below use
> `BASE="http://localhost"`. If host:80 is inconvenient, run curl from
> inside the network instead, e.g.
> `docker exec ncmu-nginx curl -s http://ncmu-backend:8000/<path> ...`.

```bash
# Shell setup used by every step below:
BASE="http://localhost"          # ncmu-nginx gateway (host port 80 default)
```

## 3. The LIVE 4 steps

### 3.0 Obtain an admin JWT (all 4 endpoints are admin-gated)

`require_admin` accepts a JWT whose `sub` is in `NCMU_ADMIN_USER_IDS`
(default = seeded admin 张三 `a0000001-0000-4000-8000-000000000001`).

**dev / dogfood environment** (`NCMU_ENABLE_DEV_LOGIN=true`, the current
state — `.env.example:357`): trade the admin UUID for a JWT via dev-login:

```bash
ADMIN_JWT=$(curl -s -X POST "$BASE/api/v1/ncmu/auth/dev-login" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"a0000001-0000-4000-8000-000000000001"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["jwt"])')
echo "${ADMIN_JWT:0:16}…"   # sanity: non-empty
```

> **prod note:** with `NCMU_ENABLE_DEV_LOGIN=false`, dev-login returns 404
> and the admin JWT must come from 钉钉扫码登录 (2D-A2-登录). The bootstrap
> admin (张三) is seeded with `dingtalk_userid = NULL`, so DingTalk login
> won't match them until provisioned — run the first LIVE sync in the
> dev/dogfood profile (consistent with "首批 dogfood = 软件开发部"), or
> pre-provision the admin's `dingtalk_userid`.
>
> Auth failures: missing/!Bearer header → **401** `{"detail":{"code":1102/1103}}`;
> non-admin subject → **403** `{"detail":{"code":1201,"message":"admin permission required"}}`.

### 3.1 Step ① — Discover the department tree, find 「软件开发部」dept_id

`discover_departments` pulls the whole tree from the DingTalk root
(`dept_id=1`) — a one-shot helper, **no DB write**.

```bash
curl -s "$BASE/api/v1/ncmu/admin/dingtalk/departments" \
  -H "Authorization: Bearer $ADMIN_JWT" | python3 -m json.tool
```

**Expected return** (HTTP 200) — flat list, each entry `{dept_id, name, parent_id}`:

```json
{
  "departments": [
    {"dept_id": 12345678, "name": "中央研究院", "parent_id": 1},
    {"dept_id": 23456789, "name": "软件开发部", "parent_id": 12345678}
  ]
}
```

Record the `dept_id` whose `"name"` is `"软件开发部"` (here `23456789`) —
call it **`DEPT_ID`** below.

```bash
DEPT_ID=23456789   # ← from the response above
```

**Failure triage:**
- HTTP **502** `{"detail":{"code":1311,"message":"dingtalk api error <errcode>: ..."}}`
  → DingTalk rejected the call. Almost always the **permissions are not yet
  published** (§2) — check 「版本管理与发布」. The real `errcode/errmsg`
  is in the message and in `docker logs ncmu-backend`.
- HTTP **401/403** → admin JWT problem (§3.0).

### 3.2 Step ② — Seed `department_tag_mappings` (dept_id → tag_id)

The sync reads `department_tag_mappings` to decide which `user_tags` to
write. Composite PK `(dept_id, tag_id)` ⇒ **one department can map to many
tags** (insert one row per tag); re-inserting the same pair is a no-op
duplicate-key error (harmless).

**(a) Get the `tag_id`(s).** `tag_id` is a `tags.id` UUID. Either list
existing tags or create one:

```bash
# List existing tags (id + name):
curl -s "$BASE/api/v1/ncmu/admin/tags" -H "Authorization: Bearer $ADMIN_JWT" \
  | python3 -m json.tool
# …or create a new tag (returns the created row incl. its id):
curl -s -X POST "$BASE/api/v1/ncmu/admin/tags" \
  -H "Authorization: Bearer $ADMIN_JWT" -H 'Content-Type: application/json' \
  -d '{"name":"软件开发部"}' | python3 -m json.tool
```

You can also read it straight from the DB:
`docker exec ncmu-pg-ncmu psql -U ncmu_app -d ncmu -c "SELECT id, name FROM tags;"`

**(b) Insert the mapping row(s)** (`dept_id` is the BigInt from §3.1;
`tag_id` is the UUID from (a)):

```bash
docker exec ncmu-pg-ncmu psql -U ncmu_app -d ncmu -c \
  "INSERT INTO department_tag_mappings (dept_id, tag_id)
   VALUES (${DEPT_ID}, '11111111-1111-4111-8111-111111111111');"
```

> A dev template lives at `ncmu-backend/scripts/seed_dept_tag_mappings.sql`
> (dev-only; fill `dept_id` after §3.1). To map the department to **several**
> tags, run one `INSERT` per `(dept_id, tag_id)` pair. **To expand the synced
> population later, you seed more `(dept_id, tag_id)` rows — you never change
> code** (§4).

**Verify the seed:**

```bash
docker exec ncmu-pg-ncmu psql -U ncmu_app -d ncmu -c \
  "SELECT m.dept_id, t.name FROM department_tag_mappings m
     JOIN tags t ON t.id = m.tag_id;"
```

> A department with **no** mapping row still gets its users created (B-strategy),
> but they receive **no tags** from it. Seed the mapping before the sync if
> you want tags written on the first pass (the sync is idempotent — re-running
> after seeding backfills tags, §4).

### 3.3 Step ③ — Trigger the sync, read `SyncResult`

`trigger_sync` body takes `root_dept_id`; if omitted it falls back to
`settings.dingtalk_sync_root_dept_id` (env `DINGTALK_SYNC_ROOT_DEPT_ID`).
For a one-shot LIVE run, **pass it in the body** (no restart needed):

```bash
curl -s -X POST "$BASE/api/v1/ncmu/admin/dingtalk/sync" \
  -H "Authorization: Bearer $ADMIN_JWT" -H 'Content-Type: application/json' \
  -d "{\"root_dept_id\": ${DEPT_ID}}" | python3 -m json.tool
```

**Expected return** (HTTP 200) — `SyncResult` JSON, exactly these fields:

```json
{
  "root_dept_id": 23456789,
  "departments": 3,
  "users_seen": 12,
  "users_created": 12,
  "users_updated": 0,
  "tags_written": 12
}
```

- `departments` = subtree dept count actually walked (**子部门 ∪ {root}** —
  the root itself is included so its direct members aren't missed).
- `users_created` / `users_updated` = new INSERTs vs existing-`dingtalk_userid`
  updates. On a **re-run** expect `users_created: 0` and everything in
  `users_updated` (idempotent, §4).
- `tags_written` = total `user_tags` rows written this pass (replace-all).

> **Permanent default (optional):** instead of the body, set
> `DINGTALK_SYNC_ROOT_DEPT_ID=<DEPT_ID>` in `.env` and **restart the backend**
> (`docker compose up -d ncmu-backend` — config is read at startup, a plain
> `restart` does NOT reload env), then `POST …/sync` with an empty body.

**Failure triage:**
- HTTP **400** `{"detail":{"code":1302,"message":"root_dept_id not provided and DINGTALK_SYNC_ROOT_DEPT_ID unset"}}`
  → you sent no `root_dept_id` and the env var is unset. Pass it in the body.
- HTTP **502** `{"detail":{"code":1311,...}}` → DingTalk upstream `errcode≠0`
  (permissions / token / network). It is **never silently swallowed** —
  the row is not partially written because the whole sync is one transaction
  (commit only on success). Check the message + `docker logs ncmu-backend`.

### 3.4 Step ④ — Verify

**Synced users (zero-credential, `dingtalk_userid` populated):**

```bash
docker exec ncmu-pg-ncmu psql -U ncmu_app -d ncmu -c \
  "SELECT id, dingtalk_userid, name, dept_path
     FROM users WHERE dingtalk_userid IS NOT NULL ORDER BY dingtalk_userid;"
```

**`user_tags` bindings are correct (per the mapping):**

```bash
docker exec ncmu-pg-ncmu psql -U ncmu_app -d ncmu -c \
  "SELECT u.dingtalk_userid, u.name, t.name AS tag
     FROM users u
     JOIN user_tags ut ON ut.user_id = u.id
     JOIN tags t       ON t.id = ut.tag_id
    WHERE u.dingtalk_userid IS NOT NULL
    ORDER BY u.dingtalk_userid, t.name;"
```

Cross-check the counts against the `SyncResult` from §3.3 (`users_created +
users_updated` ≈ distinct `dingtalk_userid` rows; `tags_written` = total
`user_tags` rows just written).

**Sample login link** (only if 2D-A2-登录 is already LIVE): pick one synced
`dingtalk_userid`, run 钉钉扫码登录, and confirm it returns a JWT with
`sub` = that user's `id` (the login half matches the **server-resolved**
DingTalk userid against these rows — see §6 / login skeleton). If 2D-A2-登录
is not live yet, this sub-step is deferred; the DB checks above are
sufficient for the sync runbook.

## 4. B-strategy (建账号 + 打标签) — what the sync actually does

- **Match key = `dingtalk_userid`.** For each subtree employee, the sync
  looks up `users.dingtalk_userid`: **hit → UPDATE**, **miss → INSERT a new
  row**. New rows write only `id` (`uuid.uuid4()` — the PK has no server
  default), `dingtalk_userid`, `name` (DingTalk display name), `dept_path`.
  **The `User` model has no `password`/`email` column** → accounts are
  **zero-credential**; the login half (2D-A2-登录) authenticates by matching
  the server-verified DingTalk userid against these existing rows.
- **`user_tags` is replace-all (not append).** Each pass deletes the user's
  existing `user_tags` then re-writes the union of tags from every
  in-subtree, mapped department the user belongs to → **re-running the sync
  is idempotent** (same result every time; no duplicate rows; users not
  re-created).
- **Scope = the 「软件开发部」subtree only** (`root_dept_id`'s children **∪
  {root}**). Anything outside the subtree — including existing NCMU users —
  is **not touched** (not deleted, not modified).
- **Expanding the population = seed more `department_tag_mappings` rows**
  (and/or sync a different/larger `root_dept_id`). **No code change.**

## 5. Safety, idempotency, rollback & troubleshooting

| Concern | Behaviour | Operator action |
|---|---|---|
| **Re-run safety** | Idempotent: replace-all tags + match-by-`dingtalk_userid` (no re-create). | Safe to re-run after fixing a mapping — counts shift to `users_updated`. |
| **Upstream error** | DingTalk `errcode≠0` → `DingTalkApiError` → HTTP **502** `code 1311`, **never silent**. Whole sync is one transaction (commit only on success) → no partial write. | Read the 502 message + `docker logs ncmu-backend`; fix permission/token/network; re-run. |
| **Missing root** | No body `root_dept_id` and env unset → HTTP **400** `code 1302`. | Pass `root_dept_id` in body (§3.3) or set `DINGTALK_SYNC_ROOT_DEPT_ID` + restart. |
| **Scope isolation** | Out-of-subtree NCMU users untouched. | Verify with the §3.4 queries (their `dept_path`/tags unchanged). |
| **Wrong tag mapping** | `user_tags` is replace-all. | `DELETE FROM department_tag_mappings WHERE dept_id=… AND tag_id=…;` then re-run sync → the removed tag is dropped from those users on the next pass. |
| **Delete a tag entirely** | FK `ON DELETE CASCADE` → deleting a `tags` row cascade-clears its `department_tag_mappings` **and** `user_tags`. | `DELETE FROM tags WHERE id=…;` (then optionally re-sync). |
| **Wrongly-created user** | B-strategy creates rows; there is no auto-delete. | Prefer flipping `is_active=false` over a hard `DELETE` (sessions/FKs); or DELETE manually if truly unwanted. Re-running sync will re-create a still-in-subtree user. |

**Error-code reference** (grounded in `dingtalk/routes.py`; 13xx is
non-exclusive — `sessions/routes.py` owns 1300/1301, so this module uses
1311/1302):

| HTTP | `code` | Meaning |
|---|---|---|
| 502 | `1311` | DingTalk oapi upstream `errcode≠0` (permission / token / network) |
| 400 | `1302` | `root_dept_id` not in body and `DINGTALK_SYNC_ROOT_DEPT_ID` unset |
| 401 | `1102`/`1103` | missing / malformed `Authorization: Bearer` header |
| 403 | `1201` | JWT subject not in `NCMU_ADMIN_USER_IDS` |

## 6. Endpoint & schema reference (field-level, re-grep if drift)

**Endpoints** (`ncmu-backend/src/ncmu_backend/dingtalk/routes.py`, both
`Depends(require_admin)`; auto-mounted — `dingtalk` is a depth-1 subpackage
discovered by `main.py:_discover_and_include_routers`, do NOT manually
include):

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/v1/ncmu/admin/dingtalk/departments` | — | `{"departments":[{dept_id,name,parent_id}]}` |
| POST | `/api/v1/ncmu/admin/dingtalk/sync` | `{"root_dept_id": <int|null>}` | `SyncResult` |

**`SyncResult`** (`dingtalk/sync_service.py`): `root_dept_id`, `departments`,
`users_seen`, `users_created`, `users_updated`, `tags_written` — all ints.

**Tables** (`ncmu-backend/src/ncmu_backend/db/models.py`):

| Table | Columns (relevant) | Notes |
|---|---|---|
| `department_tag_mappings` | `dept_id` BigInteger, `tag_id` UUID FK `tags.id` CASCADE, `created_at` | composite PK `(dept_id, tag_id)`; migration `0011` |
| `users` | `id` UUID PK, `dingtalk_userid` String(64) unique nullable, `name` String(64), `dept_path` String(255) nullable, `is_active` | **no password/email column** (zero-credential) |
| `user_tags` | `user_id` UUID FK `users.id`, `tag_id` UUID FK `tags.id` | composite PK; replace-all per sync |
| `tags` | `id` UUID PK (`gen_random_uuid()`), `name` unique, `description` | created via `…/admin/tags` (PE-05) |

## 7. Related

- **Sync implementation (authoritative, in-repo):**
  `ncmu-backend/src/ncmu_backend/dingtalk/sync_service.py`,
  `dingtalk/routes.py`, `dingtalk/client.py`,
  `db/models.py` (`DepartmentTagMapping`),
  `alembic/versions/0011_add_department_tag_mappings.py`,
  `config.py` (`dingtalk_sync_root_dept_id` / `DINGTALK_*`).
- **Login skeleton (the half this sync provisions accounts for):**
  `ncmu-backend/src/ncmu_backend/auth/dingtalk_login.py` +
  `auth/routes.py` (`/api/v1/ncmu/auth/dingtalk/{login,callback}`) — matches
  the **server-resolved** DingTalk userid against the rows this sync creates.
- **Design / plan sources** (sibling `NCMU-Wiki/sources/phase2/`):
  `2026-06-01-phase2d-a2-sync-plan.md` (implementation plan / B-strategy /
  软件开发部子树 / §8 独审回写 §8.1 防冒充),
  `2026-06-03-phase2d-a2-sync-and-secrets-milestone.md` (§3.1 转 LIVE 待办),
  `2026-06-03-phase2d-a2-login-skeleton-design.md` (login design).
- **Sibling runbook (style + DingTalk creds §2.6):**
  `docs/runbooks/production-secrets-runbook.md`.
