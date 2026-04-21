"""ncmu_init.py — First-run system tag initialization.

Creates: customer / staff / admin system tags in pg-ncmu.
Idempotent (safe to re-run).

Source: v3.3.1 §18 line 1695.
"""
import os
import sys

import psycopg2

DB_URL = os.environ.get("NCMU_DB_URL")

SYSTEM_TAGS = [
    ("customer", "客户", "外部客户访问 App（guest JWT 或钉钉扫码 OAuth2）"),
    ("staff", "员工", "内部员工，手机号 + 钉钉工作通知验证码登录"),
    ("admin", "管理员", "NCMU 管理后台访问，全局权限"),
]


def _tags_table_exists(cur) -> bool:
    cur.execute("SELECT to_regclass(%s)", ("public.tags",))
    return cur.fetchone()[0] is not None


def _insert_system_tags(cur) -> None:
    for code, name, desc in SYSTEM_TAGS:
        cur.execute(
            "INSERT INTO tags (code, name, description, is_system) "
            "VALUES (%s, %s, %s, true) "
            "ON CONFLICT (code) DO NOTHING",
            (code, name, desc),
        )


def main() -> int:
    if not DB_URL:
        print("[ERROR] NCMU_DB_URL env var not set. Set it or load via .env.")
        return 1

    conn = psycopg2.connect(DB_URL)
    try:
        with conn, conn.cursor() as cur:
            # Phase 1 ncmu-backend migration creates the tags table.
            # Skip gracefully if not present yet.
            if not _tags_table_exists(cur):
                print("[SKIP] tags table not found; run ncmu-backend migration first")
                return 0
            _insert_system_tags(cur)
            print(f"[OK] initialized {len(SYSTEM_TAGS)} system tags")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
