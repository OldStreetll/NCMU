# Production Secrets Runbook (密钥脱敏 / 上线准备)

> Audience: NCMU operators preparing a **production** deployment from the
> `.env.example` template. This is a *checklist + reference* for choosing
> strong values before first boot — it does NOT rotate the live dev `.env`
> (whose `CHANGE_ME` values are the currently-running docker-network
> passwords; rotating them in place breaks Dify / FastGPT / NCMU).
> Status: active (2026-06-02, base `ac486dc`).
> Source-of-truth: `.env.example`, `docker-compose.yml`,
> `docker-compose.override.yaml`, `ncmu-backend/src/ncmu_backend/config.py`
> — if those diverge from this doc, re-read those and update here.

## 1. Overview

`.env` (gitignored) carries **38 `CHANGE_ME` placeholder lines** (39 grep
hits — one is the header comment on line 6). Of those, ~25 are secrets you
must *generate*; the rest are real third-party values you *paste*, or
non-secret IDs. In dev, `docker-compose.yml` uses `${VAR:-CHANGE_ME}`, so
each placeholder both seeds the container on first `init` AND is spliced
into connection strings — which is why several keys MUST share a value.

Three rules dominate this runbook:

1. **DB password groups must be internally consistent** (§3) — the same
   password appears in the `*_PASSWORD` var, the embedded `*_DB_URL`, and
   one or more compose service envs.
2. **Encryption-class keys are irreversible** (§4) — fix them before the
   first byte of data is written; changing them later orphans all existing
   ciphertext.
3. **Several internal keys are mutual-auth pairs** (§3.2) — two services
   only handshake if both sides hold the identical secret.

Generation cheat sheet is §6.

## 2. Master secret table

Columns: **Key** (`.env.example` line) · **Protects what** (grounded in
real code/compose, not guessed) · **Class** · **How to generate** ·
**Must match** (cross-dependency) · **Irreversible?**

