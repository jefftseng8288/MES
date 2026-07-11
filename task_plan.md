# MES — Task Plan(可勾選工作清單)

> 本文件用途:把已定稿的 **MES_Roadmap_v8** 落成可逐項勾選的工作清單。
> 它**不是重新設計**,不新增 Roadmap v8 沒有的設計或假設;每個 Phase 的狀態直接依 Roadmap v8 的標記。

## 依據文件(本次實際讀取,以檔案當前內容為準)

| 文件 | 版本 / 定稿日 | 角色 |
|---|---|---|
| `docs/MES_Roadmap_v8.md` | v8(Jeff 定案) | **主依據** |
| `docs/MES_Entity_Model_v1.md` | v1.0(2026-06-27 定稿) | Entity Model |
| `docs/MES_Observation_Schema_v1.md` | v1.0(2026-06-27 定稿) | Observation Schema |
| `docs/MES_Knowledge_Schema_v1.md` | v1.0(2026-06-27 定稿) | Knowledge Schema |
| `docs/MES_Feature_Taxonomy_v1.md` | v1(2026-06-27 定稿) | Feature Taxonomy |
| `docs/MES_Build_vs_Buy_Matrix_v1.md` | v1(2026-07-03 定稿) | Build vs Buy Matrix |

**最後更新日期:** 2026-07-10

---

## Phase 0 — 設計地基

**狀態:** ✅ 完成(五份全定稿)

**目的:** 把「最難改、改了最痛」的資料層定義定死,讓上層認知模組(Insight/Hypothesis 的各種實作者)可插拔。

**進入條件:** 五原則已定稿(✅)

**工作項目:**

- [x] Entity Model v1 定稿(準入判準「以它為中心聚合觀測」、第一版 Store + ReviewApp、關係即觀測、canonical_key normalize 規範、value 型別結構 value_type+分欄)
- [x] Observation Schema v1 定稿(五 metadata + entity 歸屬、Append-Only、status 三值 observed/fetch_failed/not_found、confidence 離散三級 certain/inferred/estimated、source 受控清單五值)
- [x] Knowledge Schema v1 定稿(兩張表 Event Sourcing、Default 取值規則 + feature 層覆寫、不存 previous_value、source_observation_id 必填)
- [x] Feature Taxonomy v1 定稿(9 個 feature、ReviewApp Signature Library 五個)
- [x] Principles / Roadmap 定稿(六原則 P1–P6 + 兩橫切關注點 Provenance Chain / Decision Graph)
- [x] Build vs Buy Matrix v1(四分類 Raw / Inferred / Historical / Third-party estimate;第一版不採購 Store Leads)
- [x] Observation / Knowledge 拍板為兩張表(Observation_Log 唯一真相 + Knowledge_State 物化視圖)

**驗收標準:** 能拿一家真實的店,在紙上(不寫程式)把它的特徵手動填進 schema,無「不知道存哪欄」的卡頓。

**停止條件:** Entity 關係沒定清 / Observation 邊界沒劃清 / 五 metadata 沒釘死 / Feature 還寫死在 schema → 都不進 Phase 1。

---

## Phase 1 — Crawler → Observation Log(每天乾淨累積)

**狀態:** 🔄 執行中(Running)— A 工程基礎設施已完成;B/C/D 未開始

**目的:** 證明「持續產生乾淨、中立、結構正確、掛在正確 Entity 上的觀測」能穩定運作。

**進入條件:** Phase 0 五份定稿(✅ 已達成)。

> **進度小結(2026-07-10):** A(工程基礎設施)已完成——最小工程骨架 + 本機 PostgreSQL 16 開發環境、連線驗證、lint/format/type-check/pytest 皆就緒。B/C/D 尚未開始(尚無任何 ORM model,`migrations/versions/` 為空,無 Scraper/Inference/寫入鏈路)。

### A. 專案基礎設施 — ✅ 完成

