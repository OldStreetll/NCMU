"""invariance test: 所有 /api/v1/ncmu/admin/* endpoint 必须 Depends(require_admin)."""
import inspect
from fastapi import FastAPI
from ncmu_backend.main import app


def test_all_admin_endpoints_gated():
    """每条 path 以 /api/v1/ncmu/admin/ 开头的 route 必须含 require_admin 依赖."""
    from ncmu_backend.auth.deps import require_admin
    for route in app.routes:
        if not getattr(route, "path", "").startswith("/api/v1/ncmu/admin"):
            continue
        deps = [d.call for d in route.dependant.dependencies if d.call]
        # require_admin 直接挂或经子 Depends 嵌套（递归扫）:
        assert _depends_on(route.dependant, require_admin), \
            f"admin endpoint {route.path} 缺 require_admin gate"


def _depends_on(dependant, target_fn):
    if dependant.call is target_fn:
        return True
    return any(_depends_on(sub, target_fn) for sub in dependant.dependencies)
