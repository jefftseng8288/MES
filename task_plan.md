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
| `docs/MES_Feature_Taxonomy_v2.md` | v1(2026-06-27 定稿) | Feature Taxonomy |
| `docs/MES_Build_vs_Buy_Matrix_v1.md` | v1(2026-07-03 定稿) | Build vs Buy Matrix |

**最後更新日期:** 2026-07-11

---

## 驗收(Acceptance)三態說明

> **工作做完 ≠ 驗收通過。**(呼應 CLAUDE.md:code 改完 ≠ 驗過。)每個 Phase 的「工作項目 checklist」只表示「事情做了」;是否達標由獨立的 **### ✅ 驗收(Acceptance)** 子區塊判定。
>
> **★ 驗收驗能力,不卡時間。** 驗收條件一律是「該 Phase 要證明的**能力**是否具備」(是非題),**不得**含「連續 N 天 / 穩定跑多久 / 規模累積到多少」這類時間・區間門檻。理由:任何有限區間都證明不了「持續」,而「持續」是系統存在就會做的常態,不是要被驗收的目標。詳見 `CLAUDE.md`。驗收狀態四態:

- **⬜ 未驗收:** 工作可能做完了,但能力驗收還沒檢驗。
- **🔄 驗收中:** 能力正在被檢驗,尚未有結論。
- **✅ 通過:** 能力已展示、達標。
- **❌ 未通過:** 已檢驗但能力未達標。

**鐵律:不可因為工作 checklist 勾滿就把驗收標成通過;但驗收判的是能力,不是跑了多久。**

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

### ✅ 驗收(Acceptance)

**狀態:** ✅ 通過

**驗收條件:**
- [x] 能拿一家真實的店,在紙上(不寫程式)把它的特徵手動填進 schema,無「不知道存哪欄」的卡頓(五份 schema 已定稿並落成 code,實作亦印證可填)

**停止條件:** Entity 關係沒定清 / Observation 邊界沒劃清 / 五 metadata 沒釘死 / Feature 還寫死在 schema → 都不進 Phase 1。

---

## Phase 1 — Crawler → Observation Log(乾淨、可追溯的市場 Observation)

**狀態:** ✅ A–E 全完成、驗收 ✅ 通過(A 基礎設施 / B 三表 / C 雙骨牌 / D feature 抓取 / E 排程)。baseline 與戳店面兩條鏈路作為背景常態持續運行。

**目的:** 能產生乾淨、中立、結構正確、掛在正確 Entity 上、可追溯的市場 Observation。

**進入條件:** Phase 0 五份定稿(✅ 已達成)。

> **★ 實況更新(2026-07-31):Phase 1 的兩條鏈路都在這天被實質修正,下方 2026-07-15 的敘述為當時狀態,現況以本則為準。**
> - **Seed 來源已從 5 個 review app 擴為 10 個**(加 klaviyo / smile / loyaltylion / seal_subscriptions / weglot 等「有規模才會裝」的類型);常數 `REVIEW_APP_HANDLES` → `SEED_SOURCE_HANDLES`。實測確認 App Store selector **跨 app 類型通用**。
> - **每家店的 feature 從 9 個增為 12 個**(Taxonomy v2 加 `review_count` / `avg_rating` / `rating_distribution`)。
> - **store-harvest 曾連續 16 天原地打轉**(head-of-line blocking:`ORDER BY created_at` + failed 永遠是候選),已改為「最久沒嘗試優先」+ `done` 納入候選(**這才讓時間序列成立**)+ 最小重抓間隔 7 天,批量 3 → 15。
> - **`MAX_PAGES` 12 → 2000 + 單批 5 小時煞車 + 觸頂主動回報** —— 先前兩次「來源枯竭」的判斷都是誤讀(實測各來源第 25 頁仍滿),根因是觸頂沒有訊號。
> - **防重入**:三個 baseline slot 共用 `asyncio.Lock`(`max_instances` 是 per-job,擋不住跨 slot)。
>
> **進度小結(更新於 2026-07-15):** A–E 全完成。基礎設施(uv / PostgreSQL 16 / async)+ 三張核心表(entity / observation_log / knowledge_state,Append-Only trigger + Provenance 雙層 + discriminated union value CHECK)+ 雙骨牌鏈路(Seed → inferred_domain)+ 五 app 擴池 + 批號 + 三批 baseline 排程與誠實健康報告 + Phase 1-D 戳店面抓 9 個市場 feature(獨立排程)皆已就緒並實測(`pytest` 70 passed、真連 DB + 真實實跑印證)。

### A. 專案基礎設施 — ✅ 完成

- [x] MES 專案獨立於 `/Users/cashflow/Documents/MES/`
- [x] 用 uv 建 Python 3.12 環境(`.python-version` = 3.12、`pyproject.toml` + `uv_build`)
- [x] 獨立 docker compose,跑 PostgreSQL 16(`compose.yaml`,named volume + healthcheck,與 EF_WorkFlow 生命週期解耦)
- [x] SQLAlchemy 2(Async)+ Alembic 建置:async engine(`create_async_engine` + asyncpg)/ `AsyncSession`(`async_sessionmaker`)/ async 連線測試已就緒(`src/mes/db/`、`migrations/env.py` 引用 `Base.metadata`、DB URL 由 `MES_DATABASE_URL` 供給)。Alembic migration 本質同步,env.py 內將 asyncpg URL 轉為 psycopg 供其連線。
- [x] pytest 測試骨架(`tests/test_database_connection.py` 實連 PostgreSQL 跑 `SELECT 1`,非 mock)
- [x] 裁剪確認:不建 FastAPI、不建 Dashboard、不建 AI 模組(相依中無 FastAPI;README §10 明列尚未實作範圍)

### B. 資料層(依 Entity / Observation / Knowledge Schema 三份文件)— ✅ 完成

- [x] Entity 表(entity_id UUID / entity_type VARCHAR+CHECK / canonical_key / created_at;unique on (entity_type, canonical_key))
- [x] Observation_Log 表(Append-Only;完整欄位依 Observation Schema v1;entity_id NOT NULL;DB 層 trigger 物理拒絕 UPDATE/DELETE)
- [x] Knowledge_State 表(物化視圖;source_observation_id NOT NULL;複合主鍵 (entity_id, feature);允許 UPDATE)
- [x] ReviewApp Signature Library(loox / judgeme / yotpo / okendo / stamped 以 entity_type='review_app' 種子寫入)
- [x] Alembic migration 建立以上表 + trigger + CHECK/UNIQUE 約束 + 種子(可完整 down/up 回滾,已實測)
- [x] Provenance 硬約束:observation_log.entity_id 為空即拒絕、knowledge_state.source_observation_id 為空即拒絕(ORM nullable=False + DB NOT NULL 雙層;測試涵蓋)

