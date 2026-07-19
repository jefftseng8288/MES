# MES — Knowledge Schema v1.0(Phase 0 定稿)

> 上游文件:MES_Roadmap_v5.md、MES_Entity_Model_v1.md、MES_Observation_Schema_v1.md
> 狀態:定稿(2026-06-27,Jeff 拍板兩項決策 + schema 補強)

---

## 一、定位(不可動搖)

> （此為資料層硬約束,不因「系統原則可演化」的口徑而鬆動。系統原則可因證據演進;資料腐敗防線不可。）

**Knowledge_State 是 Observation_Log 的物化視圖(materialized view),是衍生品,不是真相。**

- 真相只存在於 Observation_Log(Append-Only,唯一 source of truth)。
- Knowledge_State 永遠可以被砍掉,並從 Observation_Log 完整重建。
- 任何新觀測必先 append 進 Observation_Log,再更新 Knowledge_State。**絕無「直接改 Knowledge_State」的後門。**
- Knowledge_State 仍屬事實層:只存正規化的當前值,不做任何判斷/評分(P2)。

---

## 二、Schema

```
knowledge_state {
  entity_id                -- 歸屬
  feature                  -- 哪個 feature 的當前值
  value_type               -- 同 Observation('string'|'number'|'boolean'|'entity_ref'|'json')
  value_raw                -- 該當前值的原始值原貌,nullable(語義同 Observation)
  value_text               -- string 型正規值,nullable
  value_number             -- number 型正規值,nullable
  value_boolean            -- boolean 型正規值,nullable
  value_json               -- json 型正規值,nullable
  value_entity_id          -- entity_ref 型正規值,nullable(僅 value_type='entity_ref')
  producer                 -- 產生此值的方法/模型,NOT NULL + CHECK(自來源觀測帶上)
  source_observation_id    -- ★ 本表靈魂:此當前值來自哪筆 Observation(Provenance 鎖)
  observed_at              -- 該筆來源觀測的時間
  confidence               -- 該筆來源觀測的信心等級
  selection_rule_version   -- 此值是用哪版取值規則選出的(P5)
  updated_at               -- 本列最後被重算的時間
  last_observed_at         -- 最後一次「成功」觀測的時間(= 被取為當前值那筆 observed 的 observed_at);
                           --   nullable,NULL = 從無成功觀測(此時 value 依 CHECK 必全 NULL)
  current_status           -- 最近一次「嘗試」觀測的結果,NOT NULL(observed/fetch_failed/not_found)
}
主鍵:(entity_id, feature) — 每個 entity 的每個 feature 只有一列「當前值」
```

> 註:value 欄於 2026-07-11 Phase 1-B 細化為 discriminated union;2026-07-11 Phase 1-C 加入 `producer` 欄;**2026-07-19 Phase 2(第一批)加入 `last_observed_at` + `current_status` 欄,並把 value CHECK 改為受 last_observed_at 條件化**(見下,由決定 2 導出)。皆為欄位實作細化,非設計變更,版號不動。

**`producer` 欄(NOT NULL + CHECK,與 Observation_Log 同構)。** 投影時把來源觀測的 `producer`(mes_crawler_v1 / duckduckgo_v1 / mes_store_crawler_v1 / manual_v1)一併帶上。定義與三欄分工見 `MES_Observation_Schema_v1.md` 第七節。

**value 欄與 Observation_Log 同構。** Knowledge_State 是 Observation_Log 的投影,值容器形狀必須一致(value_raw / value_text / value_number / value_boolean / value_json / value_entity_id 六欄),否則投影時要做型別轉換 —— 那正是腐敗點。

### `last_observed_at` / `current_status` 與 fetch_failed 處理(決定 2,Phase 2)

**決定 2 —— fetch_failed 保留舊值,但誠實標明新鮮度與當前狀態。** `fetch_failed` 是「系統失能」(被擋/斷網),不代表商家變了;若一次失敗就抹去舊值,狀態會隨網路波動閃爍歸零。故 knowledge_state 要能同時回答三件事:

- **當前值是什麼** —— `value`(取自最新 observed,依決定 1)。
- **這值最後一次成功觀測何時** —— `last_observed_at`(= 被取為當前值那筆 observed 的 observed_at)。
- **最近一次嘗試觀測的結果** —— `current_status`(該 (entity, feature) 最近一筆 observation 的 status,不論成敗)。

