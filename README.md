# NCMU v3 — Infrastructure Root

v3.3.1 baseline frozen 2026-04-20. See [[phase0-baseline-frozen]].

This repository hosts Phase 0 infrastructure orchestration:
- docker-compose (base/dev/prod)
- init-db DDL
- nginx configs
- system init scripts

Application code lives in separate repositories:
- `ncmu-kb-adapter` — self-built KB protocol adapter (Phase 0)
- `ncmu-backend` — FastAPI backend (Phase 1+)
- `ncmu-web` — React SPA (Phase 4)

See `NCMU_Proj/NCMU-Wiki/sources/specs/` for implementation plans (per CLAUDE.md §1 文档目录约束).
