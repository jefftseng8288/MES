# MES — Knowledge Schema v1.0(Phase 0 定稿)

> 上游文件:MES_Roadmap_v5.md、MES_Entity_Model_v1.md、MES_Observation_Schema_v1.md
> 狀態:定稿(2026-06-27,Jeff 拍板兩項決策 + schema 補強)

---

## 一、定位(不可動搖)

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
  value_raw
  value_normalized
  value_entity_id          -- nullable
  source_observation_id    -- ★ 本表靈魂:此當前值來自哪筆 Observation(Provenance 鎖)
  observed_at              -- 該筆來源觀測的時間
  confidence               -- 該筆來源觀測的信心等級
  selection_rule_version   -- 此值是用哪版取值規則選出的(P5)
  updated_at               -- 本列最後被重算的時間
}
主鍵:(entity_id, feature) — 每個 entity 的每個 feature 只有一列「當前值」
```

**`source_observation_id` 不可為空。** Knowledge_State 是衍生品,每個值必須能追回它的來源觀測 — 沒有它,這張表就是「不知道自己從哪來」的斷鏈表,違反 Provenance。

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
| country | 可覆寫為 confidence 優先 | 幾乎不變;舊的 certain(結構化讀取)比新的 inferred(語言猜測)可信 |
| currency | 可覆寫為 latest + confidence 並重 | 類似 country |

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
