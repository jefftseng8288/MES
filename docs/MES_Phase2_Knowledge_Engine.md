# MES Phase 2 — Knowledge Engine 拆解設計

> 本文件把 Roadmap v8 / task_plan 裡「Phase 2」那幾條摘要,拆解到「可實作」的層級。
> **不改變 Phase 2 的方向**(投影 Observation→Knowledge、守 P2 中立、時間序列、可重建),
> 只把抽象工作項拆細,並補上實作必然撞到、但原摘要沒寫的東西(schema 擴充 / 取值細節 / 投影時機 / 失敗處理)。
> Jeff 已定案的三個設計決定,寫在第一節,是本 Phase 的靈魂。

---

## 一、三個定案決定(Phase 2 的靈魂,Jeff 定)

### 決定 1:取值時「新鮮度(Time)優先於信心度(Confidence)」
- 一個 entity 的一個 feature 有多筆 observed 觀測時,**取最新那筆**,即使它 confidence 較低(inferred),
  也優先於較舊但 confidence 較高(certain)的。
- **理由:資料是流動的。** 半年前 certain 的「用 Loox」,在「這家店現在用什麼」這問題上價值可能為負
  —— 它會讓我們基於過期事實做動作(發信說「你在用 Loox」),對方早換成 Judge.me,已讀不回,
  且造成歸因困境(是資料過期還是動作無效?)。寧可要「最新、帶推論成分的現況」,
  不要「確定、但已入土的歷史」。
- **取值優先序:** `status(只取 observed)` → `新鮮度(observed_at 最新)` → `confidence(僅在同時間 tiebreaker)`。

### 決定 2:fetch_failed 時「保留舊值,但誠實標明新鮮度與當前狀態」
- **理由:`fetch_failed` 是「系統失能」(我們沒戳到,如被 Cloudflare 擋、本地網路斷),不代表商家變了。**
  商家真的變了會是一筆新的 `observed`。若因一次 fetch_failed 就抹去累積的舊值 → 狀態會因網路波動一直閃爍歸零,系統極脆弱。
- **投影做法:** 保留上次成功觀測的舊值,但 metadata 同步更新以誠實反映:
  - `value` = 上次 observed 的值(如 product_count=196)
  - `last_observed_at` = 上次**成功觀測**的時間(知道這值多舊)
  - `current_status` = 最近一次**嘗試**觀測的結果(observed / fetch_failed)
- **效果:** 未來篩選店家時,能精準挑「last_observed_at 在 N 天內 **且** current_status=observed」的店,
  自動過濾掉 current_status=fetch_failed(可能過期)的,不對它們做動作。

### 決定 3:投影時機 = 定時批次(每天 23:30 台灣,警鈴 23:50 之前)
- **理由:守 CLAUDE.md「拒絕不必要的即時性」。** 一天三批的資料量,不需要即時投影。
  - 不選增量(每筆 observation 觸發重算):要 DB trigger/事件監聽,架構複雜,資料量不值得。
  - 不選純手動:違反「無人看管自動跑」。
  - **定時批次是正解。**
- 時機 23:30,在警鈴(23:50)之前 —— 先把當天 observation 收斂成 knowledge_state,再體檢。

---

## 二、knowledge_state schema 擴充(★ 由決定 2 導出,原摘要沒有)

決定 2 要求 knowledge_state 能同時回答三件事,現有 schema 需擴充來承載
(趁 knowledge_state 幾乎空表,擴充成本低):

1. **當前值是什麼** —— `value`(既有的 discriminated union value 欄,取自最新 observed,依決定 1)
2. **這值最後一次成功觀測何時** —— `last_observed_at`(新增;= 被取為當前值那筆 observed 的 observed_at)
3. **最近一次嘗試觀測的結果** —— `current_status`(新增;observed / fetch_failed / not_found 之一;
   = 該 (entity, feature) 最近一筆 observation 的 status,不論成敗)

