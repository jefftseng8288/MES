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

- **Append-Only 靠 DB trigger 物理鎖死,別靠 ORM 紀律(2026-07-11)。**
  observation_log 是唯一真相,Append-Only 是鐵律。實作用 PostgreSQL plpgsql 觸發器(`mes_reject_mutation` + `BEFORE UPDATE`/`BEFORE DELETE` 各一個 trigger,`RAISE EXCEPTION`)在 DB 層物理拒絕 UPDATE/DELETE——任何來源(ORM、psql、手動)都擋得住,不靠應用層自律。**鎖要套對表:** 只鎖 observation_log;knowledge_state 是物化視圖必須允許 UPDATE(取值規則覆寫當前值),誤鎖它會弄壞物化語義;entity 一般表不鎖。trigger 由 Alembic migration 用 `op.execute` 建立,downgrade 對稱 `DROP TRIGGER`/`DROP FUNCTION`,已實測 down→up 回滾正常。測試中觸發器的例外會被 SQLAlchemy 包成 `DBAPIError`,訊息含 "append-only",據此斷言。

- **UUID 主鍵在 async 於 Python 端生成,不依賴 DB(2026-07-11)。**
  用 SQLAlchemy `Uuid` 欄位型別 + `default=uuid.uuid4`(Python 端生成),不用 DB 自增/`gen_random_uuid()`。好處:寫入前就知道 ID,寫 observation 後可直接拿 `observation_id` 去填 knowledge_state 的 `source_observation_id`,不必先 flush/round-trip 回 DB 取 ID——在 async(asyncpg)下少一次 await、Provenance 鏈接得更直接。migration 種子資料的 UUID 直接 hardcode 固定值,確保 up/down 可重現。

- **受控字串用 VARCHAR + CHECK,不用原生 ENUM(2026-07-11)。**
  status / confidence / value_type / source / entity_type 這些受控清單未來會升版;CHECK 約束改起來(DROP + ADD CONSTRAINT)比 PostgreSQL `ALTER TYPE ... ADD VALUE` 容易且可回滾。CHECK 表達式集中由 `models.py` 的受控字串常數(`STATUSES` 等)+ `_sql_in()` 單一函式生成,不散落硬寫(呼應 `CLAUDE.md` 規則 3:取值/正規化邏輯收斂單一函式)。feature 刻意**不** CHECK 鎖死——它是彈性增長的詞彙,鎖死會逼每加一個 feature 就改 schema。

- **`value_raw` 的空字串要跟 NULL 一起擋,`IS NOT NULL` 不夠(2026-07-11)。**
  discriminated union 的 status='observed' 契約要求 value_raw 有值。只寫 `value_raw IS NOT NULL` 會被 `value_raw = ''`(空字串)形式通過卻實質為空——空字串是「非 NULL」但沒有任何 feature 值,是靜默腐敗。CHECK 必須寫成 `value_raw IS NOT NULL AND btrim(value_raw) <> ''`,連只有空白的字串也一起擋。實測:寫入 `value_raw='   '` 被 DB 拒絕。這是資料庫版的「核心欄位取不到時不可無聲 fallback 成空字串」(`CLAUDE.md` 規則 2)。

- **`value_raw` 語義邊界:只存 feature 原始值原貌,不存抓取/推論證據(2026-07-11)。**
  `value_raw` 只保存「該 feature 的原始值原貌」(例:country 的 `"Taiwan"`、avg_price 的 `"$48.00 USD"`)。**絕不可**塞入取得行為的執行資訊:HTTP status code、原始 URL、parser error、命中的 HTML selector、signature evidence、搜尋候選清單——這些是「怎麼取到的」的證據,不是 feature value,未來另立 evidence / 執行紀錄欄位承擔。一旦混入,value_raw 的語義就腐敗:同一欄有時是值、有時是除錯線索,下游(Knowledge 投影、Insight)無法信任。此邊界寫進 `models.py` discriminated-union 註解,並靠「typed 欄只承接與 value_type 相符的正規化值」在結構上把值與證據分開。

- **雙骨牌:推論失敗也是第一等 Observation,掛在 Seed 上,失敗不偽裝(2026-07-11 Phase 1-C)。**
  Name→Domain 推論不是「成功才記、失敗就丟」。撈到 Store Name 先落一個 `store_name_seed` entity(骨牌一),推論的**結果**(不論成敗)都是一筆掛在 Seed 上的 `inferred_domain` observation(骨牌二)。成功 → entity_ref 指向 store、confidence=inferred(誠實:domain 是推論的,不是確定的);失敗 → 全欄 NULL、依三值分流。好處:Seed 永遠在,推論可重跑;「推了但沒成」是可查詢的歷史,不是資料黑洞。這把「失敗訊號不可偽裝」從 crawler 抓取延伸到推論環節。

- **fetch_failed vs not_found 的精確語義:系統無能 vs 確認搜不到,not_found ≠ 店已死(2026-07-11 Phase 1-C)。**
  推論失敗必須精確分流,否則統計會被污染。`fetch_failed` = 推論**動作沒成功執行**(DuckDuckGo 429 / timeout / 連線失敗 / 結果容器解析不到)——我們**不知道**結果。`not_found` = 推論**成功執行了,但未能識別出可信 domain**(200 正常回傳、結果跑完,但全是黑名單平台或查無)——我們確認「以現有 Provider 能力搜不到」。**關鍵:not_found 只宣稱「我們搜不到」,無權宣稱「這家店在網路上不存在 / 已死」**——我們能觀測的是自己的推論能力邊界,不是市場的死活。分流對了,`SELECT COUNT(*) ... WHERE feature='inferred_domain' AND status='not_found'` 才等於「以現有能力搜不到的店佔比」,不被壞掉的工具(fetch_failed)污染。**真實驗證(live run):** 對真實店連續查詢後 DuckDuckGo 開始限流,三值語義誠實運作——限流的 query 記為 `fetch_failed`(系統沒拿到結果)、搜得到結果但無可信 domain 的記為 `not_found`、命中的記為 `observed`,失敗未被偽裝成成功或 0。這正是「捍衛失敗不偽裝」在真實外部不穩定下的兌現。

