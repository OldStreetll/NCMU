# NCMU — make targets
#
# 仓内此前无 Makefile。本文件首发为 TASK-PC4 真容器 E2E 提供一键入口
# (`make e2e-personal-kb`)；其余 dev 入口（start-dev / stop / backup / restore）
# 仍以 scripts/*.sh 直接调用为主，未来按需补 target。
#
# Quick reference:
#   make help                # 列 target
#   make e2e-personal-kb     # Phase 2C Batch C4 真容器 E2E (TASK-PC4)
#   make e2e-workflow-modes  # Phase 2B 5 mode 真容器 E2E (TASK-79)
#
# 注意：dev pane 不启停 docker stack。所有 e2e target 仅校验 stack 已 healthy，
# 不替 Boss 跑 `docker compose up` / `docker compose down`。

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

REPO_ROOT := $(shell pwd)
SCRIPTS_DIR := $(REPO_ROOT)/scripts

# 必需 service（与 e2e_personal_kb.sh REQUIRED_SERVICES 一致 / Boss 启停 stack 自负责）
PC4_REQUIRED_SERVICES := ncmu-backend pg-ncmu fastgpt-app dify-api kb-adapter

.PHONY: help e2e-personal-kb e2e-workflow-modes _check-stack-healthy

help:
	@printf 'NCMU make targets:\n'
	@printf '  make e2e-personal-kb      Phase 2C Batch C4 真容器 E2E (TASK-PC4)\n'
	@printf '  make e2e-workflow-modes   Phase 2B 5 mode 真容器 E2E (TASK-79)\n'
	@printf '\nNote: 本 Makefile 不启停 docker stack — Boss 负责 stack lifecycle。\n'

# ─── _check-stack-healthy ────────────────────────────────────────────────
# 共用前置：5 service 全 healthy 才允许进 e2e target。
# 字段级 named lookup（per-service Health）— 与 e2e_personal_kb.sh preflight
# 段同型，但精简版（仅 hard fail / 不走完整 preflight）。
_check-stack-healthy:
	@missing=0; for svc in $(PC4_REQUIRED_SERVICES); do \
	  h=$$(docker compose ps --format json 2>/dev/null | jq -r --arg s "$$svc" \
	       'select(.Service == $$s) | .Health // "none"' | head -1); \
	  if [ "$$h" != "healthy" ]; then \
	    printf '[FAIL] service %s Health=%s (expected healthy)\n' "$$svc" "$$h" >&2; \
	    missing=1; \
	  fi; \
	done; \
	if [ "$$missing" -ne 0 ]; then \
	  printf '\nstack not healthy — Boss 需先跑 scripts/start-dev.sh 启动 stack\n' >&2; \
	  exit 1; \
	fi; \
	printf '[OK] all 5 required services healthy\n'

# ─── e2e-personal-kb (TASK-PC4) ──────────────────────────────────────────
# 完整业务流真容器 E2E：黄金 11 step + 6 异常 + 收尾清理。
# 时长预算 ≤ 8 min（黄金 ≤ 5 min / 异常 ≤ 3 min — AC#5）。
e2e-personal-kb: _check-stack-healthy
	@bash $(SCRIPTS_DIR)/e2e_personal_kb.sh

# ─── e2e-workflow-modes (TASK-79) ────────────────────────────────────────
# 复用 Phase 2B 已交付 5 mode E2E — 同样需 stack healthy。
e2e-workflow-modes: _check-stack-healthy
	@bash $(SCRIPTS_DIR)/e2e_workflow_modes.sh
