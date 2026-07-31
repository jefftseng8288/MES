# MES — Feature Taxonomy v2(v1 Phase 0 定稿 + 2026-07-31 評論類 feature)

> 上游文件:MES_Roadmap_v5.md、MES_Entity_Model_v1.md、MES_Observation_Schema_v1.md、MES_Knowledge_Schema_v1.md
> 狀態:v2(2026-07-31)—— v1 定稿 2026-06-27;v2 新增評論類三個 feature(§一之二)。
> 定位:**本文件不追求完整,而是定義 Phase 1 crawler 的邊界** — 夠小、夠可測、夠低風險。
> Feature 採彈性結構,新增不改 schema,只升本文件版本。

---

## 〇、分類軸:Market Feature vs Meta-Feature

> 註:2026-07-11 Phase 1-C 細化:新增元特徵分類軸與 `store_name_seed`。屬設計細化,版號不動。

feature 依「描述的對象」分兩類:

- **市場特徵(Market Feature):** 描述 **Reality 實體**(Store 等)的狀態。即 §一 的 9 個:
  `uses_review_app` / `theme_name` / `product_count` / `avg_price` / `price_range` /
  `country` / `language` / `currency` / `is_active`。
- **元特徵(Meta-Feature / Process Feature):** 描述 **MES 系統自身的認知與對接事件**(抓取、推論),
  不是 Reality 實體的狀態:
  - `observed_on_app_store` — 在 App Store 評論頁看到一筆 Store Name(掛在 `store_name_seed` 上)。
  - `inferred_domain` — 從 Store Name 推論 domain 的結果(成功 → entity_ref 指向 store;失敗 →
    依 `fetch_failed` / `not_found` 分流,全欄 NULL)。

**新增 entity_type:** `store_name_seed` —— 尚未推出 domain 前的「Store Name 種子」實體,元特徵掛載點。

兩類同用一套 Observation Schema(同一組 value 欄與 CHECK),差別只在「feature 描述的是市場,還是系統自己的過程」。
元特徵讓「MES 的抓取/推論能力本身」成為可被觀測、可被評估的對象(例:`inferred_domain` 的 not_found 佔比)。

---

## 一、v1 範圍:9 個 feature(Market Feature)

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
- 此即 Knowledge Schema country 取值特例的原因:**時間優先,但低 confidence 的新值不覆蓋高 confidence 的舊值**(剛性事實不被猜測污染;2026-07-17 Phase 2 拆解精確化,原述「confidence 優先」)。未來可演進為多來源信心合併

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

### Social Proof(v2 新增,2026-07-31)

> **目標定調(Jeff):不管店家用什麼 review app,都要能抓到 `review_count` 與 `avg_rating`。**
> 「五個 app」是目前的工具限制,不是目標的邊界。架構為**通用入口 + per-app handler 可插拔**
> (`src/mes/reviews.py`),未來每家 app 逐個加進來,核心不動。

**`review_count`**
- value_type:`number`(**全店評論總數**,不是單一商品的評論數)
- source:`review_widget`(v2 新增的受控值 —— 評論數來自 review app 自己的 widget 頁面,
  **不是店家的 html_page**;沿用 `web_search` 先例:寧可補誠實新值,不借語義不符的舊值)
- confidence:review app 自報數字為直讀 → `certain`
- **三值語義(不可混淆):**
  - 沒偵測到 review app → `not_found`(**確認不具備**,有效負向觀測)
  - 偵測到了但 MES 尚無該 app 的 handler(如 yotpo)→ `fetch_failed`
    (店家其實有評論,只是**我們讀不到** = 能力邊界 → 我們**不知道**結果。
    記 not_found 會謊稱「確認沒有評論」)
  - 有 handler 但抓取/解析失敗 → `fetch_failed`

**`avg_rating`**
- value_type:`number`(1–5)
- source / 三值語義同 `review_count`
- confidence:**頁面直接顯示平均分 → `certain`;由星等分佈計算而得 → `estimated`**
  (誠實標記「這是算出來的」)

**`rating_distribution`**
- value_type:`json`(如 `{"5":365,"4":19,"3":7,"2":2,"1":9}`)
- **為什麼連分佈一起記:** 分佈的資訊量**嚴格大於**平均值 —— 能從分佈算平均,反過來不行;
  對未來應用(如「有 50 則 1 星」)比「平均 4.5」有用得多;同一次抓取就拿得到,不記可惜。
- **★ 自我驗算(已實作):分佈加總必須等於 `review_count`**,不符則**不採用分佈**、僅記總數,
  並記 `not_found`(可見)而非靜默省略。這讓解析錯誤當場現形。

> **實測教訓(2026-07-31,寫給未來加 handler 的人):**
> ① **通用抓法不可行** —— schema.org `aggregateRating` 在 11 家真實店只有 1 家有,
>    且那筆是**單一商品**的評論數(非全店);其餘店的評分由 **JS 執行期注入**,靜態 HTML 只有空容器。
> ② **widget id 有多種 markup** —— 三家店就遇到三種(script URL / `__LXC` 註解 /
>    JSON 跳脫斜線),幾乎確定還有第四種。取不到要記 `fetch_failed` 保留原因,不可靜默跳過。
> ③ **widget 頁的可見文字會在地化**(西班牙文店顯示 `1,019 Reseñas` 而非 `Reviews`)——
>    解析要錨在**結構**(`data-testid`)而非文字,否則會漏掉所有非英文店。
>    但 `aria-label` 實測**不隨語言變**,可跨語言使用。
> ④ **不要用位置式解析**(「總數後面接的 5 個數字」)—— 小店的數字序列會誤導;
>    要錨在自我標示星等的結構(`aria-label="N stars, M reviews"`)。
> ⑤ **限流性質不同於 harvest** —— handler 打的是 review app 自己的伺服器(所有 loox 店都打
>    同一個 `loox.io`),接近 baseline 打同一個 DDG,故**獨立節流**、比 per-domain 保守。

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
| country | 時間優先,但低 confidence 新值不覆蓋高 confidence 舊值(剛性事實保護;2026-07-17 精確化,原述「confidence 優先」)|
| 其餘(含 currency、v2 的評論類三個) | default v1(status → 新鮮度 → confidence tiebreaker) |

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
| 7 | v2 加 review_count / avg_rating / rating_distribution;目標是「所有 app 都要抓到」,非只限五個 | Jeff 定調 | 2026-07-31 |
| 8 | 連星等分佈一起記(資訊量嚴格大於平均,且可自我驗算) | Jeff 定案 | 2026-07-31 |
