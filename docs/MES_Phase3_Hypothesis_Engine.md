# MES Phase 3 — Hypothesis Engine 準備/拆解

> 本文件是 Phase 3 的**實作前準備**:把既有 Roadmap / task_plan 定義裡
> 「概念清楚、但實作前需定案」的設計決定想清楚、定案。
> **既有定義的骨架不動**;本文件只補實作層決定。**這是準備,不是實作 —— 一行 code 都不寫。**

---

## 零、核心定位(既有定義,不變)

- Phase 3 = **第一次讓 AI 做「會死的預測」**。AI 只扮演 Observation/Knowledge/Hypothesis 角色,**不做 Decision**。
- 分層:Knowledge(中立事實)→ Insight(描述,可能永遠成立)→ **Hypothesis(預測,會死)**。
- **★ Phase 3 產出會死的預測,但 Phase 3 本身不驗證它們 —— 證偽發生在 Phase 4。**

### ★ 這造成一個必須守住的區分:Phase 3 驗的是「假說的形狀」,不是「假說對不對」

驗收條件裡**沒有任何「假說要準」的要求** —— 因為沒有 Outcome 之前,「準不準」根本無法評估。
Phase 3 驗的是:結構化、帶 evidence、引用 Insight、可審核、reject 進 Decision Graph、可換模型。

**實作時的紀律:不要不自覺地想「怎麼讓 AI 產出更準的假說」** —— 那個問題在 Phase 4 之前無解,
現在追求它只會變成憑感覺調 prompt(而「感覺更好」不是證據)。

### 開工前提:輸入貧乏是已知且接受的

- `insight_store` 目前只有 `SKU_SCALE`(三個標籤);`GROWTH_VELOCITY` 約一個月後才有真實資料。
- Roadmap 的假說範例(`Beauty + Loox + ReviewGrowth → ...`)是三維度組合,現在只有一維 → **第一批假說會單薄**。
- **這不構成延後的理由(Jeff 定):** 「豐富」沒有定義,以它為前提等於無限期拖延;
  真正能回答「資料夠不夠」的是 Phase 4 的 Outcome。**能力先建好,資料長出來自然變好。**

---

## 一、Hypothesis 資料模型:★ 雙層設計(受控 predicate + 自由 rationale)

**問題:** 預測若全是自由文字,Phase 4 的判官無法用程式碼自動判斷「驗證成功還失敗」。

**定案:雙層。**

| 層 | 欄位 | 性質 | 給誰用 |
|---|---|---|---|
| 受控層 | `predicted_outcome` | 受控 predicate(如 `SWAP_APP_INTENT`) | **系統判讀** —— Phase 4 可純函數比對 `ActualOutcome == PredictedOutcome` |
| 描述層 | `rationale` / `narrative` | 自由文字,記錄 LLM 的推論鏈 | **人類閱讀** + 未來生成影片/信件腳本的參考 |

### ★ predicate 的受控方式:應用層 registry,不下沉 DB CHECK(定案)

**理由(與 Phase 2.5 的 value_text 同一判準:用「穩不穩定」決定受控放哪):**
- `predicted_outcome` 的合法值**取決於 Phase 4 實際用哪些武器,而那還沒定**。
  (Roadmap 武器庫優先序是 listing 優化 / Build in Public 第 1、cold email 第 3 ——
  現在若定死 `EMAIL_OPEN`、`HIGHER_CLICK_THROUGH_RATE` 這類值,很可能定出一組用不到的。)
- 受控清單**加值容易、收回難**(Phase 1-C 踩過 downgrade 失敗)。
- → **第一版只登記「已經確定會用」的少數 predicate,不預先窮舉**;等 Phase 4 跑過、predicate 穩定了再考慮下沉 DB。

### 其他必要欄位(依 Provenance 鐵律與 P5)
- `hypothesis_id`(PK)
- **Pattern 定義**(見第二節 —— 必須是**可執行的條件**,不能只是文字)
- `predicted_outcome`(受控 predicate)/ `rationale`(自由文字)
- `confidence`
- **`source_insight_refs`** —— 引用基於哪些 insight(Provenance 鐵律:上游引用不可為空)
- **版本化(P5 第一版兌現)**:`model` / `prompt_version` / `hypothesis_version`
- `status`(見第三節)/ `parent_hypothesis_id` / `rejection_reason`
- `created_at`

