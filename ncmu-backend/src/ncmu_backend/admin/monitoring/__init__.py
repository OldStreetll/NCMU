"""Admin monitoring snapshot module (TASK-MON-1).

Sibling to the other admin sub-packages (apps / tags / users / dsl).
Ships a single read-only aggregation endpoint
(``GET /api/v1/ncmu/admin/monitoring``) that returns a one-page
operational snapshot — business operations + dingtalk integration status
(design: NCMU-Wiki sources/phase2/2026-06-04-admin-monitoring-page-mvp-design.md).

Wired into ``ncmu_backend.main`` via manual ``app.include_router`` because
the auto-discovery scanner only recurses one level into
``ncmu_backend.<sub>.routes`` and this lives at depth 2 (same manual-include
idiom as the PE-05/-06 admin sub-packages).
"""
