# MES — Progress Log(進度日誌)

> **用途:** 逐日/逐次進度日誌 —— 記「實際做了什麼、跑了什麼、結果如何」。
> **只記真實發生的事,沒發生的不要編。** 「code 改完 ≠ 驗過」——實作與驗收分開記。
> 最新的在最上面。

---

## 2026-07-12 — 口徑校正:六大原則由「憲法/教條」改為「可演化系統原則」+ 立三層口徑

- **純措辭/框架校正,不改任何 code、不改任何實際約束行為。未 commit(待 Jeff 核對)。**
- grep 全專案的「憲法/不可違背/鐵律/教條/神聖/永遠…」等措辭,逐一分類 A(可演化系統原則,軟化)vs B(資料層硬約束,保留強硬)。
- **改的(A,共 2 處,皆在 Roadmap):** `## 五原則(憲法)` → `系統原則/工程基線(可演化,非教條)` + 加口徑定性段;Phase 0 產出表「Principles … 憲法」→「系統原則/工程基線(可演化,非教條)」。六原則 P1~P6 的**內容不動**,只改「教條框架」外殼。
- **保留強硬的(B,未動):** 所有 Append-Only「鐵律/絕不 update」、Provenance NOT NULL、失敗三值不偽裝、value 欄 CHECK、Knowledge_State「不可動搖/唯一真相/無後門」、value_raw 邊界——這些是「破壞就資料腐敗」的本版硬約束,不因口徑調整鬆動。
- **CLAUDE.md 新增「A+. 三層口徑」一節:** 第一層終極目的(實質行銷效益)/ 第二層紅線防護(唯一絕對底線=不違法、不惡意詐欺)/ 第三層系統原則(可演化);明寫判斷順序(先法律紅線 → 再實質效益 → 系統原則被這兩者檢驗)+ 「系統原則 ≠ 資料層硬約束」的區分。
- 未發現「A/B 混在同一句」需拆分的情況;無 code / 約束變動。

## 2026-07-11 — 文件一致性補齊 + 驗收獨立成三態子區塊

- **一致性檢查(只補漏記/不一致,不新增設計):**
  - `MES_Observation_Schema_v1.md` §2 欄位區塊漏列 `producer` → 補上(§7 早有定義、code 為 NOT NULL)。
  - `MES_Entity_Model_v1.md` §2 清單 + §3 schema 未列 `store_name_seed` → 補上(Phase 1-C 已用)。
  - `MES_Entity_Model_v1.md` §4 未記 store_name_seed 的 canonical_key 正規化(`seed:` 前綴)→ 補上。
  - `findings.md`:downgrade 教訓補「受控清單近乎只能往前加、難往後收,新增受控值要更慎重」引申;fetch_failed/not_found 教訓補「真實 DDG 限流下三值誠實運作」的 live 驗證。
  - code ↔ schema 其餘(discriminated union 六欄、source 六值、crawler_version 語義)核對**一致**,無需再改。
- **驗收(Acceptance)獨立成三態子區塊(做法 A):** task_plan 每個 Phase(0/1/2/2.5/3/3.5/4/5)把原埋在內文的「驗收標準」抽成獨立 `### ✅ 驗收(Acceptance)` 子區塊,含四態狀態(⬜未驗收 / 🔄驗收中 / ✅通過 / ❌未通過)+ 可勾選驗收條件 + 停止條件;頂部加三態定義說明。工作項目 checklist 保留不動。
  - **Phase 0 → ✅ 通過**(五份 schema 定稿並落成 code)。
  - **Phase 1 → ⬜ 未驗收(誠實標註):** A/B/C 核心工作已完成、43 測試過,但驗收條件「連續 7 天自動跑 + 規模累積(目標~1000家)」尚未做,本次僅偵察 live run 5 家。**未因 checklist 勾滿而謊報通過。**
  - **Phase 2/2.5/3/4/5 → ⬜ 未驗收**(尚未開始);**Phase 3.5 → 🔄 驗收中**(Reddit 養成進行中)。
- 驗證:`pytest` 43 passed、ruff / mypy 綠(無 code 變動,僅文件)。
- 本次連同 B/1-C 全部進度一起 commit。

## 2026-07-11 — schema 細化:source 加 web_search + 新增 producer 欄 + crawler_version 歸位

