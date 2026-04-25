"""ncmu-backend — Phase 1 FastAPI app.

Sub-packages with `routes.py` are auto-discovered by main.py at startup
(see `main._discover_and_include_routers`). To add a new endpoint group,
create `ncmu_backend/<mod>/routes.py` exporting `router = APIRouter()`;
no main.py change is needed.
"""