> **實測驗收(2026-07-11,含 value 欄修訂):** `pytest` 30 passed(真連 DB,非 mock)。已證明:三表建立、Append-Only 鎖拒絕 UPDATE/DELETE、Provenance NOT NULL 拒絕空值、knowledge_state 可正常 UPDATE、受控字串 CHECK 拒絕非法 status/confidence/value_type/source。migration down→up 回滾驗證通過。
>
> **value 欄改為 discriminated union(2026-07-11 修訂 f215450ec0a6,未新增 migration):** 原單欄 `value_raw`+`value_normalized` 改為 `value_type` + `value_raw`(feature 原始值原貌)+ typed 分欄 `value_text` / `value_number` / `value_boolean` / `value_json`(JSONB)/ `value_entity_id`,observation_log 與 knowledge_state 兩表同構(投影不做型別轉換)。加雙層 CHECK:(1) status↔value_raw(observed 需非空且 `btrim<>''`;failed/not_found 需 NULL);(2) status↔value_type↔typed 欄(observed 正好一個相符 typed 欄非空、其餘全空;failed/not_found 全空;value_type 於失敗時保留)。knowledge_state 版無 status 分支(只投影 observed)。逐分支測試涵蓋:5 種 value_type 正確組合可寫、錯誤組合/多欄並填被拒、value_raw 空/空白被拒、failed 帶值被拒。

### C. 抓取與推論鏈路(依 Roadmap v8 五步)— 🔄 骨架完成,寫入鏈路已實測

- [x] 1. Shopify App Store 評論區 Scraper(`src/mes/scrape.py`):遵守 robots.txt(`/reviews` 允許)、5–25 秒隨機 `time.sleep`;抓**五個 review app**(loox / judgeme / yotpo / okendo / stamped)評論頁 Store Name,handle 於 2026-07-15 實測(常數 2026-07-31 改名為 `SEED_SOURCE_HANDLES`,見下方更新)。selector 於 2026-07-11 對真實 HTML 實測(`data-merchant-review` 區塊 → `title` 屬性),各 app 通用,每頁 ~10 則
- [x] 2. Inference 引擎 Name→Domain(`src/mes/inference.py`):Store Name + "shopify store" → DuckDuckGo(`html.duckduckgo.com/html/`,可替換零件)→ regex 蒸餾 → 黑名單過濾取第一筆可信 domain。實測 5/5 命中(見 progress)
- [x] 3. Normalize(`src/mes/normalize.py`):domain 小寫/去 scheme/去 www/去 path/去 port → canonical_key → 寫 store entity;seed name 正規化 → `seed:` 前綴。收斂單一模組
- [x] 4. Event Sourcing 寫入(`src/mes/ingest.py`):雙骨牌先 append Observation_Log(entity_id 不可空)。Knowledge_State 投影屬 Phase 2,本階段不做
- [x] 5. 失敗三值語義:inference 結果 observed / fetch_failed / not_found 精確分流(fetch_failed=推論沒執行成功;not_found=執行了但搜不到可信 domain,≠店已死),寫入時失敗全欄 NULL、通過 CHECK
- [x] 自動排程能力已建置並驗證(見 E:daemon 常駐、無人看管自動跑)—— 持續運行是背景常態,不列為驗收門檻

> **實測驗收(2026-07-11):** `pytest` 43 passed(6 個 1-C 寫入鏈路 + producer/source CHECK 測試,真連 DB)。live run 對真實店跑 DuckDuckGo,observed / fetch_failed / not_found 三種狀態均實際出現(連續查詢後 DDG 限流→fetch_failed,誠實記錄)。
>
> **schema 細化(2026-07-11,版號不動,已物理落地 DB):** (1) source 受控清單加 `web_search`(inferred_domain 不再假記為 html_page);(2) 新增 `producer` 欄(observation_log + knowledge_state,NOT NULL + CHECK,三值 mes_crawler_v1 / duckduckgo_v1 / manual_v1);(3) crawler_version 歸位為純 git hash(不再塞 duckduckgo_v1)。三欄分工:source=管道 / producer=方法模型 / crawler_version=程式碼版本。新增 migration `d9eb673e28aa`(down→up 於空表回滾通過)。dev 資料選擇清空重跑(TRUNCATE 繞過 Append-Only trigger → 完整鏈路重建 → 重跑 live)。

### E. 撈取排程(一天三批)+ 撈取健康報告 + 批號 — ✅ 完成(自動排程 + 誠實健康報告能力)

- [x] 排程器(`src/mes/schedule.py`,APScheduler,**一天三批 02:00 / 10:00 / 21:00 台灣**,`CronTrigger(hour="2,10,21", timezone="Asia/Taipei")` 明確帶時區;`max_instances=1`/`coalesce`;`--once` 手動觸發)
- [x] 時區修正:trigger 明確 `Asia/Taipei`(不依賴系統時區繼承);已驗證三批觸發 = 台灣 02:00/10:00/21:00
- [x] 每批 = 30 筆未撈過的 Seed,**跨五個 review app 輪詢**(loox/judgeme/yotpo/okendo/stamped,round-robin by page 分散負載+放大供給;loox 單獨第二天即抽乾)沿用既有雙骨牌鏈路,核心未動;三批分散(8h/11h/5h)測「一天總量(90 次 DDG)」而非短時爆量
- [x] 節流:每筆之間 **20–150 秒隨機** sleep(隨機為硬性要求;跨度 130 秒拉寬);保守起點,待真實負載回饋調整。30×~85s ≈ 42 分鐘/批
- [x] **批號 `batch_id`(observation_log,NOT NULL + 格式 CHECK)**:格式 `YYYY-MM-DD-NN`;**NN 固定語義:-01/-02/-03 = 三個排程時段(02:00/10:00/21:00 台灣,scheduler 傳 slot),-04+ = 手動;同時段重跑沿用同批號**;只加 observation_log、不加 knowledge_state;既有資料以 migration 依 `observed_at`(台灣日期 + >10min 分群)回填,不清除(繞過 Append-Only trigger)
- [x] Seed 去重仍生效:只取未撈過的新 Store Name;供給不足如實回報(`actual < requested`)
- [x] 撈取健康報告**按批號**:每批印出 + 寫入 `logs/harvest_health.log`,**三比例分開**(observed / not_found / fetch_failed),不合併;判讀標明 fetch_failed 為主儀表,並提示**比較同日越晚的批 fetch_failed 是否越高**(測一天總量的累積限流);`compute_health_for_batch` 供回看
- [x] 排程 daemon 常駐、無人看管自動跑(launchd LaunchAgent 已上線)—— 持續運行是背景常態,非驗收目標
- [x] 第一版不做自動告警/自動退避(先累積經驗,規則成熟再自動化)

