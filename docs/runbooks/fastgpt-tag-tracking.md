# FastGPT Image Tag Tracking Runbook (B-11)

> Audience: NCMU operators bumping `FASTGPT_IMAGE_TAG` in `.env`.
> Status: active (Phase 0 baseline 2026-04-23).

## 1. Why this runbook exists

FastGPT releases two version numbers that can drift:

| Number | Meaning | Where it lives |
|---|---|---|
| **Framework version** (e.g. `v4.14.12`) | The git tag at github.com/labring/FastGPT, anchoring the upstream `docker-compose.yml`, model registry, plugin manifests. | The repo at the moment we vendored its compose file (commit `6ec0d48`, see `errata-03`). |
| **App image tag** (e.g. `v4.14.10.2`) | The actual `ghcr.io/labring/fastgpt:<tag>` published to GitHub Container Registry. May be a *patch* number inside the framework version, or a `nightly` rebuild. | `.env.example` -> `FASTGPT_IMAGE_TAG`. Real value lives in `.env`. |

Phase 0 froze on **framework v4.14.12** but its compose file pins the
app image to **v4.14.10.2** — they look different but are in fact the
matching pair the upstream maintainers tested. Bumping one without the
other is a common foot-gun.

`errata-04` (NCMU-Wiki/sources/phase0/versions-locked-2026-04-21-errata-04.md)
documents the full reasoning; this runbook is the operator-facing
"what do I do" version.

## 2. Pre-bump checklist

Run this before changing `FASTGPT_IMAGE_TAG` in `.env` or `.env.example`.

### 2.1 Verify the candidate tag actually exists on ghcr.io

GitHub does not serve a public package list for every org. The reliable
URL is:

```
https://github.com/labring/FastGPT/pkgs/container/fastgpt
```

Confirm the candidate tag (e.g. `v4.14.13.0`) is in the "Recent tagged
versions" panel.  If you cannot see it through the browser, try a
manifest probe with no auth (works for public images):

```bash
curl -sI \
  -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
  https://ghcr.io/v2/labring/fastgpt/manifests/<TAG>
```

Anything other than `HTTP/2 200` (or a 401 that mentions
`Bearer realm`) means the tag does not exist or has been yanked.

### 2.2 Verify the framework release notes mention it

```
https://github.com/labring/FastGPT/releases/tag/<framework_version>
```

The release notes usually list the bundled image tag as
`docker pull ghcr.io/labring/fastgpt:<image_tag>`. If that tag does not
match the candidate, you are mixing framework + image versions and
must check the upstream `docker-compose.yml` of the framework version
(`projects/app/data/config.json`, etc.) for migration steps.

### 2.3 Diff the upstream compose file

```bash
git clone --depth 1 --branch <framework_version> \
    https://github.com/labring/FastGPT /tmp/fastgpt-upstream
diff -u /tmp/fastgpt-upstream/files/docker/docker-compose.yml \
        NCMU_Proj/NCMU/docker-compose.yml | less
```

Look for: env additions, mongo replSet args, plugin server tag bumps,
new volumes. Anything that touches `fastgpt-app`, `fastgpt-plugin`,
`fastgpt-mongo`, or `pg-fastgpt` may need a parallel change in NCMU
compose.

### 2.4 Verify pg-fastgpt + mongo image alignment

FastGPT does NOT version-pin its postgres / mongo bases consistently
between framework releases. Inspect the upstream compose for both:

| Service | Image | NCMU env var |
|---|---|---|
| `pg-fastgpt` | `pgvector/pgvector:0.8.0-pg15` (v4.14.12) | `FASTGPT_PGVECTOR_IMAGE_TAG` |
| `fastgpt-mongo` | `mongo:5.0.32` (v4.14.12) | `MONGO_IMAGE_TAG` |
| `fastgpt-plugin` | `ghcr.io/labring/fastgpt-plugin:v0.5.6` (v4.14.12) | `FASTGPT_PLUGIN_IMAGE_TAG` |