### 實作落地補充(2026-08-03 第一批;migration `c059c8eec042`)

- **`pattern` 的形狀(Jeff 定案):** JSONB 存**條件的 AND 組合**,例:
  `[{"insight_type": "SKU_SCALE", "value_text": "High SKU"}, {...}]`。
  **第一版只支援 AND**(不做 OR / NOT)—— 夠用,且能直接翻成 SQL;未來要更複雜的邏輯再擴充。
  翻譯成查詢見 `src/mes/patterns.py` 的 `stores_matching_pattern()`:取符合任一條件的列 →
  依 entity 分組 → 只留「命中條件數 = 條件總數」的。(`insight_store` 有
  `(entity_id, insight_type)` UNIQUE,故同一 entity 的同一維度只有一列,不會把 OR 誤算成 AND。)
- **pattern 寫入前驗證三層**(`validate_pattern()`):是非空 list / 每項有 `insight_type` 與
  `value_text` / **`(insight_type, value_text)` 在 insight registry 登記過**。
  第三層特別重要:引用未登記的標籤,這條 pattern **永遠撈不到任何店,而且是靜默撈到 0 家**
  —— 不擋的話,一條「看起來合理但打不到任何人」的假說會混進系統。
- **★ 兩個 JSONB 的非空 CHECK(NOT NULL 擋不住空陣列):**
  `source_insight_refs` 空陣列 = 沒有 Provenance(違反上游引用不可為空的鐵律);
  `pattern` 空陣列 = 「打所有店」,不是有意義的假說。兩者都在 DB 層擋。
- **CHECK 落點(刻意不一致,依「穩不穩定」判準):** `status`(四值,已定義完整)/
  `confidence`(Phase 0 既定)/ `action`(三值)→ **有 DB CHECK**;
  `predicted_outcome`(取決於未定的 Phase 4 武器)/ `target_type`(橫切、會擴充)→ **無**。
- **predicate registry(`src/mes/hypothesis_registry.py`):** 第一版**只登記
  `SWAP_APP_INTENT`** —— 它是本文件第一節唯一舉出的具體 predicate,不預先窮舉其餘。

### `decision` 表的設計落地

- **獨立成表**(而非記在 hypothesis 上):Decision Graph 是**橫切概念**;且「被 reject 兩次
  才 approve」這種**決策史**,記在單一欄位上只留得下最後結果,**中間過程整個消失**。
- **`target_type` + `target_id` 泛型指向**,不寫死 `hypothesis_id`。
  **已知代價:泛型指向無法用 FK 保證參照完整性**(FK 只能指向單一表)——
  這是為了「Decision Graph 是橫切的」而付的代價,取捨明確記在此。
  加了 `(target_type, target_id)` 複合索引服務「某對象的決策史」查詢。
- `parent_decision_id` 自我參照,可沿鏈還原整條決策路徑(測試已驗 reject → comment → approve)。

---

## 二、粒度:★ 針對「特徵組合(Pattern / Archetype)」,絕不每店一條(定案)

**這是最核心的決定。**

- ❌ **針對單一店家**:只有 1 次驗證機會(N=1)。發一封信被拒,分不清是「假說爛」還是「那家老闆剛好心情不好」
  → **假說無法被證偽,confidence 機制直接崩塌**,違反 P1「裁判需要足夠投票數」。
- ✅ **針對特徵模式**:如 `[High SKU + Rating Crisis + Loox User]` → 假說「這類店對『自動負評挽回』提案的反應率 > X%」。
  一條假說可套用在 200 家符合特徵的店上 → Phase 4 發 200 次、收 30 個反應 → **算得出統計信心度**,假說演化才成立。

### ★ 實作後果:Pattern 必須是「可執行的條件」

Phase 4 執行時需要「這個 Pattern 對應哪些店」的查詢 →
**Pattern 的定義必須能翻譯成 SQL 去撈店,不能只是文字描述。**
(例:結構化的 insight_type/value 條件組合,而非「高流量的美妝店」這種散文。)
這一點要寫進 schema —— 否則 Phase 4 拿到假說卻不知道要打誰。

---

## 三、Decision Graph:schema 現在建,但演化循環第一版不開(定案)

**要建:** `parent_hypothesis_id`、`rejection_reason`、`status`。

**★ 但要分清兩件事:**