`gen-hex` = `openssl rand -hex 32` · `gen-b64` = `openssl rand -base64 32`
· `gen-b64-42` = `openssl rand -base64 42` · `gen-fernet` =
`python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
(full commands in §6).

> Line numbers below are anchors against the current `.env.example` /
> `docker-compose.yml` as of the base SHA in the header. If either file
> gains/loses lines later, the **key name is the authoritative lookup
> anchor** — re-`grep -n` the key, don't trust a stale line number.

### 2.1 🔴 DB password group (must be set BEFORE first container init)

| Key (line) | Protects what | Class | Generate | Must match | Irreversible? |
|---|---|---|---|---|---|
| `NCMU_APP_PASSWORD` (80) | pg-ncmu `ncmu_app` login (NCMU sessions/users/RBAC) | DB pw | gen-hex | `NCMU_DB_URL`(82) embedded pw · compose `POSTGRES_PASSWORD`(29) · compose `NCMU_DB_URL`(791) | No¹ |
| `NCMU_DB_URL` (82) | same pw embedded in conn string | (mirror) | — | embedded pw == `NCMU_APP_PASSWORD` | No¹ |
| `DIFY_DB_PASSWORD` (94) | pg-dify `dify` login (Dify apps/DSL/convos) | DB pw | gen-hex | `DIFY_DB_URL`(97) · compose `POSTGRES_PASSWORD`(61) · `PGPASSWORD`(199) · `DB_PASSWORD`(238,377) · `PGVECTOR_PASSWORD` inherits via compose(253) | No¹ |
| `DIFY_DB_URL` (97) | same pw embedded | (mirror) | — | embedded pw == `DIFY_DB_PASSWORD` | No¹ |
| `PGVECTOR_PASSWORD` (222) | pg-dify vector store login (Dify embeddings) | DB pw | gen-hex (or leave to inherit) | compose default = `DIFY_DB_PASSWORD`(253); if set, MUST == `DIFY_DB_PASSWORD` | No¹ |
| `FASTGPT_DB_PASSWORD` (109) | pg-fastgpt `fastgpt` login (FastGPT vectors/metadata) | DB pw | gen-hex | `FASTGPT_DB_URL`(112) · compose `POSTGRES_PASSWORD`(99) · `PG_URL`(694) | No¹ |
| `FASTGPT_DB_URL` (112) | same pw embedded | (mirror) | — | embedded pw == `FASTGPT_DB_PASSWORD` | No¹ |
| `FASTGPT_MONGO_PASSWORD` (295) | fastgpt-mongo `root` login (FastGPT datasets/chat) | DB pw | gen-hex | compose `MONGO_INITDB_ROOT_PASSWORD`(541) · replSet init(563,567,576) · `MONGODB_URI`(640,696) | No¹ |

¹ Not cryptographically irreversible, but if changed AFTER the database is
initialized you must `ALTER USER ... PASSWORD` inside the running container
AND update every consumer above — not a fresh-deploy concern, but a
T3-destructive operation on a live DB. For a clean prod install, set once
before first `docker compose up`.

### 2.2 🔴 Encryption-class (irreversible — fix BEFORE first data write)

| Key (line) | Protects what | Class | Generate | Must match | Irreversible? |
|---|---|---|---|---|---|
| `DIFY_SECRET_KEY` (185) | Dify at-rest encryption (workspace tokens, model API keys) — MUST be a valid base64 Fernet key or Dify throws "Invalid encrypted data" at first login | encrypt | gen-fernet | compose `SECRET_KEY`(226) | **YES** — changing orphans all Dify-encrypted secrets |
| `FASTGPT_AES256_SECRET_KEY` (273) | FastGPT AES256 at-rest encryption | encrypt | gen-b64 (FastGPT requires this length) | compose `AES256_SECRET_KEY`(716) | **YES** |
| `FASTGPT_TOKEN_KEY` (269) | FastGPT session/token signing | sign | gen-hex | compose `TOKEN_KEY`(714) | Semi² |
| `FASTGPT_FILE_TOKEN_KEY` (271) | FastGPT file-download token signing | sign | gen-hex | compose `FILE_TOKEN_KEY`(715) | Semi² |
| `FERNET_KEY` (332) | **Intended**: encrypt `dify_external_kb_configs.api_key_encrypted` at rest. **NOT WIRED today** — no backend code reads it and the column was removed in alembic `0002` (Phase-3 restore per errata-08). Set a Fernet key now so the Phase-3 wiring inherits it. | encrypt (reserved) | gen-fernet | — | **YES** once wired |

² Semi-irreversible: changing invalidates outstanding tokens (users
re-login / re-issue file links) but does not corrupt stored data.

### 2.3 🟠 Dify internal mutual-auth keys (change ⇒ full Dify restart)

| Key (line) | Protects what | Class | Generate | Must match | Irreversible? |
|---|---|---|---|---|---|
| `DIFY_API_KEY` (150) | per-App key; default only used by sync scripts | bearer | gen-hex | — | No |
| `DIFY_CONSOLE_API_KEY` (152) | Dify ADMIN_API_KEY (admin bypass, never expires); ncmu-backend sends it as `Authorization: Bearer` to `/console/api` (read by config.py, **prod-required**) | bearer | gen-hex | **== `DIFY_ADMIN_API_KEY`** (override `ADMIN_API_KEY` 55,60) | No |
| `DIFY_ADMIN_API_KEY` (156) | same value wired into dify-api + dify-worker as `ADMIN_API_KEY` | bearer | (same as above) | **== `DIFY_CONSOLE_API_KEY`** | No |
| `DIFY_SANDBOX_API_KEY` (187) | dify-api ⇄ dify-sandbox code-execution auth | bearer | gen-b64-42 (per .env.example:182) | dify-api `CODE_EXECUTION_API_KEY`(259) == sandbox `API_KEY`(345) | No |
| `DIFY_PLUGIN_DAEMON_KEY` (189) | dify-api ⇄ plugin-daemon server auth | bearer | gen-b64-42 | dify-api `PLUGIN_DAEMON_KEY`(265) == daemon `SERVER_KEY`(388) | No |
| `DIFY_PLUGIN_INNER_API_KEY` (191) | plugin-daemon ⇄ dify-api inner API auth | bearer | gen-b64-42 | dify-api `INNER_API_KEY_FOR_PLUGIN`(267) == daemon `DIFY_INNER_API_KEY`(390) | No |

### 2.4 🟠 FastGPT internal + external keys

| Key (line) | Protects what | Class | Generate | Must match | Irreversible? |
|---|---|---|---|---|---|
| `FASTGPT_API_KEY` (237) | FastGPT API access (read by config.py, **prod-required**); independent of `KB_ADAPTER_ALLOWED_KEYS` | bearer | generate in FastGPT admin, or gen-hex | — | No |
| `FASTGPT_ROOT_KEY` (265) | dual-use: FastGPT container `ROOT_KEY`(712) AND `scripts/ncmu_init.py` bootstrap `rootkey:` header | bearer | gen-b64 (per .env.example:257) | compose `ROOT_KEY`(712) | No |
| `FASTGPT_DEFAULT_ROOT_PSW` (267) | FastGPT root account initial password | password | gen-b64 | compose `DEFAULT_ROOT_PSW`(713) | No³ |
| `FASTGPT_PLUGIN_TOKEN` (301) | fastgpt-app ⇄ fastgpt-plugin auth | bearer | gen-hex | app `AUTH_TOKEN`(639) == plugin `PLUGIN_TOKEN`(732) | No |
| `FASTGPT_STORAGE_ACCESS_KEY_ID` (283) | MinIO object-storage access key id | external | **paste** host MinIO credential | host MinIO config | No |
| `FASTGPT_STORAGE_SECRET_ACCESS_KEY` (285) | MinIO object-storage secret | external | **paste** host MinIO credential | host MinIO config | No |
| `FASTGPT_LLM_API_KEY` (251) | **external** LLM vendor key (MiniMax endpoint) | external | **paste** vendor key (NOT generated) | vendor account | No |
| `SILICONFLOW_API_KEY` (247) | **external** SiliconFlow embedding API key (read by config.py, **prod-required**) | external | **paste** from https://siliconflow.cn (NOT generated) | vendor account | No |

³ Set before first FastGPT boot (seeds the root account). Changeable later
via FastGPT admin UI.

### 2.5 🟡 NCMU backend self + signing

| Key (line) | Protects what | Class | Generate | Must match | Irreversible? |
|---|---|---|---|---|---|
| `NCMU_JWT_SECRET` (351) | **The Phase-1 runtime** HS256 JWT signing key for `/auth/dev-login` + DingTalk login (config.py:55-57, **prod-required**; ≥32 bytes) | sign | gen-fernet or gen-b64 | — | No⁴ |
| `JWT_SECRET` (328) | Phase-2 reserved; **NOT read by backend** (absent from config.py) | sign (reserved) | gen-hex | — | No |
| `HMAC_SECRET` (330) | Phase-2 reserved: DingTalk callback / Bot payload signing; **NOT wired** (absent from config.py) | sign (reserved) | gen-hex | — | No |
| `BOT_HMAC_SECRET` (408) | DingTalk Stream Bot signing; **NOT wired** (absent from config.py) | sign (reserved) | gen-hex | — | No |
| `KB_ADAPTER_ALLOWED_KEYS` (310) | comma-separated bearer tokens kb-adapter accepts on `/retrieval`; consumed by the **kb-adapter service**, NOT ncmu-backend (config.py:100). Each value must ALSO exist in `dify_external_kb_configs.api_key_encrypted` (Fernet-decrypted). | bearer (multi-value) | gen-hex per allowed caller, comma-joined | each entry ⇄ a `dify_external_kb_configs` row | No |

⁴ Changing forces all users to re-login (acceptable; no data loss).

### 2.6 🟡 DingTalk app credentials (mostly real values, not generated)

| Key (line) | Protects what | Class | How to fill | Must match | Irreversible? |
|---|---|---|---|---|---|
| `DINGTALK_CORP_ID` (400) | DingTalk enterprise CorpId (read by config.py) | ID (not secret) | **paste** real CorpId from DingTalk admin backend | DingTalk org | No |
| `DINGTALK_AGENT_ID` (402) | DingTalk app AgentId; reserved (absent from config.py) | ID (not secret) | **paste** real AgentId | DingTalk app | No |
| `DINGTALK_APP_KEY` (404) | DingTalk internal-app AppKey (read by config.py) | external cred | **paste** from DingTalk developer backend | DingTalk app | No |
| `DINGTALK_APP_SECRET` (406) | DingTalk internal-app AppSecret (read by config.py) | external secret | **paste** from DingTalk developer backend | DingTalk app | No |

### 2.7 ⚪ Optional / empty by default (no `CHANGE_ME`, listed for completeness)

| Key (line) | Notes |
|---|---|
| `REDIS_PASSWORD` (120) | Empty by default; redis runs without auth in-network. Set + propagate to all `redis://` consumers only if you front redis with auth. |
| `DIFY_INIT_PASSWORD` (196) | Empty by default ⇒ Dify shows the manual install wizard on first boot. Set to auto-seed the Dify admin account instead. |