> **實測(2026-07-14):** `pytest` **56 passed**(含 batch_id NOT NULL/格式 CHECK、按批號報告);ruff/mypy 綠;migration down→up 回滾通過、Append-Only trigger 回填後已復原。三批觸發時間驗證 = 台灣 02:00/10:00/21:00。daemon 已重載跑新 code(今晚 21:00 台灣自然跑第一個三批)。**未硬跑整批**(DDG 狀態未知,讓它按排程自然跑)。
> **既有真實批(2026-07-14-01,即上一輪 02:00 台灣那批):** 30 筆 · observed 29(97%)· not_found 1 · fetch_failed 0 —— 20–150s 節奏、冷卻後、零限流,證明生產間隔可行。**註:observed 是「有沒有被限流」的健康指標,非「domain 抓對」;該批攤開約半數 domain 其實抓錯(shop.app 等),精確度屬 inferred_domain 元特徵未來評估,不在此報告範圍。**
> **暫定值提醒:** batch size=30 / 間隔 20–150 / 一天三批,皆為**待真實負載修正的暫定值**,非已驗證安全基準;一天三批就是要用真實 fetch_failed 測「一天總量」對 DDG 的累積效應。

### D. 第一版 Feature 範圍(依 Feature Taxonomy v1,9 個)— ✅ Phase 1-D 完成(獨立戳店面鏈路)

抓取架構(`src/mes/harvest.py`):**與 baseline DDG 鏈路分離、獨立排程**(每 3h,戳各店自己伺服器,限流獨立)。讀「有 domain、待抓」的 store → 戳 products.json + 首頁 HTML → 寫 9 feature(掛 store entity)。producer=`mes_store_crawler_v1`。三值/confidence 逐 feature 誠實分流。

- [x] `product_count`(products.json,翻頁至 <250;超上限 → estimated)· value_number
- [x] `avg_price`(variants price 平均)· value_number
- [x] `price_range`(min/max)· value_json
- [x] `currency`(首頁 `Shopify.currency`,**非** products.json;source=html_page)· value_text
- [x] `is_active`(有商品 true / 空店・鎖店 false 皆 observed;連不上才 fetch_failed)· value_boolean
- [x] `theme_name`(首頁 `Shopify.theme.name`)· value_text
- [x] `country`(首頁 `Shopify.country`)· value_text
- [x] `language`(首頁 `Shopify.locale`)· value_text
- [x] `uses_review_app`(首頁 script 特徵比對五 app → review_app entity_ref;**confidence=inferred**,沒命中→not_found)· value_entity_id
- [x] 狀態標記:獨立表 `store_harvest_state`(pending/done/failed,可 UPDATE;與 entity 純淨/Append-Only 分離)
- [x] 三值分流 + 雙層 value CHECK 通過;每筆 Provenance 完整(producer/source/crawler_version/batch_id)

> **實測(2026-07-15):** `pytest` 70 passed(+11 harvest:解析三值 / 寫入 CHECK / 狀態流轉)。小規模實跑 3 家真實店:flated.co.nz(NZ/NZD)9/9、vaniabath.com 9/9、centricoffee.com 8/9(uses_review_app 誠實 not_found,無我們五個 app)。products.json 與 Shopify.* 變數結構如預期,未被擋。批號用 -04+(手動範圍,與 baseline -01/-02/-03 區隔,再由 producer/feature 區分)。**暫定值:每批 1–3 家、每 3h(≈8 批/日),待戳店面實況回饋調整。**

> 註記:Inference 引擎第一版搜尋源(DuckDuckGo 網頁解析)的實際可行性,需在 M4 上實測確認;若不穩則換可替換零件,不動架構(呼應 P6 Provider Agnostic)。
> 暫不抓:Performance / Growth / Pain / Market(理由見 Taxonomy 文件的留白說明)。

### F. 警鈴 — 主動回報 + 原因診斷(痛覺神經)— ✅ 完成

系統從「被動哑巴」→「會叫痛」。**只做主動回報 + 初步診斷,不做任何自動調整/退避/加來源**(規則還在猜,先累積「異常+原因」當未來自動化的燃料)。

- [x] 獨立程序(`src/mes/alarm.py`),每天 **23:50 台灣**跑一次(launchd `com.mes.alarm`,StartCalendarInterval 本地時間,一次性;**刻意獨立於 harvest daemon** —— 那個死了警鈴才能報「批次執行異常」)。不跨日(已知漏報、接受)
- [x] 三警鈴(門檻暫定,待實況調):(1) 連續兩批新 Seed < 10 → 供給不足;(2) 連續兩批 fetch_failed > 15 → 疑似限流;(3) 任一批 observed = 0 → 單批即觸發、**必帶原因診斷**
- [x] **原因診斷(核心)**:用既有資料(三值組成 / 供給 / 執行狀況)判讀最可能原因,跟警報一起推。0 observed 分辨:fetch_failed 佔滿→限流 / not_found 佔滿→市場搜不到 / 無新 Seed→池子乾 / 批次無記錄→執行異常
- [x] 結構化記錄 `alert_log`(時間 / 類型 / 診斷 / 當天三批數據 JSONB;alert_type 不 CHECK 鎖利擴充)—— 未來自動調整要學的燃料
- [x] Telegram 推播(`src/mes/notify.py`,`MES_TELEGRAM_BOT_TOKEN`/`CHAT_ID`);**只在有異常時推、正常安靜**;多警鈴合併一則不洗版。**⚠️ 待 Jeff 提供 bot token + chat_id 才能實際送達**(缺憑證則只記 DB、不推)

> **實測(2026-07-17):** `pytest` 80 passed(+10 alarm:三警鈴觸發 / 連續 vs 非連續 / 0-observed 四種診斷分辨 / DB 記錄 / 只異常才推 / 無憑證 no-op)。真實資料驗證:7/15、7/16(健康日)正確**安靜不誤報**、三批讀取準確。四個異常情境的 Telegram 訊息格式與診斷已驗(含多警鈴合併)。門檻 <10 / >15 為暫定起點,標在 code 註解 + findings。

