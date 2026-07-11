# MES — Observation Schema v1.0(Phase 0 定稿)

> 上游文件:MES_Roadmap_v5.md、MES_Entity_Model_v1.md
> 狀態:定稿(2026-06-27,Jeff 拍板三項決策)

---

## 一、鐵律(不可違反)

1. **Append-Only**:絕不 update、絕不 delete。同一 entity 的同一 feature 再次觀測 = 新增一筆帶新 timestamp 的記錄。
2. **Provenance**:`entity_id` 不可為空。每筆觀測必須知道自己屬於誰。
3. **失敗不偽裝**:抓取失敗與「確認不存在」是兩個不同的 status 值,schema 層面即區分,程式無法混淆。

---

## 二、完整欄位

```
observation {
  observation_id     -- 唯一識別(系統生成)
  entity_id          -- 歸屬,不可為空(Provenance)
  feature            -- 觀測了什麼(受控詞彙,對應 Feature Taxonomy v1)
  value_type         -- 'string' | 'number' | 'boolean' | 'entity_ref' | 'json'
  value_raw          -- crawler 看到的原貌,nullable(例 "$48.00 USD")
  value_normalized   -- 正規化可計算值,nullable(例 48.0)
  value_entity_id    -- 僅 value_type='entity_ref' 時使用,nullable
  source             -- 怎麼觀測到的(受控清單,見第五節)
  observed_at        -- 觀測時間(ISO 8601,含時區)
  confidence         -- 'certain' | 'inferred' | 'estimated'(見第四節)
  status             -- 'observed' | 'fetch_failed' | 'not_found'(見第三節)
  crawler_version    -- git commit hash(P5 第一版兌現)
}
```

五個必答問題的對應:觀測了什麼(feature)/ 值(value_*)/ 怎麼觀測到(source)/ 何時(observed_at)/ 多可信(confidence)。加上歸屬(entity_id)與狀態(status)。

---

## 三、status:三值語義(決策點 1 定案)

失敗記錄與成功記錄存**同一張表**,不另開 fetch_log。

| status | 語義 | value |
|---|---|---|
| `observed` | 抓到了,值有效 | 有效 |
| `fetch_failed` | **嘗試觀測但失敗**(429 / timeout / 頁面改版解析失敗) | 空,但這筆記錄本身是有意義的歷史:「試過、沒成功」 |
| `not_found` | **成功抓取,確認該 feature 不存在**(例:成功讀頁面,確認沒裝任何 review app) | 空;這是有效的**負向觀測**,不是失敗 |

**不分表的理由:** 失敗記錄必須與成功記錄在同一條時間軸上被查詢。Phase 2 算 Growth Signal 時,系統必須能分辨「上週沒數據是沒抓到(fetch_failed)還是真的沒有(not_found)」— 分表則此查詢需跨表拼接且易漏。

此設計直接落實跨系統鐵則:**失敗訊號不可偽裝成沒事。**

---

## 四、confidence:離散三級(決策點 2 定案)

第一版**不用 0~1 連續數字**(假精度:crawler 沒有依據說這筆是 0.85 而非 0.8,編出來的精度比粗糙的誠實更危險)。

| 等級 | 判定規則 | 例 |
|---|---|---|
| `certain` | 直接讀取的事實 | products.json 的價格;HTML 特徵碼比對命中 |
| `inferred` | 從間接證據推得 | 從主題檔案結構推斷 theme_name |
| `estimated` | 粗略估計 | 從前端顯示估評論總數 |

**升級路徑:** 未來有校準依據(某類 inferred 的實際錯誤率被驗證)時,可升級為連續數字 — 離散升連續不破壞歷史(certain→1.0 等映射);反向才會丟資訊。

---

## 五、source:受控清單 v1(決策點 3 定案)

新增值必須改文件版本,**不可自由填字串**(自由字串會腐敗:`html`、`HTML page`、`webpage` 三種寫法三個月後無法聚合)。

| source | 說明 |
|---|---|
| `html_page` | 抓商店頁面 HTML |
| `products_json` | /products.json 端點 |
| `html_signature` | 前端特徵碼比對(判定 review app / theme) |
| `manual` | Jeff 人工輸入 |
| `monitor` | 從 EF_WorkFlow Monitor 轉入的 Market Signal |

對應 Phase 1 實際會有的全部來源。Phase 2.5 加 Insight 來源、Phase 3 加 LLM 來源時升版。

---

## 六、決策記錄

| # | 決策 | 決定者 | 日期 |
|---|---|---|---|
| 1 | 失敗記同表,status 三值(observed / fetch_failed / not_found) | Jeff approve | 2026-06-27 |
| 2 | confidence 第一版用離散三級,不用連續數字 | Jeff approve | 2026-06-27 |
| 3 | source 受控清單 v1(五值),新增須升版 | Jeff approve | 2026-06-27 |