- [x] MES 專案獨立於 `/Users/cashflow/Documents/MES/`
- [x] 用 uv 建 Python 3.12 環境(`.python-version` = 3.12、`pyproject.toml` + `uv_build`)
- [x] 獨立 docker compose,跑 PostgreSQL 16(`compose.yaml`,named volume + healthcheck,與 EF_WorkFlow 生命週期解耦)
- [x] SQLAlchemy 2(Async)+ Alembic 建置:async engine(`create_async_engine` + asyncpg)/ `AsyncSession`(`async_sessionmaker`)/ async 連線測試已就緒(`src/mes/db/`、`migrations/env.py` 引用 `Base.metadata`、DB URL 由 `MES_DATABASE_URL` 供給)。Alembic migration 本質同步,env.py 內將 asyncpg URL 轉為 psycopg 供其連線。
- [x] pytest 測試骨架(`tests/test_database_connection.py` 實連 PostgreSQL 跑 `SELECT 1`,非 mock)
- [x] 裁剪確認:不建 FastAPI、不建 Dashboard、不建 AI 模組(相依中無 FastAPI;README §10 明列尚未實作範圍)

### B. 資料層(依 Entity / Observation / Knowledge Schema 三份文件)— ⏳ 未開始

- [ ] Entity 表(entity_id / entity_type / canonical_key / created_at)
- [ ] Observation_Log 表(Append-Only;完整欄位依 Observation Schema v1;entity_id 不可為空)
- [ ] Knowledge_State 表(物化視圖;source_observation_id 必填;主鍵 entity_id + feature)
- [ ] ReviewApp Signature Library(loox / judgeme / yotpo / okendo / stamped)
- [ ] Alembic migration 建立以上表
- [ ] Provenance 硬約束:寫入時 entity_id 為空即拒絕(測試涵蓋)

### C. 抓取與推論鏈路(依 Roadmap v8 五步)— ⏳ 未開始

- [ ] 1. Shopify App Store 評論區 Scraper:遵守 `apps.shopify.com` robots.txt 速率限制;5–25 秒隨機 `time.sleep`;抓 Loox 評論區 Store Name
- [ ] 2. Inference 引擎 Name→Domain:Store Name + 後綴關鍵字 → 網頁搜尋(第一版實作 DuckDuckGo 網頁解析,搜尋源為可替換零件)→ Regex 蒸餾第一筆 Domain
- [ ] 3. Normalize:Domain 轉小寫 / 去 scheme / 去 www / 去 trailing path / 去 port → canonical_key → 寫 Entity
- [ ] 4. Event Sourcing 寫入:先 append Observation_Log(entity_id 不可空)→ 再投影 Knowledge_State(無直接改 Knowledge_State 的後門)
- [ ] 5. 失敗三值語義:observed / fetch_failed / not_found,schema 層區分,失敗不偽裝

### D. 第一版 Feature 範圍(依 Feature Taxonomy v1,9 個)— ⏳ 未開始

- [ ] `uses_review_app`(entity_ref)
- [ ] `theme_name`
- [ ] `product_count`(/products.json)
- [ ] `avg_price`(/products.json)
- [ ] `price_range`(/products.json)
- [ ] `country`
- [ ] `language`
- [ ] `currency`
- [ ] `is_active`

> 註記:Inference 引擎第一版搜尋源(DuckDuckGo 網頁解析)的實際可行性,需在 M4 上實測確認;若不穩則換可替換零件,不動架構(呼應 P6 Provider Agnostic)。
> 暫不抓:Performance / Growth / Pain / Market(理由見 Taxonomy 文件的留白說明)。

**驗收標準:** 連續 7 天每天自動跑、有新增,且五 metadata 齊全、Append-Only 沒覆蓋、entity 歸屬正確。→ 不是「能抓」就成功,是「抓進來的資料乾淨且結構對」才成功。

**停止條件:** 出現「失敗被記成 0/無」、Update 覆蓋、metadata 缺漏 → 立即停修。

---

## Discovery — Status: Deferred(刻意不現在設計)

**狀態:** Deferred(刻意不現在設計)

**定義:** Discovery =「去哪找、找什麼樣的第一批觀測對象」。

**第一版寫死:** `Seed → Shopify Store`(不做通用 Discovery)。

**理由(為什麼 Deferred):** Discovery 的每條「去哪找」規則本質是**一個待驗證的假設(Hypothesis 性質)**,不是既定事實;沒有真實 Observation,Discovery 只能猜。所以 **Discovery 本身是 Evolution,不是 Architecture** —— 它要吃 Observation 當燃料才能演化。

