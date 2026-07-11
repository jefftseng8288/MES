# CLAUDE.md — MES 專案常駐規則(每次開工前先讀)

> 用途:這是 Claude Code 每次在本專案動手前必讀的常駐規則。內容自足,不依賴、不引用任何外部專案的檔案。
> 依據文件(以 `docs/` 下實體文件當前內容為準,不用記憶版本):
> `MES_Roadmap_v8.md`(主依據)、`MES_Entity_Model_v1.md`、`MES_Observation_Schema_v1.md`、
> `MES_Knowledge_Schema_v1.md`、`MES_Feature_Taxonomy_v1.md`、`MES_Build_vs_Buy_Matrix_v1.md`。

---

## A. Project Overview

- **MES(Market Evolution System)是一個「會因證據而改變」的市場學習系統**,把市場開發當成科學方法的循環:觀察 → 修正 → 成交 → 營收。核心是認知循環(Entity → Observation → Knowledge → Insight → Hypothesis → Experiment → Outcome → Evolution),不是一條 AI pipeline。
- **第一個試驗場 = EscapeFlow(Shopify review app)。** 但 MES **不綁定任何特定產業**——EscapeFlow 只是剛好有成熟資料商可用的場景;MES 對「沒有現成資料商」的產業同樣要能從 Discovery 自己建立第一批 Observation(P6 Provider Agnostic 的一體兩面)。
- **當前階段:Phase 1 執行中(Crawler → Observation Log)。** 這一階段只做**資料地基**——證明能穩定產生乾淨、中立、結構正確、掛在正確 Entity 上、可追溯的 Observation。「能抓」不算成功,「抓進來的資料乾淨且結構對」才算成功。

---

## B. 工程基線(依 Roadmap v8)

- **技術棧:** Python 3.12 / uv / PostgreSQL 16(Docker) / SQLAlchemy 2(Async) / Alembic / pytest
  - **DB driver 雙軌並存(勿誤刪其一):** App 端用 `asyncpg`(async);Alembic migration 用 `psycopg` 同步連線(Alembic 本質同步,`migrations/env.py` 內把 asyncpg URL 轉回 `+psycopg`)。兩個 driver 並存為標準做法,各有其用。
- **物理路徑:** `/Users/cashflow/Documents/MES/`(Mac Mini M4 本地)
- **獨立性:** 獨立 docker compose(與 EF_WorkFlow 生命週期解耦)、獨立資料庫。MES 的資料與生命週期不與任何其他專案共用。
- **裁剪原則(這一階段刻意不做):不建 FastAPI、不建 Dashboard、不建 AI 模組。** 只做最純粹的資料地基;任何「把系統做得更完整、更優雅」的衝動,先用 Roadmap 的目的宣言校準:這是在服務目的,還是在追求糖衣?

---

## C. 資料層鐵律(MES 專屬,依 Entity / Observation / Knowledge Schema)

1. **Event Sourcing 兩張表。**
   - `Observation_Log` = 事件日誌 = **唯一真相(source of truth),Append-Only**。
   - `Knowledge_State` = 物化視圖(materialized view),為查詢效能而存在,**永遠可從 Observation_Log 完整重建**。
   - **絕無「直接改 Knowledge_State」的後門。** 任何新觀測必先 append 進 Observation_Log,再投影更新 Knowledge_State。

2. **Append-Only。** 絕不 update、絕不 delete。同一 entity 的同一 feature 再次觀測 = **新增一筆帶新 timestamp 的記錄**,絕不覆蓋舊值。歷史因此天然可得(例:「App Changed」Growth Signal)。

3. **Provenance 硬約束(寫入時為空即拒絕)。**
   - `Observation_Log.entity_id` **不可為空**——每筆觀測必須知道自己屬於誰。
   - `Knowledge_State.source_observation_id` **不可為空**——每個當前值必須能追回它的來源觀測。
   - 這是**結構上的鐵律**,不是「希望大家記得維護」的善意;寫入時上游引用為空,直接拒絕。

4. **失敗三值語義(失敗訊號絕不偽裝)。** 在 schema 層即區分,程式無法混淆:
   - `observed` — 抓到了,值有效。
   - `fetch_failed` — 嘗試觀測但失敗(429 / timeout / 頁面改版解析失敗);值空,但這筆「試過、沒成功」的記錄本身有意義。
   - `not_found` — 成功抓取、確認該 feature 不存在(有效的**負向觀測**,不是失敗)。
   - **絕不可把失敗記成 0、空值,或偽裝成 `not_found`。** 「上週沒數據」是「沒抓到」還是「真的沒有」,系統必須能分辨。

---

## D. 通用協作原則(跨專案適用,以 MES 自己的語言重寫,自足不外引)

### 核心識別欄位保護(MES 版)

**MES 的核心識別欄位 = `entity_id`、`canonical_key`、`observation_id`、`source_observation_id`、`feature`。**

- **規則 1 — 移除欄位區塊前,先逐一清點。** 要拿掉某個欄位/區塊前,先列出該區塊內**所有仍在使用的欄位**,逐一確認每個都有去處,才能移除。**禁止「為拿掉一個欄位而整塊砍,連帶犧牲其他在用欄位」。**
- **規則 2 — 核心欄位取不到時,不可無聲 fallback。** 不可把取不到的核心欄位悄悄填成空字串或佔位字。必須 `log warning` 或留可見標記,讓缺失看得見,避免靜默把壞資料帶上正式流程。(對應失敗三值:取不到就是 `fetch_failed` 或 `not_found`,**不是空值**。)
- **規則 3 — 取值/正規化邏輯收斂到單一函式。** 核心欄位的取值與正規化(如 `canonical_key` 的 normalize)**收斂到單一函式**,不得散落各路徑各自硬寫。normalize 規範見 `MES_Entity_Model_v1.md` 第四節。

### Claude Code 工作紀律

- **grep / 先讀現有實作,再改;修改優先於重建。** 動手前先看清楚既有結構,在既有結構上更新,不整份覆蓋。
- **動手前先讀相關實體文件,不憑記憶。** 以 `docs/` 下檔案的**當前內容**為準;不確定就查,不猜;文件不存在或檔名不符,據實回報,不假設、不編造。
- **不新增 Roadmap v8 沒有的設計或假設。** 這些文件是「把既定決策落成專案文件」,不是重新設計。
- **做完如實回報實際狀態。** 「code 改完 ≠ 驗過」——實作與驗收分開講;跑了什麼、結果如何、哪些還沒驗,誠實陳述。

---

## E. 文件分工(每份用途,勿混用)

- **`task_plan.md`** — 各 Phase 的工作清單與勾選進度(`- [ ]` / `- [x]`)。狀態抄 Roadmap v8。
- **`progress.md`** — 逐日/逐次進度日誌:記「**實際做了什麼、跑了什麼、結果如何**」。只記真實發生的事,沒發生的不要編。
- **`findings.md`** — 技術教訓與原則:記「**踩過的坑、根因、據此立下的原則**」。從實作過程長出來,不預先填滿。
- **`CLAUDE.md`(本檔)** — 專案常駐規則,每次開工前先讀。
