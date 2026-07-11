# Market Evolution System — 可執行 Roadmap v8

> 設計原則:**不是設計一個會做事的系統,而是設計一個會因證據而改變的系統。**

> **定盤星(比所有原則更上層):見下方「目的宣言」。每個決定先用它校準。**

v2 相對 v1 的三個結構性調整:
1. **Entity Model 前置** — Observation 必須觀察「某個 Entity」,否則資料懸空、不知道屬於誰。
2. **Insight 獨立成一層(Phase 2.5)** — Insight 的本質是 Information Reduction(資訊降維 / 語義壓縮),不是 AI 功能。它「描述看到了什麼」,與 Hypothesis「預測會怎樣」徹底分開。實作者可以是 Rule / Statistics / LLM。
3. **Decision Log 取代 Evolution Log** — Evolution 是結果,Decision 才是事件。記錄 approve/reject/rollback/freeze,並標記決策主體。

v3 相對 v2 的兩個調整:
4. **Provenance Chain 成為 Cross-cutting Concern** — 每一層都必須能回答「你從哪裡來?」。橫跨全系統,是 debug 的第一動作,也是「上游引用不可為空」的 schema 硬約束。
5. **Decision Log 升級為 Decision Graph** — 決策不是平行的事件,是彼此回應的樹(reject→retry→reject→retry→approve)。加 `parent_decision_id`,讓決策路徑可追溯。

v4 相對 v3 的兩個補充:
6. **目的宣言** — 放在最前面當定盤星(應用科學、目的優先、卡住先找 B/C 不急著否決)。
7. **武器盤點** — 把進攻武器作為 Phase 4 的「行為變數」正式寫進,並給出對 EscapeFlow 的優先序。

v5 相對 v4 的兩個補充:
8. **三問規則(模組準入閘門)** — 每增加一個模組前必過的三道關。
9. **敘事改為純正面陳述** — 拿掉所有「不是 X」,只寫我們要達到的。判準從「不要變成什麼」(審判結果)改為「要持續做到什麼」(檢查意圖)。

v6 相對 v5 的三個補充:
10. **Provider Agnostic 原則** — 與 Model Agnostic 並列的韌性原則:MES 可利用任何資料 provider,但核心能力不依賴任何單一 provider 存在。
11. **Cold Start 重新定義** — 不是「沒有資料」,而是「使用者只有產品知識,沒有市場知識」。這直接定義了 Discovery 層的第一件事:把產品知識轉成第一批可驗證的市場 Observation。
12. **MES 追求學習自主,不是資料自主** — 資料是手段,學習才是目的。資料可來自自己、市場、夥伴或第三方;唯一不能斷的是學習循環。(配套:Build vs Buy Matrix,獨立文件)

v7 相對 v6 的狀態對齊(把已拍板的「待拍板」補上,反映實際最新狀態):
13. **Phase 0 標為完成** — 五份文件全定稿(Entity Model v1 / Observation Schema v1 / Knowledge Schema v1 / Feature Taxonomy v1 / Principles 在本文件內)。
14. **Observation/Knowledge 拍板為兩張表** — Event Sourcing:Observation_Log(唯一真相,Append-Only)+ Knowledge_State(物化視圖)。原「待拍板」撤除。
15. **Discovery 正式列為 Deferred** — Discovery 是 Evolution 不是 Architecture;第一版寫死 Seed → Shopify Store。詳見新增的 Discovery 段落。
16. **Phase 1 標為 BLOCKED** — 合法資料取得途徑未定。詳見 Phase 1 的 blocker 記錄(已淘汰路徑 + 待驗證候選)。
17. Phase 3「學習深度」仍為待拍板(未變)。

v8 相對 v7 的決策(Jeff 定案):
18. **Phase 1 狀態改為 ✅ 執行中(Running)** — 這條路徑已通過「合法底線」與「三問規則」校準:不與對方防火牆硬撞,以 Inferred(推論能力)合法、低頻、禮貌地搜集資料。撤除 v7 的 BLOCKED 定性與 blocker 待驗證記錄,改為明確的工程執行規格(見 Phase 1)。

---

## 目的宣言(定盤星 — 比五原則更上層)

> 這段管的不是「系統怎麼建」,是「我們為什麼建、以及卡住時怎麼辦」。
> 每一個決定動手前,先用這三條校準。

**一、這是應用科學,不是基礎科學。**
基礎科學可以天馬行空、不切實際也是美麗的發想;但我們不走這條路。
我們的成敗標準在**外部**(有沒有服務到產品 / 有需要的人),不在內部(系統多精巧)。
一個再優雅的架構,若不能讓 EscapeFlow 多一個訂單,在這裡就是失敗的;
一個很笨但能帶來訂單的方法,反而是成功的。

**二、警惕「美麗糖衣」陷阱。**
目的是清楚的(換到訂單/客人),但結果是模糊的(會不會成、要等多久)。
正因結果模糊,人(和 AI)會不自覺飄向「把系統做得更完整、更優雅」——
因為那提供虛假的進度感和掌控感。多加一層抽象,立刻有成就感;
「這到底能不能帶來一個訂單」卻要等很久、可能失敗。
**每次想把系統做更複雜時,先問:這是在服務目的,還是在追求糖衣?**

