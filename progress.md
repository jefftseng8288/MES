# MES — Progress Log(進度日誌)

> **用途:** 逐日/逐次進度日誌 —— 記「實際做了什麼、跑了什麼、結果如何」。
> **只記真實發生的事,沒發生的不要編。** 「code 改完 ≠ 驗過」——實作與驗收分開記。
> 最新的在最上面。

---

## 2026-07-10 — DB 連線骨架:同步 → async 校正

- **動機:** 既有連線骨架用同步 engine,與 Roadmap v8 / CLAUDE.md 的「SQLAlchemy 2(Async)」基線不符。本次把連線層校正為 async,code 與文件同步更新。
- **改的檔案:**
  - `pyproject.toml`:相依加入 `asyncpg>=0.29`;`sqlalchemy>=2.0` 改為 `sqlalchemy[asyncio]>=2.0`(async 需 greenlet,由此 extra 帶入)。psycopg 保留供 Alembic 同步 migration 使用。
  - `src/mes/db/session.py`:`create_engine` → `create_async_engine`;`sessionmaker` → `async_sessionmaker`/`AsyncSession`;`get_session` 改 async context manager;`check_connection` 改 async。
  - `tests/test_database_connection.py`:改為 async 測試,實連 PostgreSQL(asyncpg)跑 `SELECT 1`,維持非 mock、真連線。
  - `.env` / `.env.example`:`MES_DATABASE_URL` driver 由 `postgresql+psycopg` 改為 `postgresql+asyncpg`(URL 仍由 `MES_DATABASE_URL` 供給,機制不變)。
  - `migrations/env.py`:Alembic migration 本質同步,於 env.py 內把 asyncpg URL 轉回 `+psycopg` 供其連線,避免 async URL 弄壞既有 migration 連線。
- **實跑結果(非假裝):**
  - `uv run pytest` → **1 passed**(async 連線 + `SELECT 1` 通過;PostgreSQL 容器 healthy)。
  - `uv run ruff check .` → All checks passed;`uv run mypy src` → Success, no issues。
  - `uv run alembic current` → 正常連線(sync psycopg driver),未被 async URL 破壞。
- **未碰:** Phase 1-B 的 ORM model 仍未建;本次只動連線層。B 階段之後建 model 與寫入邏輯時,直接長在 async 基礎上,無額外「延後待辦」。

## 2026-07-10

- 建立四份專案常駐文件:`task_plan.md`、`CLAUDE.md`、`progress.md`、`findings.md`。
  - `task_plan.md`:已存在,依「修改不重建」原則在既有結構上更新(未整份覆蓋)。
  - `CLAUDE.md`、`progress.md`、`findings.md`:本次新建。
- 建立前已實際讀取 `docs/` 下六份定稿文件當前內容為依據:`MES_Roadmap_v8.md`(主依據)、`MES_Entity_Model_v1.md`、`MES_Observation_Schema_v1.md`、`MES_Knowledge_Schema_v1.md`、`MES_Feature_Taxonomy_v1.md`、`MES_Build_vs_Buy_Matrix_v1.md`。六份皆存在、檔名相符。