- **實跑真實教訓:DuckDuckGo 只剩一個 endpoint 能用;selector 是版本敏感的活體(2026-07-11 Phase 1-C)。**
  (a)**DuckDuckGo:** 2026 當下只有 `https://html.duckduckgo.com/html/`(POST `q`)回得出結果;`duckduckgo.com/html/` 回「browser not supported」空殼。搜尋源是可替換零件(P6),此事實記下供未來換零件參考——被封時**如實回報、不靜默 fallback**。(b)**Loox 評論頁:** 真實每頁 ~10 則(非假設的 20);Store Name 在 `data-merchant-review` 區塊的 `<span title="...">`(`title` 屬性存完整名)。selector 對真實 HTML 實測後才寫死,並集中在 `scrape.py` 單一 regex——結構一變,`parse_store_names` 回傳變少/空,那是訊號,不硬湊 selector 假裝有資料。(c)**先探再寫:** 碰外部世界的程式,先抓一頁看真 HTML、先打一次看真回應,再據實寫解析——別憑想像填結構。

- **v1 schema 缺兩個欄位軸:inference 的 source 與 provider 無處可放(2026-07-11 Phase 1-C)。✅ 已解決(同日細化)。**
  (a)**source:** 受控清單原無「網頁搜尋」值,inferred_domain 曾暫用 `html_page`。→ **已加 `web_search`(清單 v2)並物理落地 CHECK。** (b)**producer:** 原無專屬欄,`duckduckgo_v1` 曾暫塞 `crawler_version`。→ **已新增 `producer` 欄(NOT NULL + CHECK),crawler_version 歸位為純 git hash。** 下面三條記錄解決過程長出的原則。

- **source 記成 html_page 是靜默 fallback,會腐敗 Provenance(2026-07-11)。**
  `source` 是 Provenance 的「管道」欄,回答「透過什麼觀測到」。inferred_domain 的 domain 是**網頁搜尋推論**來的,記成 `html_page`(直接抓頁面)在來源追溯上就是說謊——等同 `CLAUDE.md` 規則 2 禁止的靜默 fallback,只是發生在 source 欄而非 value 欄。後果:未來無法區分「直接讀到」與「搜尋推論」兩種可信度天差地遠的管道,聚合統計失真。正解:新增精確的受控值 `web_search`,寧可升清單版本,不用 least-wrong 的舊值頂替。**管道欄寧可補一個誠實的新值,也不要借一個語義不符的舊值。**

- **producer 是「模型/方法競技場」的核心基因欄,每筆 observation 必填(2026-07-11)。**
  `producer` = 產生這筆值的方法/模型(責任主體、語義版本),NOT NULL——**每筆觀測都必須說得出「是誰做的裁決」**。這是未來評估「哪個 producer 推論成功率高 / 觀察力強」的原始基因:沒有它,同一份 observation_log 裡混著 crawler 直讀、DDG 推論、人工餵入,卻分不出誰產出的、也就無法比較。第一版三值 mes_crawler_v1 / duckduckgo_v1 / manual_v1。**命名嚴格用 `producer`,不與 P6 的 `provider`(外部資料源)混用**:provider 答「用了誰的資料源」,producer 答「誰產生了這筆值」——DuckDuckGo 兩者恰好同名,但角色不同,欄位語義要釘死。

- **producer / source / crawler_version 三欄各答一個問題,不重疊(2026-07-11)。**
  Provenance 要完整且不重疊:`source`=透過什麼**管道**(html_page / products_json / html_signature / web_search / manual / monitor)、`producer`=由哪個**方法/模型**產生(mes_crawler_v1 / duckduckgo_v1 / manual_v1)、`crawler_version`=執行當時的**實體程式碼版本**(git SHA-1,只存 hash)。合起來一句可讀:「這筆 domain,透過 web_search、由 duckduckgo_v1、在 commit abc1234 時固化。」**教訓:一個欄位塞多個語義軸(如把 producer 塞進 crawler_version)是權宜腐敗**,趁早拆成正交欄位;crawler_version 寧可 NULL(尚未接 git)也不塞非 hash 值。

- **收窄受控詞彙的 downgrade,要求該值的資料先清掉——這是正確行為,不是 migration bug(2026-07-11)。**
  加 `web_search` 到 source CHECK 的 downgrade 會把 CHECK 收窄回舊值集;若表中已有 `source='web_search'` 的列,`ALTER TABLE ADD CONSTRAINT` 會因既有列違反而失敗。這是資料庫在保護一致性,正確。清資料時又撞到 observation_log 的 Append-Only trigger 擋 DELETE——解法:**`TRUNCATE` 不觸發 row-level DELETE trigger**,可用來清空 Append-Only 表(dev 場景)。啟示:down 遷移在「窄化約束」方向天生受既有資料約束;空表或相容資料才保證可回滾。**更深的引申:受控清單(source / producer / value_type / status …)在有資料後近乎「只能往前加、難往後收」——加一個值容易,但一旦有列用了它就很難再收回 CHECK。所以新增受控值要比想像中更慎重:寧可想清楚再加,不要先加了、用了、再想撤。**