**三、卡住時,先找 B/C,否決是最後手段。**
當某個方法(A)達不到目的時,第一反應**不是**否決,而是:
B 可以嗎?C 有沒有機會?或自創一個方法來達到同一個目的?
目的是錨,方法是可替換的船。
只有當 A、B、C 都誠實試過、確實都通不到目的時,「這條暫時放下」才是結論——
而且要說清楚是「所有路徑都不通」,還是「只是現在條件不成熟、之後可能可以」。

---

## 三問規則(模組準入閘門 — 每增加一個模組前必過)

> 這是把「目的宣言」變成操作程序。動手做任何新模組前,三問必須都有答案。

**問題一:它增加了什麼商業價值?**
用「不是 X,而是 Y」把「技術成就」翻譯成「人的負擔減少 / 成本降低」:
- Knowledge — 不是「AI 要有知識」,而是「減少重複分析市場」。
- Insight — 不是「AI 很厲害」,而是「人不用每天重新讀 500 筆資料」。
- Hypothesis — 不是「AI 能預測」,而是「降低錯誤嘗試的成本」。

說不出「人少了什麼負擔 / 省了什麼成本」的模組,就是純糖衣。

**問題二:它有沒有辦法被衡量方向?**
- Insight — 是否節省分析時間?
- Hypothesis — 是否提高回覆率?
- Weapon — 是否增加安裝?

注意:**門檻是「可被衡量方向」,不是「可被精算」**。
(對應第一版只做「有反應 / 沒反應」的粗判斷,不做精確歸因。)
連「粗略但方向明確的衡量方式」都說不出來的模組 → 不要做。

**問題三:如果拿掉它,系統還能不能運作? = Priority**
能越晚拿掉的,就越晚做。這直接排出依賴序:
- Observation — 不能拿(地基)
- Knowledge — 可以先不用
- Insight — 可以晚一點
- Hypothesis — 甚至可半年後
- AI Ranking — 可一年後

→ 隱藏好處:它告訴你「最小可運作系統」在哪。若只有 Observation 不能拿,
那第一個能跑、且有價值的版本,可能小到只有 Crawler → Observation_Log → 一個能看的介面。

---

## 我們要建的是什麼(正面陳述)

> 判準是「要持續做到什麼」(向前看、檢查意圖),不是「不要變成什麼」(審判結果)。
> 一個東西最後碰巧很酷、很聰明,那是副作用,不是罪;「為了酷而做」才是。
> 而「持續」這個要求本身,已自動排除了純炫技的東西 — 炫技品 demo 完就沒了,做不到持續。

**建立一個可以持續觀察市場、持續修正策略、持續提高成交率、持續增加營收的系統。**

四個「持續」對應認知循環:觀察 → 修正 → 成交 → 營收。

---

## MES 面對的起點:Cold Start 的定義

**Cold Start 不是「沒有資料」,而是「使用者只有產品知識,沒有市場知識」。**

- 使用者**有**:產品知識 — 我的產品/服務是什麼、大概賣給誰、優勢在哪(隱性、零散、未驗證)。
- 使用者**沒有**:市場知識 — 這個市場長什麼樣、誰是真正的目標、他們有什麼特徵、什麼訊號代表購買意圖。

**所以「資料為零」有兩種,要分清:**
- 使用者的產品知識 → 非零,但還不是系統可用的結構化 Observation。
- 系統的結構化市場觀測 → 才是真正的零。

**MES 的第一件事,就是把「產品知識」轉成「第一批可驗證的市場 Observation」。**
這定義了 Discovery 層的職責:**不是等使用者給名單,而是從使用者僅有的產品知識,推導出「去哪找、找什麼樣的第一批觀測對象」。**

註:此定義使 MES 不綁定任何特定產業。EscapeFlow(Shopify)剛好有成熟資料商可用,但一個 TA 是「25–45 歲女性上班族」的產品,沒有任何 Store Leads 等著它 —— MES 對這種情況同樣要能從 Discovery 開始自己建立第一批 Observation。這與 P6(Provider Agnostic)一體兩面。

---

## 核心視角:這是一個認知循環(Cognitive Loop),不是 AI Pipeline

```
Reality
   ↓
Entity        觀察的對象(Store / Product / ReviewApp / Theme / Contact / Campaign ...)
   ↓
Observation   感知 Perceive  — 原始觀測,Append-Only,五個 metadata
   ↓
Knowledge     記憶 Remember  — 正規化的中立事實,可查詢,保留歷史
   ↓
Insight       理解 Describe  — 資訊降維 / 語義壓縮(Rule / Statistics / LLM)
   ↓                          「我們看到了什麼?」仍是事實,可能永遠成立
Hypothesis    推理 Predict   — 基於 Insight 的賭注(LLM)
   ↓                          「這代表什麼、接下來會怎樣?」有 confidence/版本,會死
Experiment    行動 Test      — 單變數;行為本身是被測的變數
   ↓
Outcome       回饋 Evidence  — 市場的裁決,共同收斂到安裝/付費
   ↓
Evolution     學習 Adapt     — 用 Outcome 演化 Hypothesis,可回滾
```