### ✅ 驗收(Acceptance)

**狀態:** ✅ **通過**(能力導向:Phase 1 要證明的能力已具備)

> Phase 1 的能力 = **「能把產品知識(Seed)轉成帶完整 Provenance、結構正確、掛在正確 Entity 上的可驗證市場 Observation」**。此能力已由 A(基礎設施)/ B(資料層)/ C(雙骨牌鏈路)/ E(自動排程 + 誠實健康報告)/ D(戳店面抓 9 feature)共同展示並經測試(`pytest` 70 passed、真連 DB)+ 真實實跑印證。依「驗收驗能力,不卡時間」:能力已達成即通過,不設「連續 N 天 / 規模到 N 家」門檻。

**驗收條件(能力,全部達成):**
- [x] 能把 Seed 轉成帶完整 Provenance 的 Observation(雙骨牌:store_name_seed → inferred_domain;五 metadata + entity 歸屬齊全)
- [x] 失敗誠實不偽裝(三值語義 observed / fetch_failed / not_found,寫入層 CHECK 強制)
- [x] Append-Only 沒覆蓋(DB trigger 物理鎖;再觀測=新增帶新 timestamp 的一筆)
- [x] 撞真實世界(DDG 限流、Loox 種子池乾)系統能**誠實反應**,且可透過**加來源持續運行**(已由五 app 擴池、健康報告三比例分開驗證)
- [x] 能自動、無人看管地運行(排程 daemon 常駐)—— 持續運行本身是背景常態,不列為門檻

> 判準:驗的是「能不能穩定產生乾淨、結構對、掛對 entity、可追溯的 Observation,且失敗誠實」——這些能力已具備。規模與天數是持續運行的自然結果,不是驗收條件。

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

**狀態:** ✅ 能力達成(第一批 schema+CHECK、第二批投影引擎/取值/country/時序/排程/純函數重建皆完成,2026-07-19)。「從無 observed 不投影無值列」已由 Jeff 定案(保 Provenance 鐵律;查無此列 → 去 observation_log 看失敗歷史)

**目的:** 把流水帳般的 Observation 收斂成「誠實反映流動現況」的、中立的、可查歷史的 Knowledge 層。

**進入條件:** Phase 1 通過驗收(能力已具備:能穩定產生乾淨、結構正確、掛對 entity、可追溯的 Observation)。

**設計文件:** `docs/MES_Phase2_Knowledge_Engine.md`(Phase 2 的可實作設計,與 Phase 0 五份 schema 同級)。

**三個定案決定(Phase 2 的靈魂,Jeff 定案):**

- **決定 1 —— 取值「時間(新鮮度)優先於信心度」。** 同一 (entity, feature) 有多筆 observed 時取最新那筆,即使 confidence 較低(新 inferred)也優先於較舊但較高(舊 certain)。理由:資料是流動的,寧要「最新、帶推論成分的現況」,不要「確定、但已入土的歷史」(基於過期事實做動作會白費且造成歸因困境)。取值序:`status(只取 observed)→ observed_at 最新 → confidence(僅同時間 tiebreaker)`。
- **決定 2 —— fetch_failed 保留舊值,但誠實標明新鮮度與當前狀態。** fetch_failed 是「系統失能」(被擋/斷網),不代表商家變了;若一次失敗就抹去舊值,狀態會隨網路波動閃爍歸零。做法:保留上次 observed 的值,同步更新 `last_observed_at`(上次成功觀測時間)+ `current_status`(最近一次嘗試的結果)。
- **決定 3 —— 投影時機 = 定時批次,每天 23:30 台灣(警鈴 23:50 之前)。** 守「拒絕不必要的即時性」:一天三批的量不需即時投影,也不搞增量觸發的複雜架構。先投影(23:30)收斂當天 observation,再警鈴(23:50)體檢。

**工作項目:**

*A. knowledge_state schema 擴充(由決定 2 導出;趁空表擴充成本近零)* — ✅ 第一批完成(2026-07-19)

- [x] knowledge_state 加 `last_observed_at`(timestamptz,nullable)+ `current_status`(String(16),NOT NULL,受控三值 CHECK)。migration `5c7e8387b736`(空表 TRUNCATE 後擴充;down→up 回滾已驗)
- [x] knowledge_state **DB 層 CHECK**(物理鎖死合法狀態組合,不信任投影代碼):
  - `last_observed_at IS NULL` → value 必 NULL(所有 typed 欄 + value_raw 皆 NULL)、`current_status` 只能 fetch_failed / not_found(防「從沒成功卻有值」的鬼值)—— 既有無條件 value CHECK 改為受 last_observed_at 條件化
  - `last_observed_at IS NOT NULL` → value 必非 NULL、`current_status` 任意(防「曾成功卻沒值」的空洞)
- [x] 同步更新 `docs/MES_Knowledge_Schema_v1.md`(加兩欄 + 語義 + 決定 2 理由 + 兩條 CHECK 規則);§3 country 舊述已於前批(commit be6280c)修正為決定 1 + country 特例
- [x] 測試(真連 DB):7 個 CHECK 測試(規則 1/2 各違反被拒 + 合法可寫 + current_status 受控);既有 knowledge 測試更新 `_ks`(加兩欄)後全過

> **註(設計 vs 本地 schema 差異,已回報):** `last_observed_at` 語義與 knowledge_state 既有 `observed_at` 欄(「該來源觀測的時間」)**重疊**——當前設計下兩者都等於「被取為當前值那筆 observed 的時間」。依 Phase 2 設計文件先加 `last_observed_at`;是否與 `observed_at` 合併,留待第二批(投影引擎)一併釐清。

*B. 投影引擎(Knowledge Engine 核心)* — ✅ 第二批完成(2026-07-19,`src/mes/knowledge.py`)