**關鍵:`value/last_observed_at` 與 `current_status` 是兩個不同時間點。** 例:product_count=196 於半年前 observed、今天去戳 fetch_failed → `value=196`、`last_observed_at=半年前`、`current_status=fetch_failed`。未來篩店可精準挑「last_observed_at 在 N 天內 **且** current_status=observed」的,自動過濾可能過期的。

> 實作註記:`last_observed_at` 語義與既有 `observed_at`(「該筆來源觀測的時間」)**重疊** —— 兩者在當前設計下都等於「被取為當前值那筆 observed 的時間」。第一批依 Phase 2 設計文件先加 `last_observed_at`;是否與 `observed_at` 合併,待第二批(投影引擎)一併釐清。

**value 欄 CHECK 契約(2026-07-19 改為受 last_observed_at 條件化;與 code 一致):** 原本 knowledge_state 只收 observed 投影、value 無條件必存在;決定 2 引入「保留舊值 + current_status」後,value 是否存在改由 `last_observed_at` 決定。DB 層 CHECK 物理鎖死合法組合(同 Observation_Log discriminated union 哲學,不信任投影代碼):

- **規則 1(從無成功觀測):** `last_observed_at IS NULL` → `value` 必全 NULL(所有 typed 欄 + value_raw 皆 NULL)、且 `current_status` 只能 `fetch_failed` / `not_found`(不能 observed)。防「從沒成功卻有值」的鬼值。
- **規則 2(曾成功觀測):** `last_observed_at IS NOT NULL` → `value` 必非 NULL(value_raw 非空 + 正好一個與 value_type 相符的 typed 欄非空、其餘全空);`current_status` 任意(observed=現在也成功 / fetch_failed=以前成功這次失敗 / not_found=現在確認沒有了)。防「曾成功卻沒值」的空洞。
- `current_status` 受控三值 CHECK(沿用 observed/fetch_failed/not_found)。

**`source_observation_id` 不可為空(不變)。** Knowledge_State 是衍生品,每個值必須能追回它的來源觀測 — 沒有它,這張表就是「不知道自己從哪來」的斷鏈表,違反 Provenance。

---

## 三、取值規則(決策點 1 定案)

### Default Selection Rule v1

同一 (entity, feature) 有多筆觀測時,「當前值」依序決定:

```
1. 先排除無效狀態:只取 status = 'observed'
2. 再看新鮮度:取 observed_at 最新
3. 最後看信心度:同一時間窗內並列時,取 confidence 較高者(certain > inferred > estimated)
```

原則:**新鮮度優先於信心度** — Knowledge_State 回答的是「現在狀態」,不是「歷史上最可靠狀態」。三天前的 certain 對「現在」的描述力,通常不如今天的 inferred。

### Feature 層覆寫(Jeff 補充,採納)

Default rule 不可能對所有 feature 都對:

| feature | 適用規則 | 理由 |
|---|---|---|
| review_app / review_count / theme_name | default(最新優先) | 會變的狀態,時效優先 |
| country | 時間優先,但**低 confidence 的新值不覆蓋高 confidence 的舊值** | 剛性物理屬性(稅務/金流,幾乎不變);舊的 certain(直讀 `Shopify.country`)勝過新的 inferred(如 IP 猜伺服器國)——伺服器託管國 ≠ 註冊國 |
| currency | default(最新優先) | 第一版**不套** country 特例(country 特例第一版只套 country) |

> 註:country 取值規則於 2026-07-17 Phase 2 拆解定案時**精確化**——原述「confidence 優先」改為「時間優先,但低 confidence 新值不覆蓋高 confidence 舊值」(即決定 1「時間優先」+ 剛性事實保護:新觀測 confidence ≥ 舊當前值 → 照時間優先;新 < 舊 → 不覆蓋、保留舊高 confidence 值)。理由:不是「country 不看時間」,而是「剛性事實不被低信心猜測污染」。**第一版只套 country**,是否推廣待未來證據決定。詳見 `MES_Phase2_Knowledge_Engine.md` 第三節。屬取值規則細化,版號不動。

**機制:** 覆寫規則定義於 Feature Taxonomy 層(該 feature 的定義中註明 selection rule);Knowledge_State 每列記錄 `selection_rule_version`,規則改版後可精確知道哪些值由舊規則選出、需否重算。

---

## 四、不存 previous value(決策點 2 定案)

**Knowledge_State v1 不存 previous_value。Growth Signal 一律回查 Observation_Log 產生。**

