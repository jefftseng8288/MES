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
