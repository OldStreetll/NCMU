"""Phase 2D-A2 DingTalk 通讯录同步模块。

本模块封装钉钉开放平台 oapi 调用 + 同步编排（按任务分批落地）：
- client.py (TASK-2DA2S-01)：oapi 三能力 —— get_access_token（带进程内缓存）
  / list_departments（递归全树）/ list_department_users（分页聚合）。
后续 sync_service.py / routes.py 由 TASK-2DA2S-03 引入。
"""
