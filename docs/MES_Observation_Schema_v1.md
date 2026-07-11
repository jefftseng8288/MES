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
  value_raw          -- 該次觀測取得的「原始值」原貌,nullable(例 "$48.00 USD")
  value_text         -- string 型正規值,nullable
  value_number       -- number 型正規值,nullable
  value_boolean      -- boolean 型正規值,nullable
  value_json         -- json 型正規值,nullable
  value_entity_id    -- entity_ref 型正規值,nullable(僅 value_type='entity_ref')
  source             -- 透過什麼管道觀測到(受控清單,見第五節)
  producer           -- 由哪個方法/模型產生,NOT NULL(受控清單,見第七節)
  observed_at        -- 觀測時間(ISO 8601,含時區)
  confidence         -- 'certain' | 'inferred' | 'estimated'(見第四節)
  status             -- 'observed' | 'fetch_failed' | 'not_found'(見第三節)
  crawler_version    -- 執行當時的 Git commit SHA-1(只存 hash,見第七節),nullable
}
```

> 註:value 欄於 2026-07-11 Phase 1-B 實作階段細化為 discriminated union(單一 `value_normalized` 欄 → 依 `value_type` 分流的 typed 欄 value_text / value_number / value_boolean / value_json / value_entity_id)。另 2026-07-11 Phase 1-C 加入 `producer` 欄。皆為欄位實作細化,非設計變更,版號不動。

五個必答問題的對應:觀測了什麼(feature)/ 值(value_*)/ 怎麼觀測到(source)/ 何時(observed_at)/ 多可信(confidence)。加上歸屬(entity_id)與狀態(status)。

### value 欄 CHECK 契約(與 code 一致)

value 欄以兩層 CHECK 把「值容器」鎖成 discriminated union,DB 層強制:

- **第一層(status ↔ value_raw):** `observed` → `value_raw` 非空(且非空白字串,`btrim(value_raw) <> ''`);`fetch_failed` / `not_found` → `value_raw` 為 NULL。
- **第二層(status ↔ value_type ↔ typed 欄):** `observed` → 正好一個與 `value_type` 相符的 typed 欄非空,其餘 typed 欄全空(string→value_text、number→value_number、boolean→value_boolean、json→value_json、entity_ref→value_entity_id);`fetch_failed` / `not_found` → 所有 typed 欄全空,但 **`value_type` 保留**(它描述該 feature 的**預期型別**,不是本次取到值的型別。例:feature=avg_price, value_type='number', status='fetch_failed', 所有 value 欄=NULL 為完整合法記錄)。

**value_raw 語義邊界:** `value_raw` 只存該 feature 的原始值原貌(例 country 的 "Taiwan"、avg_price 的 "$48.00 USD"),**絕不可**塞入抓取/推論的執行證據(HTTP status code、原始 URL、parser error、命中的 HTML selector、signature evidence、搜尋候選清單)。那些是「怎麼取到的」的證據,不是 feature value,未來另立 evidence / 執行紀錄欄位承擔;混入會讓 value_raw 語義腐敗。

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

## 五、source:受控清單(v1 → 清單 v2)

> 註:2026-07-11 Phase 1-C 細化:source 加入 `web_search`(清單版本 v1 → v2);另新增 `producer` 欄(見第七節)。屬設計細化,主文件版號不動。

新增值必須改**清單版本**,**不可自由填字串**(自由字串會腐敗:`html`、`HTML page`、`webpage` 三種寫法三個月後無法聚合)。

| source | 說明 |
|---|---|
| `html_page` | 抓商店頁面 HTML |
| `products_json` | /products.json 端點 |
| `html_signature` | 前端特徵碼比對(判定 review app / theme) |
| `web_search` | 透過網頁搜尋引擎推論(如 inferred_domain 走 DuckDuckGo)—— 清單 v2 新增 |
| `manual` | Jeff 人工輸入 |
| `monitor` | 從外部 Monitor 轉入的 Market Signal |

對應 Phase 1 實際會有的全部來源。Phase 2.5 加 Insight 來源、Phase 3 加 LLM 來源時升版。

**為什麼 web_search 必須是獨立 source(不可用 html_page 頂替):** `source` 是 Provenance 的「管道」欄。inferred_domain 的 domain 是透過網頁搜尋**推論**來的,把它記成 `html_page` 等於在來源追溯上說謊(靜默 fallback),未來無法區分「直接抓頁面」與「搜尋推論」兩種完全不同可信度的管道。

---

## 七、producer:值的生產者(受控清單,Phase 1-C 新增)

> 每筆 observation 都有生產者。`producer` **NOT NULL**(ORM nullable=False + DB NOT NULL 雙層)+ VARCHAR + CHECK 物理鎖死。

`producer` = 這筆值被固化時,由哪個**方法/模型**(責任主體、語義版本)做出的裁決。與 P6 的 **provider**(外部資料源概念)**刻意區分**,勿混用:producer 答「誰產生了這筆值」。

| producer | 說明 |
|---|---|
| `mes_crawler_v1` | 直接抓取/讀取的責任主體(observed_on_app_store + 未來 9 個市場特徵) |
| `duckduckgo_v1` | DuckDuckGo 推論 inferred_domain 的責任主體 |
| `manual_v1` | 人工校正/手動餵入的責任主體 |

### 三欄分工(Provenance 完整且不重疊)

| 欄 | 答的問題 | 值域 |
|---|---|---|
| `source` | 透過什麼**管道**觀測到 | html_page / products_json / html_signature / web_search / manual / monitor |
| `producer` | 這筆值由哪個**方法/模型**產生 | mes_crawler_v1 / duckduckgo_v1 / manual_v1 |
| `crawler_version` | 執行當時的**實體程式碼版本** | Git commit SHA-1(只存 hash,不塞別的) |

合起來:「這筆 domain,透過 `web_search` 管道、由 `duckduckgo_v1` 產生、在 git commit `abc1234` 時被固化。」

**crawler_version 語義釐清:** 只存 Git commit SHA-1(執行時的實體程式碼版本)。Phase 1-C 曾暫把 `duckduckgo_v1` 塞在此欄,已歸位到 `producer`;crawler_version 不再承載非 hash 值(尚未接 git 時為 NULL,寧可 NULL 也不塞錯值)。

---

## 八、決策記錄

| # | 決策 | 決定者 | 日期 |
|---|---|---|---|
| 1 | 失敗記同表,status 三值(observed / fetch_failed / not_found) | Jeff approve | 2026-06-27 |
| 2 | confidence 第一版用離散三級,不用連續數字 | Jeff approve | 2026-06-27 |
| 3 | source 受控清單 v1(五值),新增須升版 | Jeff approve | 2026-06-27 |
| 4 | source 清單 v2 加 `web_search`;新增 `producer` 欄(NOT NULL + CHECK);crawler_version 歸位為純 git hash | 細化(版號不動) | 2026-07-11 |
