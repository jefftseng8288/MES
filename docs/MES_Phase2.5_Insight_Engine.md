# MES Phase 2.5 — Insight Engine 準備/拆解

> 本文件是 Phase 2.5 的**實作前準備**:把既有 task_plan 定義裡「概念清楚、但實作前需定案」的設計決定想清楚、定案。
> **既有定義的骨架不動**(目的 = Describe not Predict、Rule/Statistics 實作者、不用 AI、Insight metadata、不混預測)。
> 本文件只補實作層的設計決定。**這是準備,不是實作 —— 一行 code 都不寫。**

---

## 零、核心定位(既有定義,不變)
- Phase 2.5 = 把 Knowledge 濃縮成「**描述看到了什麼**」的 Insight。**這是 Describe(描述),不是 Predict(預測)。**
- 分層:Phase 2(Knowledge,中立事實)→ Phase 2.5(Insight,描述性標籤)→ Phase 3(Hypothesis,會死的預測)。
- Insight 層做「描述性歸納/貼標籤」,但**不預測、不回寫污染 Knowledge 層**。
- 第一版**刻意不用 AI**(LLM 作為可插拔實作者,Phase 3 之後才加入)。

---

## 一、Insight 資料模型:獨立新表 `insight_store`(定案)

- **完全獨立的新表,絕不與 knowledge_state 混。**
  - knowledge_state = 中立的事實物化視圖(如 product_count=520),主鍵 (entity_id, feature)。
  - insight_store = 對 Facts 做「標籤化 / 語義壓縮」的結果(如 product_count=520 → High SKU)。
- **一對多:** 一個 Insight(如 Growth)可能引用/基於好幾條不同 (entity_id, feature) 的最新事實。
- **一個 entity 可多列**(一家店可同時是 High SKU + Growth,每個 insight 一列)。

**Schema:**
| 欄位 | 型別 | 說明 |
|---|---|---|
| `insight_id` | UUID PK | |
| `entity_id` | UUID NOT NULL | 這個 insight 關於哪家店 |
| `insight_type` | VARCHAR(50) NOT NULL | 維度,如 `SKU_SCALE` / `GROWTH_VELOCITY` |
| `value_text` | VARCHAR(255) NOT NULL | 標籤,如 `High SKU` / `Growth`(**受控,見下**) |
| `producer` | VARCHAR(50) NOT NULL | 產生者,如 `rule_v1` / `stat_v1`(Provider 競技場基因) |
| `confidence` | VARCHAR(20) NOT NULL | 離散三級(沿用既有 confidence 語彙) |
| `generated_at` | TIMESTAMPTZ NOT NULL | 產生時間 |
| `source_knowledge_refs` | JSONB NOT NULL | 這個 insight 基於哪幾條事實(見下) |

### value_text 受控(定案:第一版應用層驗證,不下沉 DB CHECK)
- **受控:** 每個 insight_type 有它自己的合法 value 集合(如 SKU_SCALE → {High SKU, Medium SKU, Low SKU})。
  防止不同實作者吐出不一致寫法(High SKU / high_sku / HIGH),否則 Phase 3 AI 會誤以為是不同東西。
- **落地方式(★ 依「這東西穩不穩定」判斷):第一版用「應用層驗證 + 集中定義」,不下沉 DB CHECK。**
  - 理由:insight_type / value 還在快速演化(才剛開始做 Insight,未來會不斷加新 Rule 實作者、新標籤)。
    此階段用 DB CHECK 硬鎖太早 —— 每加一個標籤要改 migration,且「受控清單只能往前加、難往後收」(Phase 1-C 踩過)。
  - 做法:每個 insight_type 的合法 value 集合集中定義在一處(registry,或每個 Producer 類別自己聲明),
    InsightEngine 寫入前驗證。
  - 對比:knowledge_state 的 feature 值(如 currency)是穩定既定事實,適合 DB CHECK;
    insight 標籤是正在創造、會演化的東西,適合先應用層。**用「穩不穩定/會不會頻繁改」決定受控放 DB 還是應用層。**
  - 等 insight 類型穩定後,再考慮下沉 DB CHECK。

### 實作落地補充(2026-07-19 第一批,`insight_store` 表已建;migration `aa0151f18e2d`)