**貫穿全程:Decision Graph** — 所有決策事件(approve / reject / rollback / freeze),記錄主體(Jeff / AI / 系統)、理由,以及 `parent_decision_id`(它在回應哪一次決策)。決策不是平行的 log,是彼此回應的樹。

每一層只有一個職責、一個動詞,可獨立評估、可獨立替換實作者。
**AI 不是系統的大腦,只是認知循環裡一個可替換的認知模組。** 智能在循環的結構,不在任何單一模組。

---

## 五原則(憲法)+ 兩條補充條款

1. **P1 市場永遠是對的** — 唯一最高裁判是 Outcome。市場不買單,權重就是 0。
   - 補充:**裁判需要足夠投票數才能宣判**。樣本不足時判決保留(held),不強行裁決。
2. **P2 Knowledge 永遠中立** — 只記 Evidence,不加判斷。事實與推論分開存。
3. **P3 Hypothesis 永遠可推翻** — 每條假說有版本與 confidence,失效則被冷藏、迭代,而非修正。
4. **P4 Model 沒有最好,只有目前最適合** — 模型是受僱解讀中性資料的分析師,在同一份事實上競技。
5. **P5 Everything Is Versioned** — 任何 Outcome 都能歸因到當時的 Knowledge/Model/Prompt/Hypothesis/Crawler 版本。
   - 補充:**分階段兌現**。第一版只版本化 Model / Prompt / Hypothesis;Knowledge 用 timestamp 天然表達版本;Crawler 先掛 git hash;全棧精細版本化等真需要全棧偵錯時再補。
6. **P6 Provider Agnostic** — MES 可利用任何資料 provider(Store Leads / BuiltWith / Google / Crawler / Partner API / CSV / Manual Import 都是 Provider),但**核心能力不依賴任何單一 provider 存在**。
   - 入典句:**「MES 會優先利用已存在且可靠的市場資料,以減少重複建設;但 MES 的核心能力,不應依賴任何單一資料提供者存在。」**
   - 兩個精神:應用科學(有現成的就用,不重造輪子)+ 系統韌性(Store Leads 倒了,MES 不會倒,只換一個 Discovery Provider)。
   - 與 P4(Model Agnostic)並列:P4 不讓任何模型成為唯一前提,P6 不讓任何資料源成為唯一前提。

**貫穿全部原則的一句話:MES 追求的是「學習自主」,不是「資料自主」。**
資料是手段,學習才是目的。資料可來自自己、市場、夥伴或第三方;真正不能斷、不能依賴外部的,是學習循環本身(Observation→...→Evolution)。

---

## Cross-cutting Concern:Provenance Chain(來源鏈)

> 不是 Phase,不是 Principle。它橫跨所有層,是每一層都必須遵守的契約。

**核心:每一層的每一筆記錄,都必須能回答「你從哪裡來?」**

```
Outcome → Experiment → Hypothesis → Insight → Knowledge → Observation → Entity
```

**debug 哲學(這條鏈存在的根本理由):**
未來系統出問題時,第一句問的**不是**「AI 為什麼這樣判斷?」(無法回答,進黑盒),
**而是**「這個結論一路追下去是怎麼產生的?」(有限步驟,必有答案)。

順著鏈往下走,一定能定位錯在哪一層,而每一層的錯對應一種完全不同的修法:
- Observation 抓錯 → 資料源問題
- Insight 摘要錯 → 觀察問題
- Hypothesis 推論錯 → 推理問題
- Experiment 執行錯 → 行為問題

**與 P5 的關係(互補的兩個維度,非重複):**
- P5 是**垂直的(時間軸)**:同一個東西,歷史版本怎麼變的?
- Provenance 是**水平的(因果鏈)**:同一時間點,這個結論由哪些上游推導出?
- 完整追溯 = 兩者交叉:「這個 Outcome 在某時用 Hypothesis v8.1(P5),而那假說往上追是基於 Insight#451 → Knowledge 某切片 → 某幾筆 Observation(Provenance)」。

**對 schema 的硬約束(上游引用不可為空):**

| 層 | 必須引用的上游 |
|---|---|
| Entity | 鏈的起點,無上游 |
| Observation | entity_id |
| Knowledge | 來自哪些 observation_id |
| Insight | 基於哪些 knowledge_id + generated_by |
| Hypothesis | 基於哪些 insight_id |
| Experiment | 測試哪個 hypothesis_id |
| Outcome | 屬於哪個 experiment_id |
| Decision | 針對哪個對象 + 主體 + parent_decision_id |

這條規則是**結構上的鐵律**(寫入時上游引用為空就拒絕),不是「希望大家記得維護」的善意。
Provenance 因此不是口頭原則,是結構上不允許斷裂的契約。

---

## Cross-cutting Concern:Decision Graph(決策樹)

> Decision 不是一筆筆獨立的 Log,是彼此回應、有因果父子關係的樹。