## 3. Cross-dependency quick reference

The single most common deploy failure is a password that matches in some
places but not all. Before first boot, confirm each group below holds ONE
value across every listed location.

**DB password groups (set before first container init):**

| Group | `.env` keys that must equal | compose locations (line) |
|---|---|---|
| pg-ncmu | `NCMU_APP_PASSWORD` = embedded pw in `NCMU_DB_URL` | `POSTGRES_PASSWORD`(29), `NCMU_DB_URL`(791) |
| pg-dify | `DIFY_DB_PASSWORD` = embedded pw in `DIFY_DB_URL` = `PGVECTOR_PASSWORD`† | `POSTGRES_PASSWORD`(61), `PGPASSWORD`(199), `DB_PASSWORD`(238,377), `PGVECTOR_PASSWORD`(253) |
| pg-fastgpt | `FASTGPT_DB_PASSWORD` = embedded pw in `FASTGPT_DB_URL` | `POSTGRES_PASSWORD`(99), `PG_URL`(694) |
| fastgpt-mongo | `FASTGPT_MONGO_PASSWORD` (single source) | `MONGO_INITDB_ROOT_PASSWORD`(541), replSet(563,567,576), `MONGODB_URI`(640,696) |

† `PGVECTOR_PASSWORD` defaults to `DIFY_DB_PASSWORD` via compose `:-`
fallback (253). If you set it explicitly, it MUST equal `DIFY_DB_PASSWORD`.