- **`(entity_id, insight_type)` UNIQUE 約束(Jeff 定案,設計文件原無):** 與 knowledge_state 的 `(entity_id, feature)` 主鍵**同構** —— 一個 entity 的每個 insight 維度只有一個當前值。效果:第二批的全量重算走**依此鍵 upsert**(而非清空重寫)→ **`insight_id` 穩定不會每天重生成**,未來 Phase 3 的 Hypothesis 才引用得住(清空重寫會讓引用斷掉)。
- **★ `generated_at` 用 `now()`(執行時間),這不違反 Phase 2 的「投影禁用系統時間」——兩者語義不同:**
  - knowledge_state 的 `observed_at` = 「這個**事實**何時被觀測」→ 是歷史事實,必須由 observation_log 的 observed_at 投影而來,**禁用系統時間**。
  - insight_store 的 `generated_at` = 「這個**描述**何時被產生」→ 本來就是執行時間,不是在描述歷史事實。
  - **已知且接受的代價:** insight_store **不是**冪等重建的(今天重算與明天重算 generated_at 不同)。可接受 —— insight 本來就是「每天對當前 Knowledge 重新描述一次」的快照,不是「歷史真相的投影」;真相在 observation_log,insight 只是描述層。
- **CHECK 落點(刻意不一致,依「穩不穩定」判準):** `confidence` 沿用 Phase 0 既定三級(穩定)→ **有 DB CHECK**;`insight_type` / `value_text` / `producer`(演化中)→ **無 DB CHECK**,受控放應用層 `src/mes/insight_registry.py`。
- **`producer` 不與 observation 的 `PRODUCERS` 共用受控清單:** insight 的產生者(`rule_v1` / `stat_v1`)與 observation 的產生者(`mes_crawler_v1` / `duckduckgo_v1` …)是**兩套不同語彙**,不混進同一個 CHECK;且 insight producer 會隨新 Producer 增加而演化,故同樣不下沉 DB。
- **`entity_id` 加 FK → `entity.entity_id`**(沿用 knowledge_state 慣例)。

### source_knowledge_refs(Provenance,定案:記 (entity_id, feature))
- NOT NULL JSONB,記這個 insight 基於哪幾條 knowledge 事實 —— 一組 `(entity_id, feature)`。
- **第一版記 (entity_id, feature) 即可**,不追求「完全重現當時的確切值」。
  理由:insight 有 generated_at、且每天重算(如 knowledge_state 每天重投影),
  「歷史某天 insight 的確切基於值」的精確重現,現在不是必要需求。
- 精神同 observation_log 的 source_observation_id:讓 insight 可追溯、知道它從哪些事實生出來。

---

## 二、Rule / Statistics 實作者架構(定案:Pipeline Plugins)

- **標準「管線插件」架構,由常駐 Orchestrator 定時批次調用。**
- **實作者 = 純函數類別**(繼承 `BaseInsightProducer`),**不自己撈 DB**。
- **核心管線 `InsightEngine`:**
  1. 排程觸發時,InsightEngine 把某家店(同一 entity_id)在 knowledge_state 的所有 Facts 打包成記憶體字典(Context Dict)。
  2. 把 Context Dict 丟給實作者群(一個可插拔的 List)。
  3. 實作者各司其職、平行掃描這份字典:
     - `SKURuleProducer`:看到 product_count > 500 → 吐出 High SKU 的 insight 結構。
     - `GrowthStatProducer`:呼叫歷史函數(Phase 2 的時間序列查詢)算最近 30 天 review 成長率 → 吐出 Growth 結構。
  4. InsightEngine 把這批吐出的 insight 批次寫入(upsert)insight_store。
- **好處:** 實作者是純函數(給定 Facts → 吐 Insight),可測試、可重現;加新實作者只需加一個類別丟進 List(可插拔)。
- 呼應 Roadmap「實作者可插拔」+ Phase 2 的純函數精神。

---

### 實作落地補充(2026-07-19 第二批,`src/mes/insight.py` + `insight_producers.py`)