**例:一個假說的決策路徑**
```
Hypothesis A
  → Jeff Reject
    → AI Retry
      → Jeff Reject
        → Claude Retry
          → Approve
```
每個 Decision 都是**對前一個 Decision 的回應** → 用 `parent_decision_id` 記錄這棵樹。

**它與 Provenance 的方向不同,互補:**
- Provenance 往**資料層**上游追(這結論基於哪些 Insight/Knowledge/Observation)
- Decision Graph 往**決策史**上游追(這個 approve 之前,經歷哪些 reject/retry)

**它能回答 Provenance 給不了的訊號:**
- 一個假說若是「被 reject 兩次才勉強 approve」,那它失敗或許不意外 — 這是「決策掙扎過程」才看得出的訊號。
- 未來可分析:被 reject 多次才通過的假說,最終 Outcome 是否較差?哪個模型 retry 的成功率高?
  → 這是評估「人類 × 不同模型互動品質」的原始資料,呼應模型競技場。

**schema 要求:** Decision 表加 `parent_decision_id`(可為空,根決策無父),記錄 `決策對象 / 主體 / 理由 / 時間`。

---

## 通則:每個 Phase 的驗收精神

> 驗收的不是「功能會動」,是「資料/結構是乾淨的」。

每個 Phase 結束前問三題,任一題答不出「沒問題」就不進下一階段:
1. 產出有沒有違反五原則任一條?
2. 下一個 Phase 要依賴的東西,是否已乾淨備齊?
3. 現在停下,已做的會不會變成「之後一定要重寫」的技術債?

---

## Phase 0 — 設計地基(這一週,不寫任何程式)

**目的:** 把「最難改、改了最痛」的資料層定義定死,讓上層認知模組(Insight/Hypothesis 的各種實作者)可插拔。

**進入條件:** 五原則已定稿(✅)

**產出(有嚴格依賴順序):**

| 順序 | 產出 | 反悔成本 | 為什麼這個順序 |
|---|---|---|---|
| 1 | **Entity Model** | **最高** | 一切的根。Observation 掛在 Entity 上;定錯則全錯 |
| 2 | Observation Schema | 高 | Append-Only,第一天的結構=歷史的結構 |
| 3 | Knowledge Schema | 中 | 依賴 Observation Schema |
| 4 | Feature Taxonomy v1 | **最低(刻意)** | Feature 用彈性結構可動態新增,故可做小做粗 |
| 5 | Principles(已完成) | — | 憲法,其餘四份都要能追溯到它 |

**Phase 0 狀態:✅ 完成(五份全定稿)。** 對應實體文件:
`MES_Entity_Model_v1.md`、`MES_Observation_Schema_v1.md`、`MES_Knowledge_Schema_v1.md`、
`MES_Feature_Taxonomy_v1.md`、Principles(在本 Roadmap 內)。另有配套 `MES_Build_vs_Buy_Matrix_v1.md`。

### Entity Model 的設計要點(v2 新增,最重要)

- **Entity = 被觀察的對象。** 候選:Store、Product、Theme、ReviewApp、ReviewWidget、Company、Contact、Campaign、Experiment。
- **Entity 之間有關係**:一家 Store 有多個 Product、用一個 Theme、裝一個 ReviewApp、那個 App 渲染一個 ReviewWidget。
- **Knowledge 永遠掛在某個 Entity 上** → Observation Schema 必須有 `entity_type + entity_id`。
- **好處:可在任何粒度聚合**。例如把 Observation 掛在「ReviewApp = Loox」這個 Entity 上,就能查「所有用 Loox 的店,平均 widget 載入時間」這種跨店洞察 — 沒有 Entity 層,只能以單店為單位看資料。

### Observation / Knowledge 邊界(✅ 已拍板:兩張表)

- Observation = crawler 抓回的原始觀測(raw,Append-Only,永不修改)
- Knowledge = 正規化/可查詢的知識層(從 Observation 衍生)
- **✅ 拍板:兩張實體表(Event Sourcing 模式)。**
  - `Observation_Log` = 事件日誌 = 唯一真相(source of truth),Append-Only。
  - `Knowledge_State` = 物化視圖(materialized view),為查詢效能而存在,永遠可從 Observation_Log 重建。
  - 鐵律:任何新觀測必先 append 進 Observation_Log,再更新 Knowledge_State;絕無「直接改 Knowledge_State」的後門。
  - 詳見 `MES_Knowledge_Schema_v1.md`。

### Observation 的五個 metadata(第一天就強制)

觀測了什麼(Feature)/ 值(Value)/ 怎麼觀測到的(Source)/ 何時(Timestamp)/ 多可信(Confidence)
+ **歸屬**(entity_type + entity_id)

**驗收標準:**
- 能拿一家真實的店,在紙上(不寫程式)把它的特徵手動填進 schema,無「不知道存哪欄」的卡頓。

**停止條件:**
- Entity 關係沒定清 / Observation 邊界沒劃清 / 五 metadata 沒釘死 / Feature 還寫死在 schema → 都不進 Phase 1。

