# MES — Feature Taxonomy v1(Phase 0 定稿)

> 上游文件:MES_Roadmap_v5.md、MES_Entity_Model_v1.md、MES_Observation_Schema_v1.md、MES_Knowledge_Schema_v1.md
> 狀態:定稿(2026-06-27)
> 定位:**本文件不追求完整,而是定義 Phase 1 crawler 的邊界** — 夠小、夠可測、夠低風險。
> Feature 採彈性結構,新增不改 schema,只升本文件版本。

---

## 一、v1 範圍:9 個 feature

Phase 1 crawler 的全部工作範圍。七大類只覆蓋三類半;Performance / Growth / Pain / Market 全部留白
(Growth 等時間序列自然長出、Pain/Market 屬後面的認知層、Performance 屬深掃階段)。

### Technology Stack

**`uses_review_app`**
- value_type:`entity_ref`(→ ReviewApp entity)
- source:`html_signature`(前端特徵碼比對:script src、DOM class)
- confidence:命中 → `certain`
- **not_found 語義(保守,關鍵):** 成功讀頁但無命中 → status `not_found` + confidence `inferred`。
  沒命中 ≠ 沒裝,只代表「在目前 signature library vX 裡沒識別到」。
- **對照組標記(未來用):** `not_found` 群未來搭配店齡/catalog velocity,可切分出
  「新店空白市場」子集(知道價值、正在挑、無遷移負擔 — 轉換成本最低的客群)。
  非 Phase 1 工作,留此標記免得遺忘。

**`theme_name`**
- value_type:`string`
- source:`html_page`(`Shopify.theme` JS 物件)
- confidence:直接讀到 → `certain`;間接推斷 → `inferred`
- ⚠️ 待 Phase 1 實測:`Shopify.theme` 存在與格式因店而異,命中率低則降級抓法並升版本文件

### Business Signals

**`product_count`**
- value_type:`number`
- source:`products_json`(分頁計數)
- confidence:`certain`
- ⚠️ 待實測:部分店關閉端點或有分頁上限;抓不到記 `fetch_failed`,絕不猜

**`avg_price`**
- value_type:`number`
- source:`products_json`(variants price 聚合)
- confidence:`certain`;若分頁不完整 → `estimated` 並於 value_raw 記樣本數

**`price_range`**
- value_type:`json`(`{min, max}`)
- 同 avg_price
- 注意:保留原始幣別;跨店換算屬 Phase 2 Normalize,不在觀測層做

### Company

**`country`**
- value_type:`string`(ISO 3166-1 alpha-2)
- source:`html_page` / `products_json` 間接訊號(幣別、語言、地址)
- confidence:多為 `inferred` — **接受粗糙,不讓它卡住 Phase 1**
- 此即 Knowledge Schema「country 覆寫為 confidence 優先」規則的原因;未來可演進為多來源信心合併

**`language`**
- value_type:`string`(ISO 639-1)
- source:`html_page`(`<html lang>`)
- confidence:有 lang 屬性 → `certain`;內容判斷 → `inferred`

**`currency`**
- value_type:`string`(ISO 4217)
- source:`products_json` / Shopify currency 設定
- confidence:`certain`

### Store Status

**`is_active`**
- value_type:`boolean`
- source:`html_page`(正常回應且有商品結構)
- confidence:`certain`
- **語義區分:** `is_active = false`(確認空店/關店頁)≠ status `fetch_failed`(連不上 — 可能只是網路問題)。
  沿用 Observation Schema status 三值,不在 value 層混淆。

---

## 二、ReviewApp Signature Library v1

第一版認得五個(市佔最高、對 EscapeFlow 第一階段最有觀測意義):

```
loox / judgeme / yotpo / okendo / stamped
```

- canonical_key 依 Entity Model 的 normalize 規範(小寫、去空白標點、別名表收斂)
- **庫本身有版本**:not_found 的推斷品質直接取決於庫的覆蓋率;每加一個 app 須人工確認前端特徵碼並升版
- 不追求全覆蓋 — 五個是「夠用且做得完」的起點

---

## 三、取值規則覆寫(承 Knowledge Schema)

| feature | selection rule |
|---|---|
| country | confidence 優先(覆寫 default) |
| 其餘 8 個 | default v1(status → 新鮮度 → confidence tiebreaker) |

---

## 四、決策記錄

| # | 決策 | 決定者 | 日期 |
|---|---|---|---|
| 1 | v1 = 9 個 feature,定義 Phase 1 crawler 邊界,不追求完整 | 共同定案 | 2026-06-27 |
| 2 | uses_review_app 的 not_found = not_found + inferred(保守語義) | 共同定案 | 2026-06-27 |
| 3 | Signature Library v1 = 五個 app | Jeff 定案 | 2026-06-27 |
| 4 | Phase 1 不主打「未裝評論系統」客群;not_found 保留為對照組,標記「新店空白市場」子集供未來切分 | Jeff 定案 + 補充標記 | 2026-06-27 |
| 5 | country 接受粗糙(inferred 為主),不卡 Phase 1 | Jeff 定案 | 2026-06-27 |
| 6 | Performance 暫緩至「Knowledge 能篩出值得深掃的店」之後 | Jeff 定案 | 2026-06-27 |