- [x] 實作投影(全量重算,Jeff 定案):讀 observation_log 全部 → 清空 knowledge_state → 逐 `(entity_id, feature)` 投影 → 寫入(`rebuild_knowledge_state`)。真實資料 3151 (entity,feature) 組投影出對應列
- [x] 取值邏輯(決定 1、2):observed 子集取 observed_at 最新(同時間 confidence tiebreaker → observation_id 收尾);`current_status` = **全部觀測**(含失敗)最新那筆 status(與 value 掃**不同子集**)。決定 2 場景於真實資料成立(2 列 value 保留 + current_status=fetch_failed)
- [x] **country 特例(Jeff 定案 B 版):時間序 fold,新值 confidence ≥ 現行才覆蓋**(新 inferred 擋不住舊 certain)。`selection_rule_version=country_v1`(14 列),其餘 `default_v1`。**第一版只套 country**
- [x] value 容器直接投影(同構,不做型別轉換);Normalize 於觀測層已做,投影**只取值不判斷**
- [x] 守 P2 中立(鐵律):knowledge_state 無任何評分/排序/判斷欄(測試 `test_no_scoring_columns` 守門)
- [x] **合併第一批冗餘欄:** 投影驗證 `last_observed_at ≡ observed_at`(2905 列 0 不符)→ 移除 `last_observed_at`,CHECK 改綁 `observed_at`(migration `e8d6f05d71b0`)
- [x] **「從無 observed」不投影列(Jeff 定案 2026-07-19):** 只有失敗、從無成功觀測的組合 → knowledge_state **查無此列**(而非投影無值列)。理由:無值列會逼 observed_at / source_observation_id 等 Provenance NOT NULL 欄放寬(動鐵律),**保鐵律優先**;規則 1 CHECK 續當防禦守門。要知道「為何沒值 / 試過幾次」→ 查 observation_log(那裡誠實記著所有失敗嘗試)

*C. 投影排程(依決定 3)* — ✅ 完成

- [x] 定時批次每天 **23:30 台灣**,獨立 daemon `deploy/com.mes.projection.plist`(launchd,**獨立於 harvest / alarm**,one-shot)
- [x] 順序:先投影(23:30)→ 再警鈴(23:50)

*D. 時間序列查詢* — ✅ 完成

- [x] `feature_history(entity, feature)`:直讀 observation_log(Append-Only 全歷史)的 observed 筆依 observed_at 排序;當前值查 knowledge_state、歷史查 observation_log

*E. 重建能力 + 純函數性* — ✅ 完成

- [x] 全量投影 = 砍表重建,重建與日常投影**同一套邏輯**;連跑兩次 knowledge_state 完全一致(`test_rebuild_idempotent_and_projects_expected`)
- [x] **純函數重建:投影禁用 `now()` / `CURRENT_TIMESTAMP`**;所有寫入的時間維度(`observed_at`、`updated_at`)100% 由 observation_log 的 `observed_at` 投影而來,tiebreaker 用 `observation_id` 收尾 → 冪等

> **效能備註(非工作項):** 現階段規模(Shopify 全站據所知未破百萬,knowledge_state 頂多幾十萬列)本地 PostgreSQL 全表掃即毫秒級,**不預先加索引**(過度優化)。若未來投影**實測**變慢,第一順位候選 = observation_log 的 `(entity_id, feature, observed_at)` 複合索引(最大表、投影分群挑最新值最高頻打的地方)。knowledge_state 的 (entity_id, feature) 複合主鍵已自帶唯一索引。

### ✅ 驗收(Acceptance)

**狀態:** ✅ 能力達成(2026-07-19,第二批完成;97 測試全過)。「從無 observed → 不投影無值列」已由 Jeff 定案(保 Provenance 鐵律;見 B 組)。

**驗收條件(能力導向)—— 逐條對照實際測試:**

- [x] 能對一個 (entity, feature) 依「時間優先」取出當前值(決定 1)—— `test_time_priority_new_inferred_beats_old_certain` / `test_same_time_confidence_tiebreaker`
- [x] fetch_failed 時保留舊值 + 誠實標明 observed_at(新鮮度)/ current_status(決定 2)—— `test_decision2_fetch_failed_keeps_old_value`;真實資料 2 列成立
- [x] DB CHECK 物理拒絕不老實的混合狀態(observed_at IS NULL 卻有 value、observed_at 有值卻無 value 等 → 被擋)—— `test_phase2_knowledge_state.py` 全數
- [x] country 特例生效:低 confidence 的新 inferred 不覆蓋高 confidence 的舊 certain —— `test_country_new_inferred_does_not_override_old_certain` / `test_country_new_certain_overrides_old_certain`
- [x] 能查詢某 entity 某 feature 隨時間的變化序列(Append-Only 歷史)—— `test_feature_history_sorted`
- [x] 可砍掉 knowledge_state 全表並從 observation_log 完整重建,**且重建後與砍之前完全一致**(純函數;禁用系統時間)—— `test_rebuild_idempotent_and_projects_expected`
- [x] 投影全程中立,無任何判斷/評分(守 P2)—— `test_no_scoring_columns`

**停止條件:** Normalize 混入判斷/評分 → 違反 P2,停;歷史查不出來 → Append-Only 沒生效,停;fetch_failed 抹去舊值(狀態閃爍歸零)→ 違反決定 2,停;重建結果隨執行時間浮動(用了系統時間)→ 違反純函數性,停。

---

## Phase 2.5 — Insight Engine(資訊降維 / 語義壓縮)

**狀態:** ✅ 能力達成(第一批 insight_store + registry、第二批 InsightEngine / 兩個 Producer / 執行報告表 / 23:40 排程皆完成,2026-07-19)
> **⚠️ `GROWTH_VELOCITY` 現況(2026-07-31 更新):資料源已開,但還缺第二個時間點。** `review_count` 已納入採集(Taxonomy v2 + loox handler,真實觀測 6 家),故**不再是「源頭沒開」**;但成長率需要**同一家店相隔約 30 天的兩個觀測點**,而 harvest 最小重抓間隔是 7 天 → **最快約一個月後**才會有第一筆真實成長率。**這次是真的「還沒累積夠」,與先前的「源頭沒開」性質不同** —— 前者會自己好,後者不會(判準見 findings)。

**目的:** 把 Knowledge 濃縮成「描述看到了什麼」的 Insight。這是 **Describe**,不是 Predict。

**進入條件:** Phase 2 通過,Knowledge 層乾淨可作為輸入。

**設計文件:** `docs/MES_Phase2.5_Insight_Engine.md`(實作前準備:資料模型、value 受控方式、Pipeline Plugins 架構、23:40 排程、描述vs預測界線判準)。

**工作項目(骨架 —— 概念層,保留):**

- [ ] 實作 Rule 實作者(例:`IF product_count > 500 → High SKU`)
- [ ] 實作 Statistics 實作者(例:最近 30 天 review 成長率 → Growth)
- [ ] 第一版刻意不用 AI(LLM 作為可插拔實作者,Phase 3 之後才加入)
- [ ] 每個 Insight 記 metadata:內容 / 產生者(rule_v1 / stat_v1)/ 基於哪些 Knowledge / 時間 / 信心
- [ ] 確保 Insight 中不混入任何「預測」(預測屬於 Hypothesis 層)