**Mutual-auth key pairs (both endpoints must hold the same value):**

| Pair | `.env` keys | compose proof (line) |
|---|---|---|
| Dify admin bypass | `DIFY_CONSOLE_API_KEY` = `DIFY_ADMIN_API_KEY` | override `ADMIN_API_KEY`(55,60) |
| Dify sandbox | `DIFY_SANDBOX_API_KEY` | api `CODE_EXECUTION_API_KEY`(259) = sandbox `API_KEY`(345) |
| Dify plugin daemon | `DIFY_PLUGIN_DAEMON_KEY` | api `PLUGIN_DAEMON_KEY`(265) = daemon `SERVER_KEY`(388) |
| Dify inner API | `DIFY_PLUGIN_INNER_API_KEY` | api `INNER_API_KEY_FOR_PLUGIN`(267) = daemon `DIFY_INNER_API_KEY`(390) |
| FastGPT plugin | `FASTGPT_PLUGIN_TOKEN` | app `AUTH_TOKEN`(639) = plugin `PLUGIN_TOKEN`(732) |

> Rule of thumb: when you regenerate any key on the right, regenerate
> *both* env vars / both compose consumers in the same edit, then recreate
> (not just restart) the affected containers so the new env is read.

## 4. 🔴 Irreversible warning — set encryption keys before first data write

These keys encrypt data at rest. If you change them after data exists, the
existing ciphertext can no longer be decrypted — the data is effectively
lost (no rotation path without a re-encrypt migration):

- **`DIFY_SECRET_KEY`** — encrypts Dify workspace tokens + model API keys.
  Must be a valid base64 Fernet key from the start (any other random
  string fails at first Dify login). Fix before the install wizard.
- **`FASTGPT_AES256_SECRET_KEY`** — FastGPT AES256 at-rest. Fix before the
  first FastGPT dataset / API key is stored.