**關鍵區分(實作要精確):`value/last_observed_at` 和 `current_status` 是兩個不同時間點:**
- `value` + `last_observed_at` = 「最後一次**成功**觀測的值,和那次成功的時間」
- `current_status` = 「最近一次**嘗試**觀測的結果」(可能是更晚的一次 fetch_failed)
- 例:product_count=196 於半年前 observed;今天去戳 fetch_failed。
  則 value=196、last_observed_at=半年前、current_status=fetch_failed。

**其他既有欄位保持:** source_observation_id(指向被取為當前值那筆 observed)、
discriminated union value 欄(value_type + typed 分欄)、producer 等 —— 與 Observation 同構,投影不做型別轉換。

**schema 擴充要點:**
- [ ] knowledge_state 加 `last_observed_at`(timestamp) + `current_status`(受控字串 CHECK,沿用三值)
- [ ] 趁空表用 migration 擴充;若已有零星投影資料,清空重投(空表擴充成本近零)
- [ ] 同步更新 `docs/MES_Knowledge_Schema_v1.md`(加這兩欄 + 語義說明 + 決定 2 的理由;版號依規矩處理)

**★ DB 層 CHECK 約束(物理防禦「不老實的混合狀態」,不信任投影代碼)**

決定 2 描述了 last_observed_at / value / current_status 該有的關係,但「描述」不等於「強制」。
投影代碼一旦寫錯,就會產生騙人的資料(如「從沒成功卻有值」的鬼值、「曾成功卻沒值」的空洞)。
用 CHECK 物理鎖死合法狀態組合(同 observation_log 的 discriminated union CHECK 哲學:不信任代碼,DB 拒絕不合法組合):

- **規則 1(從無觀測成功過):** `last_observed_at IS NULL` → `value` 必須 NULL,
  且 `current_status` 只能是 `fetch_failed` / `not_found`(不能是 observed)。
  —— 防止「從沒成功卻有值」的鬼值。
- **規則 2(曾觀測成功過):** `last_observed_at IS NOT NULL` → `value` 必須非 NULL;
  `current_status` 可為任何值(observed=現在也成功 / fetch_failed=以前成功這次失敗 / not_found=現在確認沒有了)。
  —— 防止「曾成功卻沒值」的空洞。

- [ ] 新增 knowledge_state DB 層 CHECK,將 `last_observed_at IS NULL` 與 `value IS NULL` 狀態強綁定
  (含 current_status 的合法取值),防範投影引擎產生不老實的混合狀態。
  (注意:value 是 discriminated union,「value IS NULL」指所有 typed 欄 + value_raw 皆 NULL;CHECK 要涵蓋這點。)

---

## 三、投影引擎(Knowledge Engine 核心)

- [ ] 實作投影:讀 observation_log,對每個 `(entity_id, feature)` 組合,依取值邏輯算出當前 knowledge_state 一列
- [ ] **取值邏輯(依決定 1、2):**
  - 候選 = 該 (entity, feature) 的所有 **observed** 觀測
  - 若有 observed:取 `observed_at` 最新那筆為 value(同時間才用 confidence tiebreaker);
    寫 value / value_type / typed 欄 / source_observation_id / last_observed_at(= 該筆 observed_at)
  - `current_status` = 該 (entity, feature) **最近一筆 observation**(不論 observed/fetch_failed/not_found)的 status
  - 若從無任何 observed(只有失敗):該 feature 無 value(不投影出值),但可記 current_status 反映「一直沒成功」
