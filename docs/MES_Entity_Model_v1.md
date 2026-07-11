# MES — Entity Model v1.0(Phase 0 定稿)

> 上游文件:MES_Roadmap_v5.md
> 狀態:定稿(2026-06-27,Jeff 拍板三項決策 + 兩項補充)

---

## 一、Entity 準入判準(唯一一條)

> **未來會不會「以它為中心聚合觀測」?會 → Entity;不會 → 它是某個 Entity 的 Feature。**

Entity 不是「重要名詞」,是「未來要被獨立觀測與聚合的對象」。
此判準用於未來每一次「要不要新增 Entity」的判斷,防止 Entity 膨脹。

---

## 二、第一版 Entity 清單

| Entity | 狀態 | 說明 |
|---|---|---|
| **Store** | ✅ 實作 | 一切觀測的主要掛載點 |
| **ReviewApp** | ✅ 實作 | 支撐跨店聚合(例:「所有用 Loox 的店的平均 widget 延遲」) |
| Theme | Feature | `theme_name` 作為 Store 的觀測值;未來需分析主題用戶群時可升級(升級不破壞資料) |
| Product | Feature | `product_count / avg_price / price_range` 等 Store 層聚合值已足夠 |
| Company | 跳過 | 三問規則第一問過不了(現在零商業價值) |
| Contact | 📋 預留 entity_type,不實作 | Phase 4 接觸時才需要 |
| Experiment / Campaign | 📋 預留 entity_type,不實作 | Phase 4 |

第一版原則:**越小越好。** 拿得掉的就晚做(三問規則第三問)。

---

## 三、Entity Schema

```
entity {
  entity_id        -- 唯一識別(系統生成)
  entity_type      -- 'store' | 'review_app'(預留:'contact' | 'experiment' | 'campaign')
  canonical_key    -- 自然鍵,去重唯一依據(規範見第四節)
  created_at       -- 首次被系統認識的時間
}
```

**設計原則:Entity 幾乎不帶屬性。** 店名、國家、評論數等一切會變的事實,全部是 Observation,不是 Entity 欄位。Entity 只是「觀測掛載點 + 去重自然鍵」。(守 P2:Entity 表不儲存會過時的事實。)

---

## 四、canonical_key 的 Normalize 規範(v1)

去重的唯一依據。規則必須在寫入前套用,不靠實作默契。

### store(以 domain 為 canonical_key)
1. 全部轉小寫
2. 去除 scheme(`https://`、`http://`)
3. 去除 trailing slash 與 path(只留 host)
4. 去除 `www.` 前綴
5. 去除 port

範例:`https://WWW.WillowBloom.com/products/` → `willowbloom.com`

**已知限制(記錄在案):** 極少數站點 www 與裸域名指向不同內容;對 Shopify 商店場景,同店雙域名幾乎皆為重導向,故採「一律去 www」為預設。此規則有版本(v1),未來撞到例外可迭代(P5)。

### review_app(以正規化名稱為 canonical_key)
1. 全部轉小寫
2. 去除空白與標點(`Judge.me` → `judgeme`)
3. 維護一份別名對照表(alias map),同一 app 的不同寫法收斂到同一 canonical_key
   (例:`loox`、`loox reviews`、`loox photo reviews` → `loox`)
   別名表本身有版本。

---

## 五、關係即觀測(本文件最重要的決策)

**「Store X 使用 ReviewApp Loox」不是永久關係,是一個在某時間點被觀測到的事實。**

因此關係不存固定外鍵(`store.review_app_id`),而是存成一筆 Observation:

```
observation {
  entity_id:        (Store X)
  feature:          "uses_review_app"
  value_type:       "entity_ref"
  value_entity_id:  (ReviewApp Loox 的 entity_id)
  source:           "html_signature"
  timestamp / confidence: ...
}
```

**理由:**
1. 關係會變。店家換 review app 時,固定外鍵只能 update(違反 Append-Only,歷史消失);存成觀測,換 app = 新增一筆,舊關係自動成為歷史 → **「App Changed」這個 Growth Signal 因此天然可得。**
2. 關係本身需要 metadata(怎麼知道的、何時、多可信)— 五個必答問題對關係同樣成立。
3. 與兩表架構同構:關係的真相在 Observation_Log,當前值由 Knowledge_State 物化。不需第三套機制。

**代價(接受):** 查「Loox 的所有用戶」需透過 Knowledge_State 反查,非單一 JOIN。Knowledge_State 本為查詢而生,此代價可承受。

---

## 六、Observation value 的型別結構(預留給 Observation Schema)

為避免 value 型別在 Phase 2 Normalize 時卡死,Observation Schema 需採判別欄 + 分欄儲存:

```
value_type:       'string' | 'number' | 'boolean' | 'entity_ref' | 'json'
value_raw:        nullable  -- crawler 看到的原貌(例 "$48.00 USD")
value_normalized: nullable  -- 正規化後的可計算值(例 48.0)
value_entity_id:  nullable  -- 僅 value_type = 'entity_ref' 時使用
```

**理由:**
- 型別由資料自己聲明,不靠 feature 名稱猜 —「靠慣例猜型別」是資料層腐敗的起點。
- `value_raw` / `value_normalized` 分開 = Provenance 在單一值層級的落實:normalize 邏輯若有 bug,raw 仍在,可重跑(「衍生層可從真相重建」的微觀版本)。

---

## 七、決策記錄(Decision Graph 的第一批人工記錄)

| # | 決策 | 決定者 | 日期 |
|---|---|---|---|
| 1 | Entity 準入判準 =「以它為中心聚合觀測」 | Jeff approve | 2026-06-27 |
| 2 | 第一版僅 Store + ReviewApp | Jeff approve | 2026-06-27 |
| 3 | 關係存成 Observation,不用固定外鍵 | Jeff approve | 2026-06-27 |
| 4 | canonical_key normalize 規範需明文化 | Jeff 提出,採納 | 2026-06-27 |
| 5 | value 採 value_type 判別欄 + 分欄儲存 | Jeff 提出,採納 | 2026-06-27 |