| | 第一版 | 理由 |
|---|---|---|
| **人的 reject 進 Decision Graph** | ✅ 做 | Jeff 審核假說時**現在就會 reject**,這條路徑要通 |
| **AI 讀舊假說產生進化版** | ❌ 不做 | **證偽發生在 Phase 4** —— Phase 3 第一次跑時,沒有任何被證偽的假說可當輸入。這個循環要等 Phase 4 有 Outcome 才轉得起來(屬 Phase 5 Evolution) |

同「學習深度」的處理:**schema 預留,第一版不開啟。**

- `parent_decision_id` 的 Decision 記錄:每個 Decision 是對前一個 Decision 的回應(Roadmap 的 Decision Graph 契約)。
- reject 要記:決策對象 / 主體 / 理由 / 時間。

---

## 四、★ 學習深度(Roadmap 待拍板項,Jeff 定案)

- **第一版:只記錄 + 累積驗證次數。confidence 不自動裁決(守 P1 held)。**
- schema **預留**「調信心度」與「長新假說」,**第一版不開啟**。
- 理由:現在連一個 Outcome 都沒有,自動裁決沒有燃料。

---

## 五、換模型機制:LLMProvider 抽象(定案)

- **採 API 直接調用(OpenAI SDK / Anthropic SDK),不依賴 CLI。**
  (Claude Code 是終端機的開發 Agent;MES 是 Python backend,兩者不同。)
- 建立統一的 `LLMProvider` 抽象(Factory Pattern):`AnthropicProvider` / `OpenAIProvider`。
- 換設定或傳參即可讓不同模型讀**同一份 Insight、同一份 Prompt** 各自產生假說 → 滿足 P4 地基與驗收條件。
- **實務注意:** API key 管理(Anthropic 的已有,OpenAI 需另辦)。
  **第一版可只實作一個 provider,但抽象層先做好** —— 這樣「換模型」的架構成立,補第二個 provider 時不動核心。
- 呼應 P4(Model Agnostic):模型是受僱解讀中性資料的分析師,不讓任何模型成為唯一前提。

---

## 六、AI 怎麼讀 Insight:★ 先聚合成 Pattern 分佈,再送 LLM(定案)

**不要把所有店的 raw insight 全塞給 LLM** —— 浪費 token,且會陷入「Lost in the Middle」。

**作法:**
1. 先在 DB 做聚合(SQL group by),算出目前水庫裡的**商家模式分佈**
   (例:特徵組合 A 有 120 家、組合 B 有 45 家)。
2. 把**聚合後的 Pattern Summary** + 2–3 家代表性的**匿名 sample** 丟給 LLM。
3. 讓 LLM 針對這個 Pattern 專心產出高品質的商業假說。

**附帶好處:這正好解掉「輸入貧乏」的擔憂** —— LLM 看的是聚合後的分佈,
14 家店也能形成 pattern,只是樣本小、confidence 低。**這是誠實地反映現況,不是缺陷。**

---

## 七、驗收(既有定義,能力導向)

- [ ] 假說結構化、帶 evidence、引用 Insight、可審核
- [ ] reject 進 Decision Graph
- [ ] 換模型(GPT ↔ Claude)讀同一份 Knowledge/Insight 能各自產生假說(P4 地基成立)
- [ ] 可分別評估模型的觀察力(Insight)與推理力(Hypothesis)

**停止條件:** AI 把推論當事實寫進 Knowledge / 假說無 evidence 或不可審核 / AI 做 approve 以外的決策 → 停。

---

## 八、實作分批建議(供實作時參考,本文件不實作)

- **第一批:** hypothesis 表 + Decision 表(含 parent 欄)+ predicate 應用層 registry 骨架。
- **第二批:** LLMProvider 抽象 + Pattern 聚合 + 假說生成 + 審核流程(Approve/Reject/Comment)。
- (實作時再依實際細拆。)

---

## 附:與既有 Roadmap / task_plan 的關係

- 既有骨架(目的 / 工作項 / 驗收 / 停止條件)**正確,不改**。
- 本文件補上:Hypothesis 雙層資料模型、predicate 受控方式、Pattern 粒度與「可執行條件」要求、
  Decision Graph 的分界(人的 reject 做 / AI 演化不做)、學習深度定案、LLMProvider 抽象、聚合後餵 LLM。
- **Roadmap 的 ⚠️ 待拍板(第一版學習深度)於本文件定案**(第四節),task_plan 該項可對應更新。
