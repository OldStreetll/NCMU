"""Phase 2B workflow subpackage — orchestrators for Dify's 4 non-chat
modes (advanced-chat / completion / workflow / agent-chat).

The chat orchestrator stays in `ncmu_backend.chat` untouched; this
subpackage owns the shared infrastructure (BaseOrchestrator + Dify
SSE client) plus the four mode-specific orchestrators that TASK-71/72/
73/74 will drop in. `routes.py` (TASK-69) wires this package into
FastAPI via the auto-discovery hook in `main._discover_and_include_routers`.
"""
