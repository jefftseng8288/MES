# MES — Findings(技術教訓與原則)

> **用途:** 記「踩過的坑、根因、據此立下的原則」。
> **從實作過程長出來,不預先填滿。** 不編造尚未發生的「踩坑」記錄。
>
> 下方先列「已定案、屬原則級」的條目(每條註明來源文件),作為地基;
> 實作中真正踩到的坑與新原則,往後逐條追加。

---

## 已定案原則(來自 Phase 0 定稿文件)

- **Event Sourcing:Observation_Log 唯一真相,Knowledge_State 可重建。**
  Observation_Log 是唯一 source of truth(Append-Only);Knowledge_State 是物化視圖,永遠可砍掉並從 Observation_Log 完整重建。絕無「直接改 Knowledge_State」的後門。
  *(來源:`MES_Knowledge_Schema_v1.md`)*

- **失敗不偽裝:三值語義。**
  `observed` / `fetch_failed` / `not_found` 在 schema 層即區分,程式無法混淆。抓取失敗與「確認不存在」是兩件事,失敗絕不可記成 0、空值或偽裝成 not_found。
  *(來源:`MES_Observation_Schema_v1.md`)*

- **Provider Agnostic(P6):核心能力不依賴單一資料源。**
  MES 可利用任何資料 provider(Crawler / 搜尋源 / Partner API / CSV / Manual…),但核心能力不綁死在任何單一 provider 上;搜尋源等是可替換零件,換零件不動架構。
  *(來源:`MES_Roadmap_v8.md`,P6)*

- **Build vs Buy 四分類。**
  Raw = 自己抓(公開端點直接讀得到)/ Inferred = 自己推(該自己鍛鍊的核心能力)/ Historical = 買時間(過去沒記就拿不到,等得起就自己累積)/ Third-party estimate = 買專有管道(自己抓不到也推不出的外部估算)。第一版:不採購 Store Leads,自己做 Raw + Inferred。
  *(來源:`MES_Build_vs_Buy_Matrix_v1.md`)*

---

## 實作中長出的教訓

- **async 專案要保留同步 driver 給 Alembic(2026-07-10)。**
  Alembic 的 migration 執行本質是**同步的**:其 `migrations/env.py` 用同步 `engine_from_config`。async 專案若把 `MES_DATABASE_URL` 全面改成 asyncpg,`alembic current` / migration 會壞。據此的做法(已驗證):保留 `psycopg` 相依供 Alembic、App 端用 `asyncpg`;在 `migrations/env.py` 內把 asyncpg URL 轉回同步 driver(`+asyncpg` → `+psycopg`)供 Alembic 連線。兩個 driver 並存是「SQLAlchemy + Alembic + async」的標準組合,不是冗餘,各有其用。這是「移除/替換前先逐一清點,勿為單一目標連帶犧牲仍在使用的功能」(見 `CLAUDE.md` 核心識別欄位保護 規則 1)的一次真實應用——沒有為了「全面 async」把 psycopg 一刀砍掉弄壞 Alembic。實跑驗證:`alembic current` 連線正常。