---

## Phase 1 — Crawler → Observation Log(每天乾淨累積)

> **實作狀態:✅ 執行中(Running)。** 此路徑已通過「合法底線」與「三問規則」校準:
> 不與對方防火牆硬撞,以 **Inferred(推論能力)**合法、低頻、禮貌地搜集資料。

**目的:** 證明「持續產生乾淨、中立、結構正確、掛在正確 Entity 上的觀測」能穩定運作。

**進入條件:** Phase 0 五份定稿(✅ 已達成)。

---

### 工程基線

- 語言 / 套件管理:**Python 3.12 / uv**
- 資料庫:**PostgreSQL 16(Docker)**
- ORM:**SQLAlchemy 2(Async)**
- Migration:**Alembic**
- 測試:**pytest**
- 物理路徑:Mac Mini M4 本地 `/Users/cashflow/Documents/MES/`

**裁剪原則(拿掉所有多餘抽象,只做最純粹的資料地基):**
- 暫時**不建 FastAPI**、**不建 Dashboard**、**不建 AI 模組**。
- 這一階段只證明一件事:能不能穩定產生乾淨、可追溯、掛在正確 Entity 上的 Observation。

---

### 自動化輸入源與推論鏈路(具體執行做法)

**1. Shopify App Store 評論區 Scraper**
- 嚴格遵守 `apps.shopify.com` 的 robots.txt 速率限制。
- 採 **5–25 秒隨機隨眠(time.sleep)**,低頻、禮貌地抓取 Loox 評論區的 **Store Name**。

**2. Inference 引擎(Name → Domain)**
- 將 Store Name 加上後綴關鍵字,透過網頁搜尋引擎(第一版實作:DuckDuckGo 網頁版解析)進行 **Inferred 推論**。
- 用正則表達式(Regex)蒸餾出第一筆網址 Domain。
- 註:此步驟的「推論能力」是核心;所用的搜尋源是**可替換零件**(呼應 P6 Provider Agnostic)—— 若某搜尋源不穩或失效,換零件不動架構。第一版實作零件的實際可行性由 Claude Code 在 M4 上實測確認。

**3. Normalize 規範**
- Domain 一律:轉小寫 / 去 `https://`、`http://` / 去 `www.` / 去 trailing slash 與 path / 去 port。
- 轉化為唯一的 `canonical_key`,寫入 `Entity` 表。
- 規範同 `MES_Entity_Model_v1.md` 第四節。

**4. Event Sourcing 硬約束(強引用來源鏈 / Provenance Chain)**
- 任何觀測資料**必先 append 寫入 `Observation_Log` 表**。
- **`entity_id` 絕對不可為空**(Provenance 硬約束:寫入時為空即拒絕)。
- 隨後才投影至 `Knowledge_State` 物化視圖。**絕無「直接改 Knowledge_State」的後門。**

**5. 失敗三值語義(失敗訊號絕不偽裝)**
- 抓取成功 → `observed`
- 網路或解析失敗 → `fetch_failed`
- 確認不具備該特徵 → `not_found`
- 三值在 schema 層即區分,程式無法混淆(同 `MES_Observation_Schema_v1.md` 第三節)。

---

### 第一版抓取的 Feature 範圍

依 `MES_Feature_Taxonomy_v1.md`(9 個 feature):
- Technology Stack:`uses_review_app`(entity_ref)、`theme_name`
- Business Signals:`product_count`、`avg_price`、`price_range`(從 /products.json)
- Company:`country`、`language`、`currency`
- Store Status:`is_active`

**暫不抓:** Performance / Growth / Pain / Market(理由見 Taxonomy 文件的留白說明)。

**ReviewApp Signature Library v1:** loox / judgeme / yotpo / okendo / stamped。

---

### 判斷依據(每筆 Observation 必須)

- 五 metadata + entity 歸屬全填。
- **Append-Only**:再次觀測 = 新增帶新 timestamp 的一筆,絕不 Update 覆蓋。
- **抓取失敗 ≠ 沒資料**:失敗明確記為 `fetch_failed`,不可偽裝成 `not_found` 或 0。

### 驗收標準

- 連續 7 天每天自動跑、有新增,且五 metadata 齊全、Append-Only 沒覆蓋、entity 歸屬正確。
- → 不是「能抓」就成功,是「抓進來的資料乾淨且結構對」才成功。

### 停止條件

- 出現「失敗被記成 0/無」、Update 覆蓋、metadata 缺漏 → 立即停修。

---

## Discovery — Status: Deferred(刻意不現在設計)

> Discovery = 「去哪找、找什麼樣的第一批觀測對象」。

**為什麼 Deferred(不是不重要,是現在缺燃料):**
- Discovery 的每條「去哪找」規則,本質是**一個待驗證的假設**(Hypothesis 性質),不是既定事實。
  例:「用 review app 的 Shopify 店值得找」是假設,要跑市場才知道對不對。
- 沒有真實 Observation,Discovery 只能猜 —— 如同沒有 Knowledge 的 Insight 只能猜。
- 所以 **Discovery 本身是 Evolution,不是 Architecture**:它要吃 Observation 當燃料才能演化。