- **⚠️ GROWTH_VELOCITY 目前無資料源(誠實性標記):** 它算的 `review_count` **不在 Feature Taxonomy v1 的 9 個市場特徵內、Phase 1-D 沒抓**,真實觀測數為 **0** → **對真實資料永遠產出 0 筆**。這**不是「還不夠 30 天、等等就有」,而是源頭沒開**;要有真實產出,得先把 `review_count` 納入採集(屬 Feature Taxonomy / Phase 1-D,待 Jeff 定)。計算能力本身已用測試資料完整驗證。**「能力達成」≠「現在有東西可產出」。**
- **GROWTH_VELOCITY 改為「數值型、刻意不設門檻」(Jeff 定案,原設計文件寫的是吐出 `Growth` 標籤):** 不判斷「多少算 Growth」,只記錄實際成長率數值。**理由:門檻是一種判斷,判斷該由「後面要做什麼行為」決定。** 現階段沒有任何下游行為,設門檻等於憑空造判斷,還會丟失資訊(+19% 與 −50% 被壓成同一類「Growth」,差異永遠不見)。Phase 4 要怎麼切,由那時的實際行為決定。故 registry 擴充為支援兩種 insight_type:**列舉型**(SKU_SCALE)與**數值型**(GROWTH_VELOCITY,驗證「可解析為數值」而非列舉)。
- **SKU_SCALE 門檻(Jeff 定案,連續無縫不留空隙):** `≤100` Low SKU / `101–500` Medium SKU / `>500` High SKU。confidence 誠實沿用來源事實的信心度(事實若 estimated,標籤不自稱 certain)。
- **成長率算法:** 用 Phase 2 的時間序列取 `review_count` 的 observed 歷史;「30 天前的值」取**最接近 30 天前**的那筆,**容忍窗 25–35 天**(含端點),窗內找不到 → 不產出並記錄跨度。成長率 =(當前 − 30天前)/ 30天前;分母 ≤0 → 不產出並記錄基準值。value_text 格式統一為**比率、小數 6 位**(如 `"0.250000"` = +25%,`"-0.500000"` = −50%)。因窗有容忍(未必剛好 30 天)→ confidence 記 `estimated`。
- **★ Producer 純函數但需歷史 → 由 Engine 統一撈:** Producer 只**聲明**它需要什麼(`required_features` / `required_history`),Engine 撈齊打包成記憶體 `InsightContext` 交給它;Producer 拿到的永遠是記憶體物件、**不碰 DB** → 純函數、可測試、可重現。`produce(ctx)` 的唯一參數就是 Context(測試有守門)。
- **producer 欄補上應用層受控(第一批的缺口):** producer 是 Provider 競技場的核心欄位(未來比較 rule_v1 / stat_v1 / LLM 的觀察力),寫法不一致(rule_v1 / rule_V1 / ruleV1)= 計分板壞掉。各 Producer 類別透過 `__init_subclass__` **自己聲明**、registry 統一收攏;寫入前 `validate_producer()` 守門。仍**不下沉 DB CHECK**(理由同 value_text:還在演化)。
- **加新 Producer = 加一個類別丟進 `DEFAULT_PRODUCERS`**,不動 Engine(連 registry 登記都自動)。
- **★ Engine 只處理 `store` entity(實作決定,需 Jeff 確認):** Insight 描述的是市場實體的狀態;`store_name_seed`(尚未推出 domain 的名字)依定義沒有任何市場特徵,為它記「無 product_count」不是失敗訊號而是**類別錯誤**,且量體會把 run log 的真訊號埋掉(實測:不篩時一次全量 5918 筆 skip 中 2477 筆來自 seed;篩後降為 964 筆且**未損失任何真實產出**)。

### 執行報告表 `insight_run_log`(定案:記錄「為什麼沒產出」)

「查無此列」會靜默掉三種完全不同的情況 ——(a) 資料不足算不出、(b) 算得出但無此描述、(c) 根本沒處理到這家店。分不出來 = 失敗訊號被偽裝成沒事。**但不塞進 insight_store**(value_text NOT NULL;塞「資料不足」會把「系統的計算狀態」混進「市場描述」,污染語義)→ 獨立記錄,比照 `alert_log` 精神。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `run_log_id` | UUID PK | |
| `run_at` | TIMESTAMPTZ NOT NULL | 同一次全量重算共用,可據此聚合「某次執行」 |
| `entity_id` | UUID NOT NULL (FK) | 哪個 entity |
| `insight_type` / `producer` | VARCHAR NOT NULL | 哪個維度 / 哪個實作者 |
| `reason` | TEXT NOT NULL | **具體載明缺什麼**(人讀) |
| `detail` | JSONB | 結構化細節,供聚合(如 `{"history_points": 1, "span_days": 12}`) |