**與 Cold Start 的接點:** Discovery 的職責 = 把使用者的「產品知識」轉成「第一批可驗證的市場 Observation」。

---

## Phase 2 — Knowledge Engine(Observation 正規化為可查詢 Knowledge)

**狀態:** 未開始

**目的:** 讓原始觀測變成可查詢、可算變化的中立知識層。

**進入條件:** Phase 1 通過 7 天乾淨累積驗收。

**工作項目:**

- [ ] 實作 Knowledge Engine:把 Observation_Log 投影成 Knowledge_State(非同步批次重算)
- [ ] Normalize:單位統一(幣別、時間格式)
- [ ] 實作取值邏輯(Default rule:status → 新鮮度 → confidence tiebreaker;country 覆寫為 confidence 優先)
- [ ] 守 P2:Normalize 不做任何判斷/評分(不標「高價值/高風險」)
- [ ] 支援查詢「某 Entity 的某 feature 隨時間的變化序列」
- [ ] 重建能力:可砍掉 Knowledge_State 全表並從 Observation_Log 完整重建(可回滾驗收條件)

**驗收標準:** 能查詢「某 Entity 的某 feature 隨時間的變化序列」→ 證明 Append-Only 歷史讀得出來,Growth 原料齊了。

**停止條件:** Normalize 混入判斷/評分 → 違反 P2,停;歷史查不出來 → Append-Only 沒生效,停。

---

## Phase 2.5 — Insight Engine(資訊降維 / 語義壓縮)

**狀態:** 未開始

**目的:** 把 Knowledge 濃縮成「描述看到了什麼」的 Insight。這是 **Describe**,不是 Predict。

**進入條件:** Phase 2 通過,Knowledge 層乾淨可作為輸入。

**工作項目:**

- [ ] 實作 Rule 實作者(例:`IF product_count > 500 → High SKU`)
- [ ] 實作 Statistics 實作者(例:最近 30 天 review 成長率 → Growth)
- [ ] 第一版刻意不用 AI(LLM 作為可插拔實作者,Phase 3 之後才加入)
- [ ] 每個 Insight 記 metadata:內容 / 產生者(rule_v1 / stat_v1)/ 基於哪些 Knowledge / 時間 / 信心
- [ ] 確保 Insight 中不混入任何「預測」(預測屬於 Hypothesis 層)

**驗收標準:** 純 Rule + Statistics 能對一批店穩定產出結構化 Insight,每個帶產生者與來源;Insight 中沒有混入任何預測。

**停止條件:** Insight 裡開始夾帶預測/賭注 → 停(Describe 與 Predict 混了)。

---

## Phase 3 — Hypothesis Engine(AI 進場做預測)

**狀態:** 未開始

**目的:** 第一次讓 AI 做「會死的預測」。AI 只扮演 Observation/Knowledge/Hypothesis 角色,不做 Decision。

**進入條件:** Phase 2.5 通過,Insight 層乾淨且可作為輸入。

**工作項目:**

- [ ] 產出結構化 Hypothesis(特徵組合 → 預測 + confidence + evidence),非散文
- [ ] 每條 Hypothesis 引用它基於哪個 Insight(Provenance)
- [ ] Jeff 審核流程:Approve / Reject / Comment,reject 記進 Decision Graph
- [ ] P5 第一版兌現:版本化 Model / Prompt / Hypothesis;Knowledge 用 timestamp;Crawler 掛 git hash
- [ ] 支援換模型(GPT ↔ Claude)讀同一份 Knowledge/Insight 各自產生假說(P4 地基)
- [ ] 可分別評估模型的觀察力(Insight)與推理力(Hypothesis)
- [ ] ⚠️ **待拍板:第一版「學習深度」** — 建議「只記錄 + 累積驗證次數」,confidence 先不自動裁決(守 P1 held);schema 預留「調信心度」與「長新假說」,第一版不開啟。**此項在 Roadmap v8 仍為待拍板,尚未定案。**

