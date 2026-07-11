# MES — Build vs Buy Matrix v1

> 上游文件:MES_Roadmap(Provider Agnostic 原則)
> 狀態:定稿(2026-07-03)
> 用途:MES 評估「任何」資料 provider 的通用判斷工具。不只評估 Store Leads —
> 每進一個新產業、每遇到一個新資料商,都用這張矩陣問同一個問題:
> **它賣給我的,是資料、是能力、是時間,還是我根本沒有的專有管道?**

---

## 一、四分類判準

對 provider 的每一個欄位,依序問四個問題,判定它屬於哪一類:

| 類別 | 判準 | MES 的決策 |
|---|---|---|
| **Raw** | 當下去看店面前端 / 公開端點就直接讀得到? | **自己抓。** 買它只是買「幫你抓好的時間」,不需要。 |
| **Inferred** | 看得到原料,但需要判斷 / 比對 / 計算才得到? | **自己推,而且該自己推。** 這是 MES 面對任何產業都必須有的核心能力;買它 = 放棄鍛鍊這個能力。 |
| **Historical** | 牽涉「過去某時點的狀態」或「跨時間的變化」,過去沒記錄就永遠拿不到? | **買的是時間。** 等得起 → 自己從今天累積;等不起、需要立即的歷史 → 才值得買。 |
| **Third-party estimate** | 不是店面公開的,是 provider 用它專有的外部資料源推估的(流量、Common Crawl、第三方資料)? | **買的是你根本沒有的專有管道。** 自己抓不到也推不出,除非自建同等基礎設施(另一個大工程)。 |

**兩側的本質區分:**
- Raw + Inferred = 「省事」與「能力」→ MES 該自己做(自己做才有韌性、才有能力累積)。
- Historical + Third-party estimate = 「非它不可」的兩種 → 但性質不同:
  - Historical 買的是**時間**(可用等待替代)。
  - Third-party estimate 買的是**專有管道**(等再久也生不出來,除非自建)。

---

## 二、範例:Store Leads 逐欄標記

> 資料來源:Store Leads 官方欄位定義頁(storeleads.app/help/faq)+ 昨日 API 回傳實例。
> 這是本矩陣的第一個應用範例,也示範「如何拆解一個 provider」。

### Raw — 自己抓得到(不需要買)
Domain、Alternate Domains、Platform(是否 Shopify)、Plan(是否 Shopify Plus)、
Currency、Content Language、Country(宣告)、Products Sold(product count)、
Average Product Price、Product Variants、Product Images(當下數)、Average Product Weight、
Theme(Name/Style/Vendor/Version)、Apps(當下裝了什麼)、Technologies、
Contact Page / Retailer Page / Store Locator URL、Meta Description/Keywords、Description、
Sales Channels、Shipping Carriers、Ships-to Countries、Features(如 Storefront API)、
Status(當下 active/inactive)、Social Media handles

### Inferred — 自己推得出、且該自己推(鍛鍊 MES 核心能力)
Category(產業分類 — 從商品與文案判斷)、Merchant Name、
Location / City / State / Zip(從公開資訊推)、
Tags(Dropshipper / Print on Demand 等 — 是判斷,不是讀取)、
Follower change rate(有當下數字即可自算,但須自己持續記錄)

### Historical — 過去沒記就永遠拿不到(可能非買不可:買時間)
Created(開店日期)、Last Plan + Last Plan Changed Date、
Last Platform + Last Platform Changed Date、Theme Change Log、
Apps 的 installed_at 與 installs_30d / 90d、Product Images 30/90/364 天新增數

### Third-party estimate — 專有外部資料管道(自己抓不到也推不出:買管道)
Estimated Sales、Estimated Visits、Estimated Page Views、Employees、
Common Crawl Centrality、Common Crawl Page Rank、Rank / Platform Rank、
Street Address(官方稱僅約 1% 的店有)

---

## 三、對第一版(EscapeFlow / Phase 1)的結論

**第一版:不採購 Store Leads。自己做 Raw + Inferred。**

理由(逐一對應四分類):
1. **Raw + Inferred 佔絕大多數欄位** —— 全部能自己做,且 Inferred 那批**該自己做**(核心能力)。
2. **Historical 第一版用不到** —— 驗證認知鏈(Observation→Knowledge→Insight)靠「自己從今天起累積的當下觀測」即可;不需要別人的歷史。Growth Signal 在 Roadmap 中本就是「等時間序列自己長出」。自己的 Append-Only Observation_Log 從第一天起就在累積自己的歷史。
3. **Third-party estimate 第一版用不到** —— 流量 / 營收估算屬「精準行銷」階段,不是「驗證認知鏈」階段所需。

**未來採購的觸發條件(明確,非情緒):**
- 需要**立即的歷史**(等不及自己累積 N 個月)→ 買 Historical。
- 需要**流量/營收估算**且不自建估算基礎設施 → 買 Third-party estimate。
- 兩者都是「買明確的東西(時間 / 專有管道)」,不是「買含糊的省事」。

---

## 四、這張矩陣的長期意義

它不是一次性的 Store Leads 評估表,是 **MES 面對任何產業、任何 provider 的通用能力地圖**。

- 進入一個**有**成熟資料商的產業(如 Shopify)→ 用它判斷「哪些該買、哪些該自己做」。
- 進入一個**沒有**任何資料商的產業 → 四分類自動塌成兩類:所有欄位不是 Raw 就是 Inferred(因為沒有 provider 提供 Historical / estimate),MES 就從 Discovery 開始全部自己做。

**呼應 Provider Agnostic:有 provider,矩陣幫你省力;沒 provider,矩陣告訴你自己做什麼。MES 兩種情況都能活。**

---

## 五、決策記錄

| # | 決策 | 決定者 | 日期 |
|---|---|---|---|
| 1 | 採四分類(Raw / Inferred / Historical / Third-party estimate),第四類自 v1 表獨立 | Jeff approve | 2026-07-03 |
| 2 | 第一版不採購 Store Leads,自己做 Raw + Inferred | 共同定案 | 2026-07-03 |
| 3 | 採購觸發條件明文化:立即的歷史 → 買 Historical;流量估算 → 買 Third-party estimate | 共同定案 | 2026-07-03 |