**第一版寫死:** `Seed → Shopify Store`(不做通用 Discovery,避免紙上推演出「自以為通用、其實只是 EscapeFlow 專用」的 Discovery)。

**未來形態(預期,不現在建):** 待真實 Observation 累積後,Discovery 很可能長成認知循環的一個應用
(「哪裡能找到高價值觀測對象」本身是一條 Hypothesis,走一樣的 Experiment→Outcome→Evolution),
而非獨立模組。例:從單一 review app,被市場逼著長成 Agency / Shopify Experts / Theme Developer / Migration Consultant。

**與 Cold Start 定義的接點:** Discovery 的職責 = 把使用者的「產品知識」轉成「第一批可驗證的市場 Observation」。
(商品化後才需要的「Seed Hypothesis 擷取 / onboarding 引導」屬更後面的模組,現在不碰。)

---

## Phase 2 — Knowledge Engine(Observation 正規化為可查詢 Knowledge)

**目的:** 讓原始觀測變成可查詢、可算變化的中立知識層。

**進入條件:** Phase 1 通過 7 天乾淨累積驗收。

**Normalize 做什麼(待 Domain Model 拍板,候選):**
- 單位統一(幣別、時間格式)。
- 同一 feature 多次/多來源觀測的取值邏輯(取最新?取最高信心?都保留?)。
- **不做任何判斷**(不標「高價值/高風險」)— 守 P2。

**驗收標準:**
- 能查詢「某 Entity 的某 feature 隨時間的變化序列」→ 證明 Append-Only 歷史讀得出來,Growth 原料齊了。

**停止條件:** Normalize 混入判斷/評分 → 違反 P2,停;歷史查不出來 → Append-Only 沒生效,停。

---

## Phase 2.5 — Insight Engine(資訊降維 / 語義壓縮)★ v2 新增獨立層

**目的:** 把 Knowledge 濃縮成「描述看到了什麼」的 Insight。這是 **Describe**,不是 Predict。

**核心定義:Insight = Information Reduction,不是 AI 功能。**
- Insight 把 200 個 Feature 濃縮成 5 個 Observation(例:「高速營運型商店」「review 成長快」)。
- 它仍是**事實的摘要**,可能永遠成立 — 與會死的 Hypothesis 性質完全不同。

**三種實作者(第一版刻意先不用 AI):**
- **Rule**:`IF product_count > 500 → High SKU`
- **Statistics**:`最近 30 天 review 成長率 +28% → Growth`
- **LLM**(後加):`這家店正在快速擴張`(需語義理解時才補)

**第一版用 Rule + Statistics 就能做 ~80%。** AI 作為「另一個可插拔的 Insight 實作者」,在 Phase 3 之後才加入並接受評估。

**每個 Insight 要記 metadata(與 Observation 同精神,來源可追溯):**
- 內容 / **產生者**(rule_v1 / claude / gpt / stat_v1)/ 基於哪些 Knowledge / 時間 / 信心

**這帶來的關鍵能力:非 AI 基準線 + 觀察力競技場。**
- Rule 已能產生 15 個 Insight = LLM 必須證明自己能產出「Rule 產不出、且下游被市場驗證有用」的 Insight。
- 同一份 Knowledge:Rule 15 / GPT 18 / Claude 22 / Gemini 17 → 比較的不是 prompt,是**觀察力**。

**驗收標準:**
- 純 Rule + Statistics 能對一批店穩定產出結構化 Insight,每個帶產生者與來源。
- Insight 中沒有混入任何「預測」(預測屬於 Hypothesis 層)。

**停止條件:** Insight 裡開始夾帶預測/賭注 → 停,Describe 與 Predict 混了,正是要避免的黑盒。

---

## Phase 3 — Hypothesis Engine(AI 進場做預測)

**目的:** 第一次讓 AI 做「會死的預測」。AI 只扮演 Observation/Knowledge/Hypothesis 角色,不做 Decision。

**進入條件:** Phase 2.5 通過,Insight 層乾淨且可作為輸入。

**產出:**
```
Insight  →  AI  →  Hypothesis(特徵組合 → 預測 + confidence + evidence)
                   ↓
         Jeff: Approve / Reject / Comment
```

**判斷依據(每條 Hypothesis 必須):**
- 結構化,不是散文:`Beauty + Loox + ReviewGrowth → 較易接受 Migration(confidence + evidence 指向哪些 Insight/Knowledge)`。
- **引用它基於哪個 Insight** → 假說失敗時可診斷「是 Insight 錯,還是 Insight→Hypothesis 的推論錯」。
- Pain Signals 掛 evidence 鏈(推論,非事實)。
- 可審核;**Jeff 的 reject 是一次 Decision 事件,記進 Decision Graph**。

**P5 第一版兌現:** 版本化 Model / Prompt / Hypothesis;Knowledge 用 timestamp;Crawler 掛 git hash。

