"""pytest 全域設定:**讓測試跑在獨立的測試資料庫,絕不碰正式 DB。**

**為什麼(踩過的坑):** 測試原本直接寫進正式 DB,造成 —— store entity 有 85% 是測試
fixture(3498 / 4092)、測試批號 `2099-*` 混進正式觀測、store-harvest 佇列被測試假網域
塞爆並卡死 16 天。**測試資料寫進正式庫,是那一連串問題的共同源頭。**

**做法:** 本檔在 **任何測試被匯入之前**(conftest 於收集階段最先載入)把 `MES_DATABASE_URL`
指向獨立的測試庫,並重建其 schema。因為 `mes.config.get_settings()` 每次都重讀環境變數
(沒有快取),且 `migrations/env.py` 也走同一個 `get_settings()`,所以**應用程式與 Alembic
會一起指向測試庫**,測試檔本身一行都不用改。

每次 session 重建 schema(DROP SCHEMA + migration),因此測試從乾淨狀態開始 —— 順帶解掉
「跨次執行累積資料」造成的偶發性斷言失敗。
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg

_ROOT = Path(__file__).resolve().parent.parent
# 測試庫名稱:必須與正式庫不同,且以 _test 結尾(下方有硬性檢查,防止誤砍正式庫)。
_TEST_DB_SUFFIX = "_test"


def _swap_database(url: str, db_name: str) -> str:
    return urlunparse(urlparse(url)._replace(path=f"/{db_name}"))


def _sync(url: str) -> str:
    """asyncpg URL → psycopg 同步 URL(psycopg.connect 不吃 SQLAlchemy 的 +driver 標記)。"""
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _setup_test_database() -> str:
    """建立(如不存在)並重建測試庫 schema,回傳測試庫的 async URL。"""
    prod_url = os.environ.get("MES_DATABASE_URL")
    if not prod_url:
        # 沒有環境變數時退回讀 .env(與 app 同一份設定來源)。
        from mes.config import get_settings

        prod_url = get_settings().database_url

    prod_db = urlparse(prod_url).path.lstrip("/")
    test_db = os.environ.get("MES_TEST_DATABASE_NAME") or f"{prod_db}{_TEST_DB_SUFFIX}"

    # ★ 安全鎖:測試庫名稱必須與正式庫不同且以 _test 結尾,否則寧可中止也不動 schema。
    if test_db == prod_db or not test_db.endswith(_TEST_DB_SUFFIX):
        raise RuntimeError(
            f"拒絕執行:測試資料庫名稱 {test_db!r} 不安全(不可等於正式庫 {prod_db!r},"
            f"且必須以 {_TEST_DB_SUFFIX!r} 結尾)"
        )

    test_url = _swap_database(prod_url, test_db)

    # 1) 確保測試庫存在(連到 server 的 postgres 庫下 CREATE DATABASE)。
    with psycopg.connect(_sync(_swap_database(prod_url, "postgres")), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (test_db,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{test_db}"')

    # 2) 重建 schema —— 每次 session 從乾淨狀態開始(不殘留上次的測試資料)。
    with psycopg.connect(_sync(test_url), autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")

    return test_url


# --- 在任何測試模組被匯入前執行(conftest 於收集階段最先載入)----------------------
_TEST_URL = _setup_test_database()
os.environ["MES_DATABASE_URL"] = _TEST_URL  # app 與 Alembic 皆讀這個

# 3) 套用 migration 到測試庫(env.py 走同一個 get_settings(),故已指向測試庫)。
from alembic import command  # noqa: E402  - 必須在設定好環境變數之後才匯入
from alembic.config import Config  # noqa: E402

_alembic_cfg = Config(str(_ROOT / "alembic.ini"))
_alembic_cfg.set_main_option("script_location", str(_ROOT / "migrations"))
command.upgrade(_alembic_cfg, "head")