**實作層工作項(依設計文件補;分批建議:A = 第一批,B/C = 第二批):**

*A. Insight 資料模型 —— 新表 `insight_store`(完全獨立於 knowledge_state,絕不混)* — ✅ 第一批完成(2026-07-19,migration `aa0151f18e2d`)

- [x] 新表 `insight_store`:`insight_id`(UUID PK)/ `entity_id`(NOT NULL,FK → entity)/ `insight_type`(VARCHAR(50) NOT NULL,如 `SKU_SCALE` / `GROWTH_VELOCITY`)/ `value_text`(VARCHAR(255) NOT NULL,標籤如 `High SKU`)/ `producer`(VARCHAR(50) NOT NULL,如 `rule_v1` / `stat_v1`)/ `confidence`(VARCHAR(20) NOT NULL,沿用既有離散三級 + DB CHECK)/ `generated_at`(TIMESTAMPTZ NOT NULL)/ `source_knowledge_refs`(JSONB NOT NULL)
- [x] 一對多 + 一 entity 可多列(一家店可同時 High SKU + Growth,每 insight 一列);主鍵用 insight_id,不是 (entity_id, feature)
- [x] **`(entity_id, insight_type)` UNIQUE 約束(Jeff 定案):** 與 knowledge_state 的 (entity_id, feature) 主鍵同構 —— 一 entity 的每個 insight 維度只有一個當前值。第二批全量重算走**依此鍵 upsert**(非清空重寫)→ `insight_id` 穩定不每天重生成,Phase 3 的 Hypothesis 才引用得住
- [x] **`generated_at` 用 `now()`(執行時間),不違反 Phase 2 禁用系統時間 —— 語義不同:** knowledge_state 的 `observed_at` = 「這個**事實**何時被觀測」(歷史事實,必由 observation_log 投影);insight_store 的 `generated_at` = 「這個**描述**何時被產生」(本來就是執行時間)。已知且接受:insight_store **不是**冪等重建的(真相在 observation_log,insight 只是每天重新描述一次的快照)
- [x] **value_text 受控,第一版用「應用層驗證 + 集中定義」,不下沉 DB CHECK** —— 理由:insight_type / 標籤還在快速演化,DB 硬鎖太早(每加標籤改 migration,且受控清單只能往前加、難往後收,Phase 1-C 踩過)。判準:**用「穩不穩定 / 會不會頻繁改」決定受控放 DB 還是應用層**。落地:`src/mes/insight_registry.py`(registry + `validate_insight_value()` 守門,不合法明確報錯);`insight_type` / `value_text` / `producer` 皆無 DB CHECK,`confidence`(Phase 0 既定、穩定)有 DB CHECK
- [x] registry 設計成「第二批 Producer 可登記進來」的形狀(`register_insight_type()`;同維度不同值集合 → 擋);**第一批只建機制不填內容**,只留設計文件明載的 `SKU_SCALE` 一個示範登記
- [x] `source_knowledge_refs`(Provenance):記一組 `(entity_id, feature)` 即可,**第一版不追求完全重現當時的確切值**(insight 有 generated_at、每天全量重算,精確重現非必要需求)。精神同 observation_log 的 source_observation_id:讓 insight 可追溯

*B. 架構 —— Pipeline Plugins(常駐 Orchestrator 定時批次調用)* — ✅ 第二批完成(2026-07-19)

- [x] `InsightEngine`(`src/mes/insight.py`)+ `BaseInsightProducer`(`insight_producers.py`);**實作者 = 純函數類別,不自己撈 DB**
- [x] **Producer 純函數但需歷史 → 由 Engine 統一撈:** Producer 只**聲明**需要什麼(`required_features` / `required_history`),Engine 撈齊打包成記憶體 `InsightContext`;Producer 只看記憶體物件、不碰 DB(`produce(ctx)` 唯一參數就是 Context)
- [x] 頭兩個實作者:`SKURuleProducer`(≤100 Low / 101–500 Medium / >500 High,連續無縫)、`GrowthStatProducer`(用 Phase 2 時間序列算 30 天成長率,**數值型、刻意不設門檻**)
- [x] **registry 擴充:列舉型 + 數值型兩種 insight_type**;`validate_insight_value()` 依種類分派,未登記一律擋
- [x] **producer 欄補上應用層受控**(第一批缺口):各 Producer 類別透過 `__init_subclass__` 自己聲明、registry 統一收攏,寫入前 `validate_producer()` 守門;仍不下沉 DB CHECK
- [x] 批次 **upsert** 依 `(entity_id, insight_type)` → **insight_id 穩定不重生成**(Phase 3 引用得住);加新 Producer 只需加一個類別丟進 `DEFAULT_PRODUCERS`,不動 Engine(連 registry 登記都自動)
- [x] **執行報告表 `insight_run_log`**(migration `7c7cff956f83`):記錄「某 entity 的某 insight_type **為什麼沒產出**」,原因具體載明缺什麼 + JSONB 結構化細節;不塞進 insight_store(避免把系統計算狀態混進市場描述)。**只做記錄,停止觀察的決策機制不在 Phase 2.5 做**
- [x] **Engine 只處理 `store` entity**(實作決定,待 Jeff 確認):seed / review_app 依定義無市場特徵,為其記 skip 是類別錯誤且會埋掉真訊號(實測 5918 → 964 筆,未損失任何真實產出)

*C. 排程 —— 每天 23:40 台灣獨立 daemon 全量重算* — ✅ 完成

- [x] 獨立 daemon `deploy/com.mes.insight.plist`(launchd one-shot,**獨立於 harvest / alarm / projection**),每天 23:40 台灣全量重算
- [x] 每日處理鏈:Knowledge 投影(23:30)→ Insight 壓縮(23:40)→ 警鈴(23:50)

*D. 描述 vs 預測界線(實作紅線,寫進實作紀律)* — ✅ 判準落地(borderline 的 Highly Dependent Producer 尚未實作,非本批範圍)

- [x] **紅線判準(可操作):** 只要出現「下個月 / 即將 / 應該會」等隱含未來時間軸 + 帶賭注性質的推論 → 是預測 → **立即停修**(屬 Phase 3 Hypothesis)。已寫進 `insight_producers.py` / `insight.py` 模組 docstring 作為實作紀律;測試 `test_no_prediction_columns_in_insight_store` 守門
- [ ] **borderline:** `Highly Dependent`(如連續半年 uses_review_app='loox')**可作 Insight**(對歷史流水帳的描述壓縮);但引申「因為依賴,所以短期內不會換 App」= 預測 → **砍**。關鍵:同一事實,描述它是 Insight、從它引申未來是 Hypothesis

