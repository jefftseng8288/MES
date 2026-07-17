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
}
主鍵:(entity_id, feature) — 每個 entity 的每個 feature 只有一列「當前值」
```

> 註:value 欄於 2026-07-11 Phase 1-B 實作階段細化為 discriminated union(單一 `value_normalized` 欄 → 依 `value_type` 分流的 typed 欄)。另 2026-07-11 Phase 1-C 加入 `producer` 欄。兩者皆為欄位實作細化,非設計變更,版號不動。

**`producer` 欄(NOT NULL + CHECK,與 Observation_Log 同構)。** 投影時把來源觀測的 `producer`(mes_crawler_v1 / duckduckgo_v1 / manual_v1)一併帶上,讓「這個當前值由哪個方法/模型產生」在 Knowledge 層也追得到。定義與三欄分工見 `MES_Observation_Schema_v1.md` 第七節。

**value 欄與 Observation_Log 同構。** Knowledge_State 是 Observation_Log 的投影,值容器形狀必須一致(value_raw / value_text / value_number / value_boolean / value_json / value_entity_id 六欄),否則投影時要做型別轉換 —— 那正是腐敗點。

**value 欄 CHECK 契約(與 code 一致):** Knowledge_State **無 status 欄**,只收 `status='observed'` 的來源投影,故無 failed / not_found 分支。其 CHECK:`value_raw` 非空(非 NULL 且 `btrim(value_raw) <> ''`)+ 正好一個與 `value_type` 相符的 typed 欄非空、其餘全空(對應規則同 Observation Schema §2)。

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

---

## 六、決策記錄

| # | 決策 | 決定者 | 日期 |
|---|---|---|---|
| 1 | Default 取值規則:status → 新鮮度 → confidence(tiebreaker) | Jeff approve | 2026-06-27 |
| 2 | 取值規則可於 Feature Taxonomy 層按 feature 覆寫;schema 加 selection_rule_version | Jeff 提出,採納 | 2026-06-27 |
| 3 | v1 不存 previous_value,Growth Signal 回查 Observation_Log | Jeff approve(並列五項定義歧義為據) | 2026-06-27 |
| 4 | source_observation_id 為必填(Provenance 鎖) | Jeff 提出,採納 | 2026-06-27 |