- **`FERNET_KEY`** — reserved for KB-config API-key encryption (Phase-3,
  not wired today). Choose a Fernet key now so the Phase-3 migration
  inherits a stable key rather than minting one post-hoc.

`FASTGPT_TOKEN_KEY` / `FASTGPT_FILE_TOKEN_KEY` are *signing* keys: changing
them invalidates outstanding tokens (re-login / re-issue links) but does
not corrupt stored data — recoverable, unlike the three above.

## 5. Deployment order (clean prod install)

1. **Copy template**: `cp .env.example .env`.
2. **Set DB password groups** (§3) — all four groups internally
   consistent. These take effect only at first container init, so they
   must be correct before step 4.
3. **Set encryption keys** (§4: `DIFY_SECRET_KEY`,
   `FASTGPT_AES256_SECRET_KEY`, `FERNET_KEY`) — irreversible once data is
   written, so fix them now.
4. **Set mutual-auth pairs + remaining generated secrets** (§2.3–2.5) —
   each pair identical on both sides.
5. **Paste external values** (§2.4 external rows, §2.6 DingTalk):
   `FASTGPT_LLM_API_KEY`, `SILICONFLOW_API_KEY`, `FASTGPT_STORAGE_*`,
   `DINGTALK_*`. These are real vendor / org values, not generated.
6. **First boot**: `docker compose up -d`. Containers initialize their DBs
   and seed admin accounts from the env values set above.
7. **Fill post-init values**: `DIFY_TENANT_ID` (owner-tenant UUID via the
   psql query in `.env.example`:141-145), `DIFY_APP_DEFAULT_TOKEN` (Dify
   console "Access API" panel) — then recreate ncmu-backend so it reads
   them.
8. **Verify**: with `DEPLOY_PROFILE=prod`, ncmu-backend raises at startup
   if any of the 6 prod-required secrets (`NCMU_JWT_SECRET`,
   `DIFY_CONSOLE_API_KEY`, `DIFY_APP_DEFAULT_TOKEN`, `DIFY_TENANT_ID`,
   `FASTGPT_API_KEY`, `SILICONFLOW_API_KEY`) is still a `CHANGE_ME`
   placeholder (config.py `_validate_sensitive_placeholders`). A clean
   startup is your placeholder-leak check.

> Note: `NCMU_ENABLE_DEV_LOGIN` MUST be `false` in prod — the lifespan
> startup raises `RuntimeError` if it is `true` while `DEPLOY_PROFILE=prod`.

## 6. Generation command cheat sheet

```bash
# Hex 32-byte (HMAC / JWT / generic bearer tokens):
openssl rand -hex 32

# Base64 32-byte (FastGPT *_KEY, AES256-class, root key/psw):
openssl rand -base64 32

# Base64 42-byte (Dify shared-secret bearer trio: sandbox /
# plugin-daemon / plugin-inner — per .env.example line 182):
openssl rand -base64 42

# Fernet key (DIFY_SECRET_KEY, FERNET_KEY, and a convenient
# high-entropy choice for NCMU_JWT_SECRET):
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'

# Multi-value KB_ADAPTER_ALLOWED_KEYS (one per allowed caller, comma-joined):
echo "$(openssl rand -hex 32),$(openssl rand -hex 32)"
```

**Not generated — paste real values**: `FASTGPT_LLM_API_KEY` (LLM vendor),
`SILICONFLOW_API_KEY` (siliconflow.cn), `FASTGPT_STORAGE_ACCESS_KEY_ID` /
`FASTGPT_STORAGE_SECRET_ACCESS_KEY` (host MinIO), `DINGTALK_*` (DingTalk
developer backend), `DIFY_TENANT_ID` (psql query post-init),
`DIFY_APP_DEFAULT_TOKEN` (Dify console post-init).

## 7. Scope note

This runbook is documentation only. It does not rotate the live dev `.env`
(those `CHANGE_ME` values are the running docker-network credentials — see
the 0-risk constraint at the top). When a real rotation is needed for a
specific key, the live-rotation procedures live in `scripts/README.md`
(e.g. "Dify ADMIN_API_KEY 生成 + 轮换") and the backup/restore safety net
in `backup-restore-runbook.md`.