### ✅ 驗收(Acceptance)

**狀態:** ✅ 能力達成(2026-07-19,第二批完成;143 測試全過。真實資料實跑:758 家 store → 548 筆 SKU_SCALE)
> **⚠️ `GROWTH_VELOCITY` 現況(2026-07-31 更新):資料源已開,等第二個時間點。** `review_count` 已納入採集,真實產出仍為 0 —— 但原因已從「源頭沒開(永遠不會有)」變成「**還沒累積夠(會自己好)**」:需同店相隔約 30 天兩點,最小重抓間隔 7 天 → 約一個月後可得。

**驗收條件(能力導向)—— 逐條對照實際測試:**
- [x] 純 Rule + Statistics 能對一批店穩定產出結構化 Insight,每個帶產生者(producer)與來源(source_knowledge_refs)—— **Rule:** 真實資料 548 筆 SKU_SCALE(547 Low / 1 Medium),每筆帶 `rule_v1` + refs;**Statistics:** 計算能力以測試資料驗證通過;真實產出仍為 0,但**資料源已於 2026-07-31 開通**(review_count 已採集),等第二個時間點累積(見下)
- [x] Insight 中沒有混入任何預測 —— `test_no_prediction_columns_in_insight_store`;Producer 只做當前事實/歷史的描述壓縮
- [x] value_text 受控一致 —— 列舉型擋 `high_sku`/`HIGH SKU`;數值型擋非數值(`test_numeric_type_accepts_numbers_rejects_labels`)
- [x] source_knowledge_refs 記錄基於哪幾條 (entity_id, feature) 事實 —— 真實資料與測試皆驗證
- [x] **producer 受控** —— `rule_V1` / `ruleV1` 等未登記寫法被擋(`test_producer_controlled`)
- [x] **upsert 穩定** —— 連跑兩次不重複建列、`insight_id` 不變(`test_engine_produces_and_upserts_stably`)
- [x] **Producer 純函數** —— 同一 Context 輸出恆定、`produce` 不收 session(`test_producers_are_pure_*`)
- [x] **可插拔** —— 新增假 Producer 丟進 List 即生效,不動 Engine(`test_engine_pluggable_new_producer`)
- [x] **未產出有具體原因可查** —— `insight_run_log` 載明缺什麼(如「僅 1 筆 observed」「跨度僅 12 天」)

> **⚠️ GROWTH_VELOCITY 對真實資料尚無產出(非 bug,但原因與預期不同):** `review_count` **不是 MES 目前採集的 feature**(不在 Feature Taxonomy v1 的 9 個市場特徵內、Phase 1-D 沒抓),真實觀測數為 **0** —— 故不是「資料跨度不足 30 天」,而是**該 feature 從未被採集**。能力已用造的測試資料完整驗證(成長率計算、25–35 天容忍窗邊界、資料不足各分支)。要讓它對真實資料生效,需先把 `review_count` 納入採集範圍(屬 Feature Taxonomy / Phase 1-D 範疇,待 Jeff 定)。

**停止條件:** Insight 裡開始夾帶預測/賭注 → 停(Describe 與 Predict 混了)。

---

## Phase 3 — Hypothesis Engine(AI 進場做預測)

**狀態:** 🔄 進行中(第一批「資料層地基」完成:hypothesis / decision 表 + predicate registry + pattern 查詢;第二批「LLMProvider / Pattern 聚合 / 假說生成 / 審核流程」未做)

**目的:** 第一次讓 AI 做「會死的預測」。AI 只扮演 Observation/Knowledge/Hypothesis 角色,不做 Decision。

**進入條件:** Phase 2.5 通過,Insight 層乾淨且可作為輸入。

**設計文件:** `docs/MES_Phase3_Hypothesis_Engine.md`(實作前準備:雙層資料模型、Pattern 粒度、Decision Graph 分界、LLMProvider 抽象、聚合後餵 LLM)。

> **★ Phase 3 驗的是「假說的形狀」,不是「假說對不對」。** 證偽發生在 Phase 4 —— 沒有 Outcome 之前「準不準」根本無法評估,驗收條件裡因此**沒有任何「假說要準」的要求**。
> **實作紀律:不要不自覺地想「怎麼讓 AI 產出更準的假說」** —— 那個問題在 Phase 4 之前無解,現在追求它只會變成憑感覺調 prompt(而「感覺更好」不是證據)。
>
> **開工前提:輸入貧乏是已知且接受的(Jeff 定)。** `insight_store` 目前只有 `SKU_SCALE`;`GROWTH_VELOCITY` 約一個月後才有真實資料 → 第一批假說會單薄。**但這不構成延後的理由** ——「豐富」沒有定義,以它為前提等於無限期拖延;真正能回答「資料夠不夠」的是 Phase 4 的 Outcome。**能力先建好,資料長出來自然變好。**

**工作項目(骨架 —— 概念層,保留):**

- [ ] 產出結構化 Hypothesis(特徵組合 → 預測 + confidence + evidence),非散文
- [ ] 每條 Hypothesis 引用它基於哪個 Insight(Provenance)
- [ ] Jeff 審核流程:Approve / Reject / Comment,reject 記進 Decision Graph
- [ ] P5 第一版兌現:版本化 Model / Prompt / Hypothesis;Knowledge 用 timestamp;Crawler 掛 git hash
- [ ] 支援換模型(GPT ↔ Claude)讀同一份 Knowledge/Insight 各自產生假說(P4 地基)
- [ ] 可分別評估模型的觀察力(Insight)與推理力(Hypothesis)
- [x] ~~⚠️ 待拍板:第一版「學習深度」~~ → **✅ Jeff 定案(2026-08-03)**:第一版**只記錄 + 累積驗證次數**,confidence **不自動裁決**(守 P1 held);schema **預留**「調信心度」與「長新假說」,**第一版不開啟**。理由:現在連一個 Outcome 都沒有,自動裁決沒有燃料。

**實作層工作項(依設計文件補;分批建議:A = 第一批,B/C/D = 第二批):**

*A. Hypothesis 資料模型 —— ★ 雙層設計(受控 predicate + 自由 rationale)* — ✅ 第一批完成(2026-08-03,migration `c059c8eec042`)