**第一版「學習」深度(建議,待拍板):**
- 建議「只記錄 + 累積驗證次數」,confidence 先不自動裁決(守 P1 held)。
- schema 預留「調信心度」與「長新假說」,第一版不開啟。

**驗收標準:**
- 假說結構化、帶 evidence、引用 Insight、可審核;reject 進 Decision Graph。
- 換模型(GPT↔Claude)讀同一份 Knowledge/Insight,能各自產生假說 → P4 地基成立。
- **可分別評估**:哪個模型觀察力強(Insight)、哪個推理力強(Hypothesis)。

**停止條件:** AI 把推論當事實寫進 Knowledge / 假說無 evidence 或不可審核 / AI 做 approve 以外的決策 → 停。

---

## Phase 3.5 — 接觸前置條件(平行進行,不阻擋 Phase 0–3)

- **Reddit 帳號養成**:真實參與累積 karma(進行中)。
- **Cold email 合規 + 獨立發信網域**:若用 email,需獨立網域,絕不用 reviews@escapeflow.app。
- **UTM → Shopify Partner API 歸因鏈**:確認通的。
- **本地部署備份 + 防駭**:資產是無法重建的累積觀測,備份是地基等級必要。

**商品化紅線(封存,觸發點明確):**
- 當「系統對 EscapeFlow 真的有用、決定商品化」時,才討論資料搬遷(自建/雲端)與持有商家機密的安全合規。在那之前不碰。

---

## Phase 4 — Experiment + Outcome(真實接觸,同步收反饋)

**進入條件:** Phase 3 通過 + Phase 3.5 至少一種合規、可收反饋的接觸行為就緒。

**關鍵約束:**
- **Experiment 與 Outcome 必須一起做**:沒有同步反饋機制,接觸資料就永遠丟失(資料黑洞)。
- **單變數原則**:一次只測一個變數(A/B 核心,隔離變數才能歸因)。
- **行為是變數**:六種接觸行為(cold email / 內容引力 / 聯絡表單 / 社群接觸 / 精準投放 / App Store 機制)本身是被測對象。
- **共同成效尺**:不同行為反饋形狀不同,都收斂到共同終點(UTM → 安裝)。第一版不做精確歸因,只做「有反應 / 沒反應」。

**每個 Experiment 記錄(對應 P5):** 用了哪條 Hypothesis(版本)/ 哪個 Model+Prompt(版本)/ 讀的哪個時間點 Knowledge / 行為類型 / UTM / 假設(預測)。

**判斷依據:** 內容發出前 Jeff approve;高風險行為(cold email)押後,先用低風險行為起步;每個 Experiment 帶「假設」欄。

**驗收標準:**
- 跑通一個完整循環:假設 → 行動 → Outcome → 綁回 Experiment 綁回 Hypothesis。
- 哪怕只接觸十幾家、反饋很粗,只要「特徵→行為→結果」鏈完整就成功 = 親手驗證「系統會學習」。

**停止條件:** Experiment 做了但 Outcome 收不到 / 一次動多變數 / 樣本不足卻下結論 → 停。

### 武器庫(Phase 4 的「行為變數」具體清單)

> 武器不分好壞,只分「現在這個階段、這個產品,哪個划算」。
> 每種武器用三屬性量:**觸及誰(精準度)/ 付什麼代價(成本+風險)/ 回饋多快多清楚(可學習性)**。
> 對 Evolution System 而言,第三個尤其關鍵 — 武器的回饋就是系統的學習燃料。
> **所有武器的共同轉換終點都是 App Store listing。** listing 不行,前面所有火力都在最後一哩漏掉。

**A 類 主動推送型(你去敲門)**
- Cold Email:精準、有合規+網域信譽風險、回饋清楚。需獨立發信網域(絕不用 reviews@escapeflow.app)。
- 聯絡表單:精準、無網域風險、難規模化。適合少量精準。
- 社群主動接觸:半精準、軟性、易突兀、難規模化。

**B 類 內容引力型(讓對的人來找你)**
- 內容 / SEO(教學/比較/遷移指南):不精準但對的人會搜到、無合規風險、回饋慢且模糊、**複利強**。
- 影音(demo/對比):製作成本高、信任建立快。
- Build in Public:最真實、最低成本、最符合品牌(不造假)。

**C 類 付費放大型(花錢買觸及)**
- App Store 廣告:精準(就在商家找 app 處)、要花錢、回饋清楚、**離安裝最近**。
- Google/Meta 精準投放:可鎖定 Shopify 受眾、要花錢且要會操作、回饋清楚。

### 對 EscapeFlow 的優先序(現在的處境:剛上架、零付費、一人、預算有限)

| 順位 | 武器 | 目的(換到什麼) | 為什麼是這個順位 |
|---|---|---|---|
| 1 | **App Store listing 優化**(免費) | 讓被帶來的人真的按安裝 | 所有武器的共同轉換終點;免費;放大其他所有武器 |
| 1 | **Build in Public + 內容** | 零成本、不造假地讓對的人知道存在 | 手上有別人沒有的彈藥(真實開發歷程);無預算/網域/合規門檻 |
| 2 | **App Store 廣告**(小錢測) | 用最少錢買「商家正在找 review app」的曝光 | 離安裝最近、回饋最清楚,完美餵 Evolution System |
| 3 | **Cold Email** | 雷達+落地頁備好後,主動帶來高意圖訪客 | 最強主動武器,但需先備獨立網域+合規+Taxonomy |
| 暫緩 | 影音、大規模付費投放 | — | 成本太重 / 在不知彈道前亂開槍 |

