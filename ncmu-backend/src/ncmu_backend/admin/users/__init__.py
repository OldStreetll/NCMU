"""Admin users CRUD module (TASK-PE-06).

Sibling to ``ncmu_backend.admin.routes`` (debug/sync) but lives in its
own sub-package so the route handlers stay focused on the user-management
verbs (list/create/patch/soft-delete). Wired into ``ncmu_backend.main``
via manual ``app.include_router`` because the auto-discovery scanner only
recurses one level into ``ncmu_backend.<sub>.routes`` (see
``main.py:_discover_and_include_routers``).
"""