If upstream bumped any of these, decide whether to follow or stay back
(staying back may break model registry compatibility — see release
notes).

### 2.5 Spike on a throwaway profile first

```bash
# Pull only — does not change running containers.
docker pull ghcr.io/labring/fastgpt:<candidate>
# Smoke the image.
docker run --rm --entrypoint sh ghcr.io/labring/fastgpt:<candidate> \
    -c 'cat /app/projects/app/data/config.json | head -50'
```

Look for: model registry shape changes (the JSON schema of
`llmModels` / `vectorModels`) — those are the most common source of
post-bump incidents.

## 3. Performing the bump

1. Open an `errata-NN` page in `NCMU-Wiki/sources/phase0/` describing
   the bump and the upstream commit it tracks.  Do not bump silently.
2. Edit `NCMU_Proj/NCMU/.env.example`:
   ```
   FASTGPT_IMAGE_TAG=<new_tag>
   ```
   Keep the leading-comment block above it in sync if the framework
   version moved.
3. Run `docker compose --env-file .env config -q` — no errors.
4. `docker compose pull fastgpt fastgpt-plugin pg-fastgpt fastgpt-mongo`.
5. `scripts/stop.sh && scripts/start-dev.sh` (NOT a hot restart;
   model-registry migrations only run on cold boot).
6. Re-run `scripts/ncmu_init.py --bootstrap-fastgpt` if it fails to
   activate the bge-m3 / MiniMax-M2.7 models — model IDs may have
   shifted in the new registry.
7. Update `errata-NN` with the post-bump verification (admin login OK,
   model list shows expected models, embedding test query returns
   vectors of the same dimension as before).

## 4. Tag-locking discipline

`FASTGPT_IMAGE_TAG` MUST be a fully-qualified semver-style tag.
`latest`, `nightly`, and unversioned `v4` shorthands are forbidden:

* They make rollback ambiguous (`docker pull fastgpt:latest` next week
  may give you a different image than the one you tested).
* They break air-gap-style operator playbooks where the image tarball
  is staged ahead of time.
* They obscure incidents — a bug report citing "we ran fastgpt:latest"
  is unactionable.

`errata-04` formalises this constraint: any future `FASTGPT_IMAGE_TAG`
override that does not match `^v[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$`
should fail review.

## 5. Rollback

If a bump goes wrong:

1. Revert `.env` (or `.env.example` plus a fresh `.env` copy).
2. `docker compose down fastgpt fastgpt-plugin` — leave the data
   volumes in place; FastGPT v4.14.x supports backwards-compatible
   downgrades within the same minor.
3. `scripts/start-dev.sh`. The bootstrap script is idempotent.
4. If the new image had run mongo/pg migrations, downgrade may NOT be
   safe — restore from the last `mongo dump` taken before the bump
   (operator policy: snapshot before any image change).

## 6. Why the framework / image split exists

FastGPT releases the *framework* on its own cadence (compose file,
plugin server, frontend) and the *app image* on a faster cadence
(hot fixes, security patches). The compose file pins to a tested image
tag — usually a few patches behind the framework number — to keep CI
green. We mirror that decision because:

* Tracking just the framework breaks when the framework version is
  released ahead of a corresponding image build.
* Tracking just the image loses the compose / plugin / model registry
  changes shipped with the framework.
* Treating them as independent lockable knobs (errata-03 + errata-04)
  lets us pin the pair we tested without forcing operators to memorise
  the relationship.

## 7. References

* `NCMU-Wiki/sources/phase0/versions-locked-2026-04-21.md` — Phase 0
  version lock list (master).
* `NCMU-Wiki/sources/phase0/versions-locked-2026-04-21-errata-03.md` —
  Original FastGPT framework / image split rationale.
* `NCMU-Wiki/sources/phase0/versions-locked-2026-04-21-errata-04.md` —
  Tag-locking discipline + image / framework reconciliation rules.
* Upstream FastGPT releases:
  https://github.com/labring/FastGPT/releases
* Upstream container registry:
  https://github.com/labring/FastGPT/pkgs/container/fastgpt