- [ ] **country 特例(Jeff 定案,理由如下):** country 用「**時間優先,但低 confidence 的新值不覆蓋高 confidence 的舊值**」。
  - **理由:country 是極度剛性的物理屬性。** Shopify 店的註冊國家牽涉稅務與金流(Shopify Payments),
    在店的生命週期裡幾乎不變。所以一筆舊的 `certain`(從 `Shopify.country` 直讀)遠比一筆新的 `inferred`(如用 IP 庫猜伺服器位置)精確。
    例:半年前直讀到註冊在 US(certain);今天用 IP 猜伺服器在 CA(inferred)。伺服器託管在加拿大 ≠ 公司在加拿大
    —— 若讓時間無條件優先,會把美國老闆誤認成加拿大人,產生無效動作。
  - **精確定義(不是「country 不看時間」,而是「剛性事實不被猜測污染」):**
    - 新觀測 confidence **≥** 舊當前值的 confidence → 照決定 1 時間優先(新的 certain 仍贏舊的 certain,新事實贏舊事實)。
    - 新觀測 confidence **<** 舊當前值的 confidence(如新 inferred vs 舊 certain)→ **不覆蓋**,保留舊的高 confidence 值。
  - 這讓 country 與主原則(決定 1)自洽:時間仍優先,只是「低 confidence 新猜測擋不住高 confidence 舊直讀」,保護剛性屬性。
  - **實作注意:** 這個「低 confidence 不覆蓋高 confidence」的規則,第一版只套用在 country;是否推廣到其他剛性 feature,待未來證據決定(現在不預先推廣)。
- [ ] **Normalize(單位統一):** 幣別、時間格式等統一。**只做正規化,不做任何判斷/評分**(守 P2)
- [ ] **守 P2 中立(鐵律):** 投影/Normalize 絕不標「高價值/高風險」、不排序、不評分。只做「取值+正規化」。

---

## 四、投影時機與排程(依決定 3)

- [ ] 定時批次:每天 **23:30 台灣**,獨立程序統一重算投影(launchd,類似 alarm daemon 的掛法)
- [ ] 投影方式:對「當天有新 observation 的 entity」重算(或全量重算,依實作簡潔度判斷;資料量小,全量亦可)
- [ ] 與警鈴(23:50)的順序:先投影(23:30)再警鈴(23:50)
- [ ] 排程 daemon 獨立(不與 harvest / alarm 綁死)

**效能與索引(現階段:不預先加索引)**
- Shopify 全站店家據所知未破百萬 → store_harvest_state / knowledge_state 頂多幾十萬列量級。
  這種規模,本地 PostgreSQL 就算全表掃也是毫秒級 —— **現階段不需要任何效能索引**,
  預先加反而是過度優化(索引加速讀、但拖慢寫、佔空間,且低基數欄如 status 建索引效益差)。
- **不預先加**,但保留判斷:若未來投影**實測**變慢(不是憑想像),第一順位候選是
  **observation_log 的 `(entity_id, feature, observed_at)` 複合索引** —— 那是最大的表、投影分群挑最新值時最高頻打的地方。
- knowledge_state 的 (entity_id, feature) 若已是複合主鍵,則已自帶唯一索引,無需另加(實作時確認)。
- 原則:先做出來、跑起來,慢了再用「實際慢查詢」決定加哪個索引(呼應「別憑想像優化」)。

---

## 五、時間序列查詢(回應「資料流動」——這正是 Jeff 在意的新鮮度基礎)

- [ ] 支援查詢「某 entity 的某 feature **隨時間的變化序列**」
  —— 直接讀 observation_log(Append-Only 的全歷史),依 observed_at 排序,回傳該 feature 的歷次 observed 值
- [ ] 這是「能看出店家狀態怎麼變」的技術基礎(如 review 數的成長、review app 的更換),
    也是 Phase 2.5 Growth 類 Insight 的原料
- [ ] knowledge_state 是「當前值」;時間序列是「歷史」—— 兩者都要能查(當前查 knowledge_state、歷史查 observation_log)

---

## 六、重建能力(Event Sourcing 的核心保證)

- [ ] **可砍掉 knowledge_state 全表,從 observation_log 完整重建** —— knowledge_state 是物化視圖,
    observation_log 是唯一真相。重建 = 對全部 observation 重跑投影邏輯