實際原因範例(非「資料不足」這種無資訊字串):`review_count 僅 1 筆 observed(需當前 + 約 30 天前兩點)`、`觀測跨度僅 12 天,25–35 天窗內無 observed`、`knowledge 無 product_count 值(該 entity 未曾成功觀測到商品數)`。**累積不刪**(未來要判斷「還要不要繼續觀察這家店」需要歷史)。

**界線:** 本表**只做記錄**。「什麼時候停止觀察某家店」的決策機制**不在 Phase 2.5 做** —— 那要基於這些記錄累積後才能定,且牽涉 harvest 排程策略。

---

## 三、投影時機(定案:定時批次,每天 23:40 台灣)

- 遵守「拒絕不必要即時性」—— 算出來存起來,不即時算。
- **每天 23:40 台灣**,InsightEngine 批次跑(獨立 daemon,沿用既有 launchd 掛法,獨立於 harvest/alarm/projection)。
- **每日處理鏈:** Knowledge 投影(23:30)→ Insight 壓縮(23:40)→ 警鈴(23:50)。
- 理由:Insight 要餵給 Phase 3 AI 當輸入。AI 需要的是「固定時間點收攏、固化的乾淨靜態切片」,
  不是即時閃爍訊號。這確保純函數性與可重現性。
- 第一版全量重算(沿用 Phase 2 精神:資料量小,全量快、天然冪等)。

---

## 四、描述 vs 預測的界線(★ 防止實作時滑進預測;定案判準 + borderline 案例)

**紅線判準(可操作):只要出現「下個月 / 即將 / 應該會」等隱含未來時間軸、帶賭注性質的推論 → 是預測 → 立即停修。**
預測屬於 Phase 3 Hypothesis,Phase 2.5 一出現這種字眼就砍。

| 案例 | 歸類 | 裁決原因 |
|---|---|---|
| 商品數 > 500 → High SKU | 🟢 純描述(Insight) | 客觀事實。520>500 是當下狀態定義,無賭注成分。 |
| 最近 30 天 review 成長率 25% → Growth | 🟢 純描述(Insight) | 歷史統計。用今天與 30 天前數據相減算出的既定軌跡,是對歷史的語義壓縮。 |
| 這家店成長很快,下個月 review 應該會破千 | ❌ 預測(Hypothesis) | **越線!** 出現「下個月/應該會」的未來時間軸 + 賭注 → Phase 3,立即停修。 |
| 這家店「極度依賴」Loox(Highly Dependent) | ⚠️ 邊界臨界 → **可作 Insight,但死扣證據** | 若 knowledge 連續半年 uses_review_app='loox',下標籤 Highly Dependent = 對半年流水帳的描述壓縮(可)。但若引申「因為依賴,所以短期內不會換 App」→ 這是預測,立刻砍! |

**關鍵洞察:同一個事實,描述它是 Insight、從它引申未來是 Hypothesis。** Loox 依賴的「描述」可以,「因此短期不換」的「引申」不行。

---

## 五、驗收(既有定義,能力導向,不變)
- 純 Rule + Statistics 能對一批店穩定產出結構化 Insight,每個帶產生者(producer)與來源(source_knowledge_refs)。
- Insight 中沒有混入任何預測。
- **停止條件:** Insight 裡開始夾帶預測/賭注 → 停(Describe 與 Predict 混了)。

---

## 六、實作分批建議(供實作時參考,本文件不實作)
類似 Phase 2 的分批:
- **第一批:** insight_store 表 + schema(含應用層 value 受控機制的骨架)。
- **第二批:** InsightEngine + BaseInsightProducer + 頭兩個實作者(SKURuleProducer / GrowthStatProducer)+ 23:40 排程。
- (實作時再依實際細拆;此處僅備註方向。)

---

## 附:與既有 task_plan Phase 2.5 的關係
- 既有 task_plan 的 Phase 2.5 骨架(目的/工作項/驗收/停止條件)**正確,不改**。
- 本文件補上:insight_store 資料模型、value 受控方式、source_knowledge_refs 定義、Pipeline Plugins 架構細節、23:40 排程、描述vs預測界線的可操作判準與案例。
- 實作前,task_plan 的 Phase 2.5 工作項可依本文件補上「insight_store schema」「應用層 value 受控」「InsightEngine + Producer 架構」「23:40 排程」等實作項。