- **兩個已定案細化(版號不動,物理落地 DB CheckConstraint),趁 DB 近空表一次改乾淨。**
- **決定一:source 加 `web_search`。** inferred_domain 原暫記 `html_page` = 在 Provenance 管道欄說謊。改法:SOURCES 加 `web_search` 並擴充 DB source CHECK(物理);`ingest.py` 的 inferred_domain source 改 `web_search`;`docs/MES_Observation_Schema_v1.md` §5 source 清單升 v2。
- **決定二:新增 `producer` 欄 + crawler_version 歸位。**
  - `producer`(observation_log + knowledge_state 兩表同構)**NOT NULL**(ORM + DB 雙層)+ VARCHAR + CHECK,三值:`mes_crawler_v1` / `duckduckgo_v1` / `manual_v1`。語義=產生此值的方法/模型(責任主體);與 P6 的 provider(外部資料源)刻意區分,命名嚴格用 producer。
  - crawler_version 歸位:拔除暫塞的 `duckduckgo_v1`,回歸只存 git SHA-1;移轉到 producer。
  - 三欄分工:`source`=管道 / `producer`=方法模型 / `crawler_version`=程式碼版本。
  - 寫入鏈路對齊:observed_on_app_store → producer=mes_crawler_v1;inferred_domain(A/B 皆)→ source=web_search + producer=duckduckgo_v1。inference.py 的 result 欄位由 provider 改名 producer。
- **Migration `d9eb673e28aa`(接在 f215 後):** source CHECK 擴充、兩表加 producer(NOT NULL + CHECK,含 backfill 安全)、crawler_version 清 duckduckgo_v1。
- **dev 資料處理:選擇清空重跑(非遷移)。** 理由:最乾淨,且避免 backfill 把舊 inferred_domain 誤標成 mes_crawler_v1。作法:`TRUNCATE observation_log/knowledge_state/entity CASCADE`(row-level Append-Only trigger 對 TRUNCATE **不**觸發,故可清)→ `downgrade base` → `upgrade head` 完整重建(5 種子回來)→ 重跑 live。
- **實跑結果:**
  - `pytest` 43 passed(36 + 7 新:source=web_search 可寫、producer 三值可寫、非法 producer 被拒、producer=NULL 被拒、knowledge producer=NULL 被拒);ruff / mypy 綠。B/C 既有測試全過。
  - **migration 回滾:** 空表上 `downgrade -1 → upgrade head` 通過。**教訓記下:** 有 `web_search` 資料時 downgrade **會**(且應該)失敗——收窄受控詞彙的 downgrade 本就要求該值資料先清掉,這是正確行為不是 bug。
  - **live run 三值全現:** 真實店跑 DDG,observed / fetch_failed / not_found 都出現(連續查詢後 DDG 開始限流 → 多筆 fetch_failed,三值語義誠實運作)。DB 實查:observed_on_app_store→(html_page, mes_crawler_v1);inferred_domain→(web_search, duckduckgo_v1);crawler_version=='duckduckgo_v1' 計數 **0**(歸位確認)。
- **未 commit。**

## 2026-07-11 — Phase 1-C:Scraper + Name→Domain Inference + 雙骨牌寫入鏈路

- **建的檔:** `src/mes/normalize.py`(domain / seed name 正規化,單一收斂)、`src/mes/ingest.py`(雙骨牌寫入鏈路,確定性)、`src/mes/scrape.py`(Loox 評論頁 scraper)、`src/mes/inference.py`(DuckDuckGo Name→Domain)、`tests/test_phase1c_ingest.py`(6 個寫入鏈路測試)。改:`models.py`(entity_type += `store_name_seed`、加 `FEATURES_META`)、migration entity_type CHECK、`pyproject.toml`(+httpx)、`docs/MES_Feature_Taxonomy_v1.md`(元特徵分類軸)。
- **受控詞彙(細化,版號不動):** entity_type 加 `store_name_seed`;feature 加 `observed_on_app_store` / `inferred_domain`(feature 欄不 CHECK 鎖,僅登錄)。
- **雙骨牌結構:** 骨牌一=撈到 Store Name → 建 `store_name_seed` entity + `observed_on_app_store` observation(certain,現場真實)。骨牌二=拿 Seed 推 domain →(A)成功:建 store entity + `inferred_domain`(entity_ref, inferred);(B)失敗:掛 Seed、全欄 NULL、status 依 fetch_failed / not_found 分流。

### Scraper/Inference 實跑的真實情況(本階段最重要)