- [ ] 重建與日常投影共用同一套取值邏輯(重建=全量、日常=增量或全量),入口不同、邏輯一致
- [ ] 驗收:砍表重建後,knowledge_state 與砍之前一致(證明投影是純函數、observation_log 真的是唯一真相)

**★ 純函數重建(Pure-Function Reconstruction)—— 禁用隱式系統時間**

重建能力的價值前提 = **冪等性:同樣的 observation_log,不管今天重建還是明天重建,都得到一模一樣的 knowledge_state。**
若投影/重建時偷用「現在的系統時間」(`now()` / `CURRENT_TIMESTAMP`)去寫 knowledge_state 的時間欄位,就毀了這個前提:
半年前那筆 observation 的 last_observed_at,今天重建會寫成「今天」、明天重建寫成「明天」—— 同一筆歷史,時間標記浮動,
重建結果不確定,「可重建」這張底牌就不可信了(你砍掉重建、結果卻跟原來不一樣,怎麼敢砍?)。

- [ ] **投影與重建邏輯中,不使用任何 `now()` / `CURRENT_TIMESTAMP` 等隱式系統時間。**
  所有寫入 knowledge_state 的時間維度(last_observed_at、以及任何 updated_at 性質的時間標記),
  **必須 100% 由對應 observation_log 的 `observed_at` 投影而來**,確保重建的純函數性(冪等性)。
- [ ] 這條不只重建適用,**日常投影也適用** —— last_observed_at 本來就該是「那筆觀測的 observed_at」,
  而非「投影跑的時間」。日常與重建共用同一套「時間來自 observation」的規則。

---

## 七、驗收(能力導向,不卡時間)

Phase 2 的能力 = 「能把流水帳般的 Observation,收斂成**誠實反映流動現況**的、中立的、可查歷史的 Knowledge」。

- [ ] 能對一個 (entity, feature) 依「時間優先」取出當前值(決定 1 生效)
- [ ] fetch_failed 時保留舊值 + 誠實標明 last_observed_at / current_status(決定 2 生效)
- [ ] DB CHECK 物理拒絕不老實的混合狀態(last_observed_at IS NULL 卻有 value 等 → 被擋)
- [ ] country 特例生效:低 confidence 的新 inferred 不覆蓋高 confidence 的舊 certain(剛性事實不被猜測污染)
- [ ] 能查詢某 entity 某 feature 隨時間的變化序列(Append-Only 歷史讀得出來)
- [ ] 可砍掉 knowledge_state 全表並從 observation_log 完整重建,**且重建後與砍之前完全一致**(純函數性;禁用系統時間)
- [ ] 投影/Normalize 全程中立,無任何判斷/評分(守 P2)

**停止條件:** Normalize 混入判斷/評分 → 違反 P2,停;歷史查不出來 → Append-Only 沒生效,停;
fetch_failed 抹去舊值(狀態閃爍歸零)→ 違反決定 2,停;重建結果隨執行時間浮動(用了系統時間)→ 違反純函數性,停。

---

## 八、對照現有 task_plan.md 的調整建議

現有 task_plan Phase 2 的工作項(6 條)方向正確但太粗,建議依本文件補充為:
- **原有保留**:投影 Observation→Knowledge、Normalize 單位統一、取值邏輯、守 P2、時間序列查詢、重建能力。
- **需補上(原摘要沒有,但實作必撞)**:
  1. knowledge_state schema 擴充(last_observed_at / current_status)—— 由決定 2 導出。
  2. 三個定案決定明文寫入(時間優先 / 失敗保留舊值 / 定時批次)。
  3. 投影時機定義(23:30 定時批次 + 獨立 daemon)。
  4. fetch_failed 的投影處理(保留舊值 + 更新 metadata)。
  5. country 特例的理由(待 Jeff 確認)。
- **驗收條件更新**:加「決定 1 生效」「決定 2 生效」兩條能力驗收。
