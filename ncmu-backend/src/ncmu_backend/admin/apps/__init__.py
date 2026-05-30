"""Admin App-management module (TASK-PE-07).

Sibling to ``ncmu_backend.admin.routes`` (debug/sync, incl. the existing
POST /sync_apps from Phase 2B TASK-67a), ``ncmu_backend.admin.users``
(PE-06) and ``ncmu_backend.admin.tags`` (PE-05). Lives in its own
sub-package so the route handlers stay focused on App-management verbs
(list / detail / is_active toggle). Wired into ``ncmu_backend.main`` via
manual ``app.include_router`` because the auto-discovery scanner only
recurses one level into ``ncmu_backend.<sub>.routes`` (same depth-2 limit
hit by ``admin.users.routes`` and ``admin.tags.routes``).

Scope (Phase 2E Batch 3):
- GET  /admin/apps               admin list (all apps, not employee-filtered;
                                 include_inactive + search query params)
- GET  /admin/apps/{id}          single-app detail
- PATCH /admin/apps/{id}         toggle is_active only (other fields are
                                 owned by Dify Console — read-only here)

POST /admin/sync_apps is NOT re-implemented here — the Phase 2B endpoint
in ``admin.routes`` is reused as-is (the SPA "立即同步" button calls it).

Webhook path A (spec §5.5.2 POST /admin/webhooks/dify) was dropped: the
PE-07 Step 0 spike proved Dify v1.13.3 has no outbound app-lifecycle
webhook (only the inbound WorkflowWebhookTrigger), so ``webhooks.py`` is
not created and the cache stays poll-synced (path B). App↔tag binding
endpoints (PUT /admin/apps/{id}/tags) are deferred to PE-08.
"""