**驗收標準:** 假說結構化、帶 evidence、引用 Insight、可審核;reject 進 Decision Graph;換模型能各自產生假說(P4 地基成立);可分別評估觀察力與推理力。

**停止條件:** AI 把推論當事實寫進 Knowledge / 假說無 evidence 或不可審核 / AI 做 approve 以外的決策 → 停。

---

## Phase 3.5 — 接觸前置條件(平行進行,不阻擋 Phase 0–3)

**狀態:** 平行進行(Reddit 帳號養成:進行中)

**目的:** 為 Phase 4 的真實接觸準備合規、可收反饋的前置條件。

**進入條件:** 無(與 Phase 0–3 平行,不互相阻擋)。

**工作項目:**

- [ ] Reddit 帳號養成:真實參與累積 karma(進行中)
- [ ] Cold email 合規 + 獨立發信網域(若用 email 需獨立網域,絕不用 reviews@escapeflow.app)
- [ ] UTM → Shopify Partner API 歸因鏈:確認通的
- [ ] 本地部署備份 + 防駭(資產是無法重建的累積觀測,備份是地基等級必要)

> 商品化紅線(封存):當「系統對 EscapeFlow 真的有用、決定商品化」時,才討論資料搬遷與商家機密安全合規。在那之前不碰。

---

## Phase 4 — Experiment + Outcome(真實接觸,同步收反饋)

**狀態:** 未開始

**目的:** 真實接觸市場並同步收反饋,親手驗證「系統會學習」。

**進入條件:** Phase 3 通過 + Phase 3.5 至少一種合規、可收反饋的接觸行為就緒。

**工作項目:**

- [ ] Experiment 與 Outcome 一起做(沒有同步反饋機制,接觸資料就永遠丟失)
- [ ] 單變數原則:一次只測一個變數
- [ ] 行為是變數:六種接觸行為本身是被測對象
- [ ] 共同成效尺:不同行為都收斂到共同終點(UTM → 安裝);第一版只做「有反應 / 沒反應」
- [ ] 每個 Experiment 記錄(對應 P5):用了哪條 Hypothesis(版本)/ 哪個 Model+Prompt(版本)/ 讀的哪個時間點 Knowledge / 行為類型 / UTM / 假設
- [ ] 內容發出前 Jeff approve;高風險行為(cold email)押後,先用低風險行為起步
- [ ] 依武器庫優先序起步:1) App Store listing 優化(免費) 1) Build in Public + 內容 → 2) App Store 廣告(小錢測) → 3) Cold Email

**驗收標準:** 跑通一個完整循環:假設 → 行動 → Outcome → 綁回 Experiment 綁回 Hypothesis。哪怕只接觸十幾家、反饋很粗,只要「特徵→行為→結果」鏈完整就成功。

**停止條件:** Experiment 做了但 Outcome 收不到 / 一次動多變數 / 樣本不足卻下結論 → 停。

---

## Phase 5 — Evolution(用 Outcome 演化 Hypothesis)

**狀態:** 未開始

**目的:** 系統真正「因證據而改變」— 靈魂兌現。

**進入條件:** Phase 4 累積足夠 Outcome(每條假說達最低樣本量),此時解除 P1 held,市場裁決生效。

**工作項目:**

- [ ] 演化第一層:調信心度(Outcome 回來自動重算 confidence,達樣本量才生效)
- [ ] 演化第二層:長新假說(AI 分析為何失敗,催生更精細的新假說 Variation)
- [ ] 演化第三層:市場選擇(成功 Retention,失效冷藏/封存 Selection;不是修正,是迭代)
- [ ] 達爾文框架:Variation(求多樣)→ Selection(市場決定,P1)→ Retention(成功進 Knowledge/Hypothesis)
- [ ] 人類 reject = Decision 事件進 Decision Graph;未來 AI 再提同樣建議,系統能說「這曾被否決,要重檢嗎?」
- [ ] 全程可追溯(P5)+ 可回滾(Append-Only + Decision Graph)

**驗收標準:** 出現第一個「假說因新證據改變 confidence,並導致 Jeff 調整方向」的完整事件;全程可追溯 + 可回滾。

**停止條件:** 樣本不足就裁決生死 / 演化不可追溯或不可回滾 → 停。