- **robots.txt(apps.shopify.com,2026-07-11 實查):** User-agent `*` 只擋 `/internal/`、`/services/`、`*q=*`、shpxid/auth 參數;`/loox/reviews` 允許抓。另自我節流 5–25s。
- **Loox 評論頁真實結構:** 每頁 **~10 則**(非想像的 20)。每則在 `data-merchant-review` 區塊;Store Name 在 `tw-text-heading-xs tw-text-fg-primary` div 內的 `<span title="...">`,`title` 屬性存完整店名(視覺截斷也完整)。實測一頁乾淨抓到 10 個店名(如 Wölfe Cutlery、AIRDEKO、Savvy Boheme)。此 selector 版本敏感,結構一變 `parse_store_names` 會回傳變少/空——那本身是訊號,不硬湊。
- **DuckDuckGo 2026 現況:通,但只有一個 endpoint 能用。** `https://html.duckduckgo.com/html/`(POST `q`)回 200 + 10 筆 `result__a` 連結;`https://duckduckgo.com/html/` 回 3KB「browser not supported」空殼、抓不到結果。第一版用前者。
- **命中率初估(live run,前 5 家真實店):5/5 推出 domain。** Wölfe Cutlery→wolfecutlery.com、AIRDEKO→airdeko.com、RipRightWear→riprightsticks.com、Savvy Boheme→savvyboheme.com、T-Toplights→t-toplights.com。樣本極小,不宜外推;RipRightWear→riprightsticks.com 顯示「第一個非黑名單結果」規則可能選到品牌相關但非完全同名的 domain,精確度未經人工核實(這正是 `inferred_domain` 元特徵未來要評估的)。
- **可信 domain 判定規則 v1(可檢驗、可演化):** query = `"<name> shopify store"` → 取 `result__a` 連結,依序找第一個 registrable domain 不在黑名單(shopify.com / pinterest / etsy / amazon / 社群 / 聚合站 shopifyspy 等)的當候選;非 200/timeout/無結果容器 → fetch_failed;有結果但全被黑名單濾掉 → not_found。
- **Provider 版本標註:** inference 的 `duckduckgo_v1` 暫存於 observation 的 `crawler_version` 欄(v1 schema 無專屬 provider/generated_by 欄)。此為權宜,已在 findings 標記待補。

- **測試:** `pytest` 36 passed(30 B + 6 C);ruff / mypy 綠。B 階段既有測試全數保留通過,新增 entity_type/feature 未弄壞既有 CHECK。live run 已把 5 家真實店寫進 dev DB(雙骨牌完整)。
- **未 commit。**

## 2026-07-11 — schema 文件對齊 discriminated union(做法 A,版號不動)

- 把 `docs/` 三份 schema 定稿的 value 欄結構原地更新為 discriminated union,對齊已實作的 `src/mes/db/models.py`。做法 A:原地改 + 修訂註記,**版號不動**(欄位實作細化,非設計變更)。
- **改動段落:**
  - `MES_Observation_Schema_v1.md` §2:value 欄由 `value_normalized` 單欄改為 typed 六欄;新增「value 欄 CHECK 契約」小節(兩層 status↔value_raw / status↔value_type↔typed + value_raw 語義邊界)。
  - `MES_Knowledge_Schema_v1.md` §2:value 欄同步改 typed 六欄;註明與 Observation_Log 同構、無 status 分支的 CHECK、source_observation_id 不變。
  - `MES_Entity_Model_v1.md` §6:value 型別結構整段改寫為 discriminated union,保留「型別自聲明」「raw/normalized 分開=Provenance 微觀落實」論點。
- 三處各加一行修訂註記(2026-07-11 Phase 1-B 細化,版號不動)。與 code 一致:同一組六欄 value 欄、同一組 CHECK 契約。
- **未 commit**(今天結束時統一 commit)。

## 2026-07-11 — Phase 1-B 修訂:value 欄改 discriminated union + 雙層 CHECK

- **動機:** 原 value 欄是單一 `value_raw` + `value_normalized`,無法安全表達五種 value_type,且 knowledge_state 投影時需型別轉換(腐敗點)。改為 discriminated union 分欄結構。因 migration `f215450ec0a6` **尚未 commit、表還空**,直接改乾淨,不新增中間狀態 migration。
- **改了什麼:**
  - `src/mes/db/models.py`:observation_log 與 knowledge_state 兩表**同構** value 容器 —— `value_type` + `value_raw`(只存 feature 原始值原貌)+ typed 分欄 `value_text`(Text)/ `value_number`(Numeric)/ `value_boolean`(Boolean)/ `value_json`(JSONB)/ `value_entity_id`(UUID FK)。移除 `value_normalized`。CHECK 表達式由受控清單常數 + `_exactly_one_typed()` / `_all_typed_null()` 單一函式生成(收斂,不散寫)。
  - `migrations/versions/f215450ec0a6_*.py`:同步改欄位 + 加四條 value CHECK(observation 兩條、knowledge 兩條)。沿用上一批的 UUID / Provenance 雙層 / Append-Only trigger / 5 個種子,那些**未動**。
  - `tests/test_phase1b_schema.py`:更新 helper(改用 typed 欄),擴充逐分支測試。