**武器如何接進系統:** 武器 = Phase 4 Experiment 的「行為」變數。系統的設計本來就是要「測出」哪種武器對哪種商家有效,而不是賭定一個。
```
Hypothesis(這種商家、這個痛點值得打)
  → Experiment:用哪種武器去打 ← 武器是這裡的變數(單變數原則:一次測一種)
  → Outcome:每種武器各自回饋,都掛 UTM,收斂到安裝
  → 學到:對「這種商家 × 這個痛點」,哪種武器最有效
```

**若只能先做一件事:把 App Store listing 優化到位。** 它是所有武器的共同瓶頸,免費,且放大其他所有武器的效果 — 完全符合目的宣言(投報率最高、最不「酷」、最服務目的)。

---

## Phase 5 — Evolution(用 Outcome 演化 Hypothesis)

**目的:** 系統真正「因證據而改變」— 靈魂兌現。

**進入條件:** Phase 4 累積足夠 Outcome(每條假說達最低樣本量),此時解除 P1 held,市場裁決生效。

**演化三層深度(逐步開啟):**
1. 調信心度:Outcome 回來自動重算 confidence(達樣本量才生效)。
2. 長新假說:AI 分析「為何此假說在此案例失敗」,催生更精細的新假說(Variation)。
3. 市場選擇:成功保留(Retention),失效冷藏/封存(Selection);不是修正,是迭代。

**達爾文框架:** Variation(AI/Jeff 提多版本,求多樣)→ Selection(市場決定,P1)→ Retention(成功進 Knowledge/Hypothesis)。人類 reject = Decision 事件,進 Decision Graph;未來 AI 再提同樣建議,系統能說「這曾被否決,要重檢嗎?」

**驗收標準:**
- 出現第一個「假說因新證據改變 confidence,並導致 Jeff 調整方向」的完整事件。
- 全程可追溯(P5)+ 可回滾(Append-Only + Decision Graph)。
- → 系統第一次「因證據而改變自己」,且改變可解釋、可回滾。

**停止條件:** 樣本不足就裁決生死 / 演化不可追溯或不可回滾 → 停。

---

## 副產品:一個用真實市場結果評測 AI 認知能力的平台

因 Insight 與 Hypothesis 拆開,系統能分別回答:
- 哪個模型最會**發現市場現象**(Insight 品質)
- 哪個模型最會**提出可驗證假說**(Hypothesis 品質)
- 哪個模型的假說**最終最常被市場證實**(Outcome 命中率)

這三個是不同能力,現實中未必同一模型最強。一般 AI 評測用靜態 benchmark(考試);這個系統用真實世界的因果後果當裁判。其前提正是 P2(中立 Knowledge)+ P5(全棧版本化)— 地基已備。

---

## 全局判斷尺(每個決策都量一次)

0a. **目的優先** — 這一步是在服務目的(換到訂單/客人),還是在追求糖衣(系統更優雅)?
0b. **卡住先找路** — 遇到困難,我有沒有先找 B/C 或自創方法,還是急著否決?
0c. **三問閘門**(新模組才需)— 增加什麼商業價值?可被衡量方向嗎?拿掉還能運作嗎(=Priority)?
1. 市場是唯一裁判(P1)— 最後是否交給市場,而非 AI/Jeff 的自戀?
2. Knowledge 中立(P2)— 有沒有把判斷混進事實?
3. 假說可推翻(P3)— 有版本和 confidence、能被未來證據冷藏嗎?
4. 模型只是分析師(P4)— 是不是把 AI 當神諭了?
5. 全部版本化(P5)— 能回答「在什麼 Knowledge/Model/Prompt/Hypothesis 下得到的」?
6. Provider Agnostic(P6)— 這個設計有沒有把 MES 的存活綁死在某個單一資料源上?這個 provider 賣的是資料、能力、時間,還是專有管道?(用 Build vs Buy Matrix 判)
7. 突破 vs 順風車 — 是搭現成工具衝紅海,還是突破痛點、建別人沒有的?
8. 真實規模對齊 — 是不是在零/少數據上,蓋需要大量數據才有意義的東西?

---

## 一句話總結

> 從最無聊、最底層、最難改的 Entity/Observation 開始,把資料層做成中立、可追溯、掛在正確對象上的事實;
> 用 Rule/Statistics 先把 Insight 跑起來,讓 AI 成為可插拔、可被市場評分的認知模組;
> 每一層一個職責、一個動詞,Describe 與 Predict 徹底分開。
> 每個 Phase 驗收的不是「功能會動」,是「資料是乾淨的」。
> 因為乾淨、可演化、可回滾的資料,才是這個系統唯一抄不走的資產。
