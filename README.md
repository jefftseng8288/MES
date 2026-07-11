# MES — Market Evolution System

## 1. 專案目的

MES 是一個以證據驅動演化的市場系統。Phase 0 已定稿其設計方向；Phase 1 的目標是
持續產生乾淨、中立、結構正確、可追溯的 Observation。

**本次（本 commit 範圍）只建立最小工程骨架與本機 PostgreSQL 開發環境**：Python 專案初始化、
套件與品質工具、資料庫連線設定與驗證、測試與操作文件。不包含任何 MES 業務邏輯。

## 2. 必要工具與版本

- Python 3.12（本專案以 Homebrew 安裝的 Python 3.12 為唯一版本，見 `.python-version`）
- [uv](https://docs.astral.sh/uv/)
- Docker + Docker Compose（僅用來啟動 PostgreSQL 16）
- Git

## 3. 初始化方式

```bash
cd /Users/cashflow/Documents/MES
uv sync
```

`uv sync` 會依照 `.python-version` 使用 Python 3.12，並安裝 `pyproject.toml` 中列出的所有相依套件
（含開發工具）到 `.venv`。

## 4. 設定環境變數

複製範例檔並視需要調整：

```bash
cp .env.example .env
```

`.env` 不會被提交到 Git。至少需要以下變數（見 `.env.example`）：

```env
MES_ENV=development
MES_DATABASE_URL=postgresql+psycopg://mes:mes_dev_password@localhost:5432/mes
MES_LOG_LEVEL=INFO
```

## 5. 啟動 PostgreSQL

```bash
docker compose up -d
```

## 6. 等待 healthcheck 通過

```bash
docker compose ps
```

確認 `postgres` 服務的 STATUS 顯示為 `healthy`（首次啟動可能需要數秒）。

## 7. 執行 migration 檢查

Alembic 設定會從 MES 設定（`MES_DATABASE_URL`）讀取資料庫連線字串，並引用
`mes.db.base.Base` 的 metadata。目前沒有任何業務資料表，因此沒有實際的 migration 版本；
以下指令僅驗證 Alembic 能正常連線並回報目前版本：

```bash
uv run alembic current
```

## 8. 執行 lint、format、type check、tests

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

`tests/test_database_connection.py` 是一個整合測試，會實際連線到本機 PostgreSQL 並執行
`SELECT 1`；執行前請確保步驟 5–6 已完成（PostgreSQL 已啟動且 healthy）。

## 9. 停止 PostgreSQL

```bash
docker compose down
```

資料保存在 named volume 中，`docker compose down` 不會刪除資料；如需清除資料，另外執行
`docker compose down -v`。

## 10. 本次尚未實作的內容

以下項目**尚未實作**，將於後續階段處理：

- Entity / Observation 等業務資料表與 schema
- Discovery
- Knowledge Engine
- Crawler
- API
- UI
- 任何 AI / LLM 功能