- **雙層 CHECK 契約(observation_log):**
  - 層一 status↔value_raw:observed → value_raw 非 NULL 且 `btrim(value_raw) <> ''`(空字串一起擋);fetch_failed/not_found → value_raw NULL。
  - 層二 status↔value_type↔typed:observed → 正好一個與 value_type 相符的 typed 欄非空、其餘全空;failed/not_found → 所有 typed 欄全空,但 value_type 保留(描述預期型別)。
  - knowledge_state:無 status 欄(只投影 observed)→ value_raw 非空 + 正好一個相符 typed 欄非空。
- **實跑結果(非假裝):**
  - `pytest` → **30 passed**(真連 DB)。逐項含:5 種 value_type 正確組合可寫;`string` 卻填 `value_number`、`number` 卻同時填 `value_text`+`value_number` **被拒**;observed 但 value_raw=NULL / value_raw='   ' **被拒**;fetch_failed/not_found 帶 value_raw 或 typed 值 **被拒**,全空 + 保留 value_type **可寫**;knowledge 正確組合可寫、value_raw 空被拒、typed 不符 value_type 被拒。
  - 上一批測試(Append-Only 鎖、Provenance NOT NULL、knowledge 可 UPDATE、entity_type/source CHECK)**全數保留且通過**,未被弄壞。
  - `ruff check .` → All passed;`mypy src` → Success;migration down→up 回滾通過,5 筆種子重建。
- **文件不符提醒:** 三份 schema 定稿(Observation Schema v1 §2、Knowledge Schema v1 §2、Entity Model §6)仍記載舊的 `value_raw`/`value_normalized` 單欄形式;此次是 Jeff 指示的結構修訂,schema 文件尚未同步(本次未被要求改 schema 文件)。

## 2026-07-11 — Phase 1-B:三張核心表 ORM + Alembic migration

- **建了什麼:**
  - `src/mes/db/models.py`:`Entity` / `ObservationLog` / `KnowledgeState` 三個 ORM model,嚴格依三份 Phase 0 schema 定稿欄位。所有主鍵 ID 用 UUID、Python 端 `uuid.uuid4` 生成。受控字串(entity_type / value_type / source / confidence / status)用 VARCHAR + CHECK(非原生 ENUM)。`feature` 不 CHECK 鎖死(彈性結構),9 個 v1 feature 以常數 `FEATURES_V1` 列出供參考。
  - `src/mes/db/__init__.py`:匯出 `Base` 與三個 model。
  - `migrations/env.py`:import `mes.db.models` 讓 autogenerate 看得到 metadata。
  - `migrations/versions/f215450ec0a6_*.py`:autogenerate 三表後,手動補上 (a) observation_log 的 Append-Only trigger(plpgsql `mes_reject_mutation` + BEFORE UPDATE/DELETE 兩個 trigger),(b) 五個 ReviewApp 種子 entity(loox/judgeme/yotpo/okendo/stamped,固定 UUID)。downgrade 對稱清掉 trigger/function。
  - `tests/test_phase1b_schema.py`:12 個整合測試(真連 PostgreSQL,非 mock)。
  - `pyproject.toml`:ruff `extend-exclude = ["migrations/versions"]`(Alembic 生成碼不 lint)。
- **實跑結果(非假裝):**
  - `alembic upgrade head` → 成功;`docker exec psql` 確認 3 表 + 2 個 trigger + 5 筆 review_app 種子皆在。
  - **Append-Only 鎖(決定三關鍵)實測生效:** 對 observation_log 做 UPDATE / DELETE **被 DB 拒絕**,錯誤訊息含 "append-only"。knowledge_state 的 UPDATE **正常成功**(物化語義未被誤鎖)。
  - **Provenance 硬約束實測生效:** observation_log 寫 `entity_id=NULL` 被拒;knowledge_state 寫 `source_observation_id=NULL` 被拒。
  - **受控字串 CHECK 實測生效:** 非法 status/confidence/value_type/source 四種各自被拒。
  - `pytest` → **12 passed**;`ruff check .` → All checks passed;`mypy src` → Success。
  - **migration 回滾驗證:** `downgrade base`(trigger/function 歸零)→ `upgrade head`(重建 + 重新種子 5 筆)round-trip 通過。
- **未碰:** C(Scraper / Inference / 寫入鏈路)與 D(9 個 feature 實際抓取)仍未開始,屬後續階段。

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