理由(Jeff 列出的定義歧義,每一個都無顯然答案):
- previous 是「上一筆 observed」還是「上一個不同 value」?
- fetch_failed 算不算?
- 同一天多來源怎麼算?
- Normalize rule 改版後 previous 要不要重建?

**一個連定義都需五回合討論的欄位,不進 v1。** 回查 Observation_Log 無此歧義(查詢方自定語義),且第一版資料量小,掃 log 完全可接受。

**升級路徑:** 等查詢瓶頸真實出現,再以物化欄位或 derived table 加速 — 由真實需求驅動,不做預先優化(三問規則第三問:拿掉它,系統照樣運作)。

---

## 五、重算義務(Phase 2 Knowledge Engine 的職責)

- Knowledge Engine 的工作 = 把 Observation_Log 投影成 Knowledge_State 的那個計算過程。
- 更新模式:非同步批次重算(crawler 快速 append,Engine 定期投影)— 符合每日 crawler 的節奏,非即時系統。
- **重建能力是驗收條件:** 給定 Observation_Log,必須能砍掉 Knowledge_State 全表並完整重建。此能力 = 「可回滾」在本層的具體形式。

### Phase 2 第二批投影引擎實作落地(2026-07-19,`src/mes/knowledge.py`)

- **全量重算(Jeff 定案):** 每次投影都清空 knowledge_state、把整個 observation_log 重跑。資料規模小(<百萬),全量快、天然冪等、最不易錯;日常投影 = 重建,順便驗證重建能力。不做增量。
- **value(只看 observed)vs current_status(看全部):兩者掃不同子集** —— `value` 只從該 (entity, feature) 的 **observed** 子集依取值規則挑;`current_status` 從**所有觀測(含 fetch_failed/not_found)**取 observed_at 最新那筆的 status。故決定 2 場景成立:半年前 observed=196、今天 fetch_failed → `value=196`、`observed_at=半年前`、`current_status=fetch_failed`。
- **純函數重建(鐵律):** 投影不使用任何 `now()` / `CURRENT_TIMESTAMP`;所有寫入的時間維度(`observed_at`、`updated_at`)100% 由 observation_log 的 `observed_at` 投影而來 → 同樣的 observation_log 今天投影與明天投影**完全一致**(冪等)。取值 tiebreaker 一路用 `observation_id` 收尾以確保決定性。
- **`last_observed_at` 併回 `observed_at`(第一批冗餘欄合併):** 第一批加的 `last_observed_at`(= 被取為當前值那筆 observed 的時間)經投影驗證與既有 `observed_at` **恆等**(2905 列 0 不符),故第二批移除 `last_observed_at`,value 閘門 CHECK 改綁 `observed_at`(migration `e8d6f05d71b0`)。
- **「從無 observed」不投影列(Jeff 定案 2026-07-19):** 只有 fetch_failed/not_found、從無成功觀測的 (entity, feature),**不投影** knowledge_state 列(而非投影一列無值列)—— knowledge_state 裡就是**查無此列**。因為無值列會逼 `observed_at` / `source_observation_id` 等 Provenance NOT NULL 欄放寬,動到鐵律;**保鐵律優先**,規則 1 的 CHECK 續當防禦守門。要知道「它為什麼沒值 / 試過幾次」→ 去查 observation_log(Append-Only 誠實記著所有失敗嘗試)。
- **時間序列查詢** `feature_history(entity, feature)`:讀 observation_log(Append-Only 全歷史)的 observed 筆,依 observed_at 排序。knowledge_state = 當前值(查它);時間序列 = 歷史(查 observation_log)。
- **投影排程:** 獨立 launchd `com.mes.projection`,每天 23:30 台灣全量投影(投影 23:30 → 警鈴 23:50,投影在前),獨立於 harvest / alarm daemon。

---

## 六、決策記錄

| # | 決策 | 決定者 | 日期 |
|---|---|---|---|
| 1 | Default 取值規則:status → 新鮮度 → confidence(tiebreaker) | Jeff approve | 2026-06-27 |
| 2 | 取值規則可於 Feature Taxonomy 層按 feature 覆寫;schema 加 selection_rule_version | Jeff 提出,採納 | 2026-06-27 |
| 3 | v1 不存 previous_value,Growth Signal 回查 Observation_Log | Jeff approve(並列五項定義歧義為據) | 2026-06-27 |
| 4 | source_observation_id 為必填(Provenance 鎖) | Jeff 提出,採納 | 2026-06-27 |
