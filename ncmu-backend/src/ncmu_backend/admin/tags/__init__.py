"""Admin tags CRUD module (TASK-PE-05).

Sibling to ``ncmu_backend.admin.routes`` (debug/sync) and
``ncmu_backend.admin.users.routes`` (PE-06 user CRUD), but lives in its
own sub-package so the route handlers stay focused on tag-management
verbs (list / create / update / delete). Wired into ``ncmu_backend.main``
via manual ``app.include_router`` because the auto-discovery scanner
only recurses one level into ``ncmu_backend.<sub>.routes`` (see
``main.py:_discover_and_include_routers`` — same depth-1 limit hit by
``personal_kb.admin_routes`` (Phase 2C) and ``admin.users.routes``
(PE-06)).

Binding routes (apps↔tags / users↔tags) deferred to PE-08 + PE-09;
PE-05 ships the ``tags`` table CRUD only. ``app_tags`` / ``user_tags``
ORM classes ship in this batch's models.py so PE-08/-09 don't churn
``alembic`` again.
"""