- [x] 新表 `hypothesis`:`hypothesis_id`(PK)/ **Pattern 定義**(見 B)/ `predicted_outcome`(受控 predicate)/ `rationale`(自由文字)/ `confidence` / `source_insight_refs` / `model` / `prompt_version` / `hypothesis_version` / `status` / `parent_hypothesis_id` / `rejection_reason` / `created_at`
- [x] **雙層的理由:** 預測若全是自由文字,**Phase 4 的判官無法用程式碼判斷「驗證成功還失敗」**。受控層 `predicted_outcome` 給**系統判讀**(Phase 4 可純函數比對 `ActualOutcome == PredictedOutcome`);描述層 `rationale` 給**人閱讀**(並作未來影片/信件腳本的參考)
- [x] **predicate 受控用應用層 registry,不下沉 DB CHECK** —— 同 Phase 2.5 value_text 的判準(用「穩不穩定」決定受控放哪)。合法值**取決於 Phase 4 實際用哪些武器,而那還沒定**(武器庫優先序是 listing 優化 / Build in Public 第 1、cold email 第 3,現在定死 `EMAIL_OPEN` 之類很可能定出一組用不到的);且受控清單**加值容易、收回難**(Phase 1-C 踩過)。**第一版只登記「已確定會用」的少數 predicate,不預先窮舉**
- [x] `source_insight_refs` 依 Provenance 鐵律:上游引用不可為空(**NOT NULL + 非空陣列 CHECK** —— NOT NULL 擋不住 `[]`)
- [x] 新表 `decision`(Decision Graph):含 `parent_decision_id`;`target_type` + `target_id` **泛型指向**(代價:無 FK 保證參照完整性,取捨已記進設計文件)

*B. ★ 粒度:針對「特徵組合(Pattern / Archetype)」,絕不每店一條* — ✅ schema + 查詢完成(生成邏輯屬第二批)

- [x] **這是最核心的決定。** 針對單一店家只有 **1 次驗證機會(N=1)** → 發一封信被拒,分不清是「假說爛」還是「那家老闆剛好心情不好」→ **假說無法被證偽,confidence 機制直接崩塌**,違反 P1「裁判需要足夠投票數」
- [x] 針對特徵模式(如 `[High SKU + Rating Crisis + Loox User]`)→ 一條假說可套用在 200 家符合特徵的店上 → Phase 4 發 200 次、收 30 個反應 → **算得出統計信心度**,假說演化才成立
- [x] **★ Pattern 必須是「可執行的條件」,不能只是文字** —— Phase 4 需要「這個 Pattern 對應哪些店」的查詢,故 Pattern 定義**必須能翻譯成 SQL 去撈店**(結構化的 insight_type/value 條件組合,而非「高流量的美妝店」這種散文)。**這要寫進 schema,否則 Phase 4 拿到假說卻不知道要打誰**

*C. Decision Graph:schema 現在建,演化循環第一版不開*

- [ ] **人的 reject 進 Decision Graph —— ✅ 做**(Jeff 審核假說時現在就會 reject,這條路徑要通)
- [ ] **AI 讀舊假說產生進化版 —— ❌ 第一版不做**:證偽發生在 Phase 4,Phase 3 第一次跑時**沒有任何被證偽的假說可當輸入**,這個循環要等 Phase 4 有 Outcome 才轉得起來(屬 Phase 5 Evolution)。**schema 預留,第一版不開啟**

*D. 換模型(P4 地基)+ 餵 LLM 的方式*

- [ ] `LLMProvider` 抽象(Factory Pattern):`AnthropicProvider` / `OpenAIProvider`。**採 API 直接調用(SDK),不依賴 CLI**(Claude Code 是終端機的開發 Agent,MES 是 Python backend,兩者不同)
- [ ] **第一版可只實作一個 provider,但抽象層先做好** —— 換模型的架構成立,補第二個 provider 時不動核心。實務注意:API key 管理(Anthropic 已有,OpenAI 需另辦)
- [ ] **★ 先聚合成 Pattern 分佈,再送 LLM** —— 不要把所有店的 raw insight 全塞給 LLM(浪費 token,且會陷入「Lost in the Middle」)。作法:① DB 聚合(SQL group by)算出商家模式分佈 → ② 把 Pattern Summary + 2–3 家代表性**匿名 sample** 丟給 LLM → ③ 讓 LLM 針對該 Pattern 專心產出高品質假說
- [ ] **附帶好處:這正好解掉「輸入貧乏」的擔憂** —— LLM 看的是聚合後的分佈,14 家店也能形成 pattern,只是樣本小、confidence 低。**這是誠實反映現況,不是缺陷**

### ✅ 驗收(Acceptance)

**狀態:** ⬜ 未驗收(Phase 尚未開始)

**驗收條件:**
- [ ] 假說結構化、帶 evidence、引用 Insight、可審核
- [ ] reject 進 Decision Graph
- [ ] 換模型(GPT ↔ Claude)讀同一份 Knowledge/Insight 能各自產生假說(P4 地基成立)
- [ ] 可分別評估模型的觀察力(Insight)與推理力(Hypothesis)

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

### ✅ 驗收(Acceptance)

**狀態:** 🔄 驗收中(Reddit 帳號養成進行中;其餘未起步)

**驗收條件:**
- [ ] 至少一種合規、可收反饋的接觸行為就緒(此為 Phase 4 進入條件之一)
- [ ] 本地部署備份 + 防駭到位(累積觀測是無法重建的資產)

**停止條件:**（此為平行準備 Phase,無硬停止條件;若接觸行為的合規性存疑則暫緩該行為,不阻擋 Phase 0–3。）

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

### ✅ 驗收(Acceptance)

**狀態:** ⬜ 未驗收(Phase 尚未開始)

**驗收條件:**
- [ ] 跑通一個完整循環:假設 → 行動 → Outcome → 綁回 Experiment 綁回 Hypothesis
- [ ] 「特徵 → 行為 → 結果」鏈完整(哪怕只接觸十幾家、反饋很粗)

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

### ✅ 驗收(Acceptance)

**狀態:** ⬜ 未驗收(Phase 尚未開始)

**驗收條件:**
- [ ] 出現第一個「假說因新證據改變 confidence,並導致 Jeff 調整方向」的完整事件
- [ ] 全程可追溯(P5)+ 可回滾(Append-Only + Decision Graph)

**停止條件:** 樣本不足就裁決生死 / 演化不可追溯或不可回滾 → 停。
