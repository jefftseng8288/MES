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

- **健康報告三比例必須分開,不可合併成單一「成功率」——失敗不偽裝的儀表層延伸(2026-07-13 Phase 1 排程)。**
  撈取健康報告把 `observed` / `not_found` / `fetch_failed` **分三個比例**呈現。若合併成一個「成功率」(例如把 not_found + fetch_failed 都算「失敗」、或只報 observed 佔比),會在**撈到一批死店多的 Seed 時產生假訊號**:not_found 高其實是**市場事實**(這批店真的搜不到/死了),與系統無關;fetch_failed 高才是**我們被限流/系統無能**,是唯一該觸發「調整節奏」的訊號。混在一起會誤導我們在市場問題上瞎調系統參數。這是「失敗不偽裝」(Observation 層的三值語義)一路延伸到**人看的健康儀表層**:儀表也不准把兩種性質不同的「非 observed」抹成一個數字。判讀規則寫進報告本文:**fetch_failed 是主儀表,not_found 不是。**

- **節流間隔是「待回饋調整的起點」,不是宣稱正確的答案(2026-07-13)。**
  每筆之間 20–150 秒隨機 sleep 是**保守的起始值**,不是算出來的最佳解。真實環境(DuckDuckGo 限流)是持續變動的,沒有預先能猜準的完美參數。作法:先用保守間隔跑一週,讓 `fetch_failed` 曲線回饋——**一週都低 → 之後可縮短加快;飆高 → 拉長或另想辦法(換 producer/加代理/降頻)**。這呼應「先有 Observation 再演化」:不預先猜規則,先累積真實資料點再由人判斷。實測佐證:間隔縮到 2–5 秒猛打→fetch_failed 75%,再壓到 5–15 秒連打→**100%**(DDG 已對本機硬限流);證明間隔對限流極敏感、且壓縮間隔會把井打壞,更該由真實回饋(在真實 20–150s 節奏下)定,而非拍腦袋。此起點值標在 `pipeline.py` 註解,明列「待回饋調整」。

- **隨機間隔是硬性要求,不是禮貌建議——定量節奏 = 向對方廣播「我是機器人」(2026-07-13)。**
  節流用 `random.uniform(20, 150)` **每次重新隨機**,不可用固定間隔。固定節奏(每次剛好 85 秒)是機器人最明顯的指紋,反而**自製限流**——對方一眼看出非人類流量就更容易封。範圍刻意拉寬(20–150,跨度 130 秒)讓節奏更不規則、更貼近人類瀏覽,更難被指紋辨識。所以「隨機且寬」是規格層級的硬要求,不是「有空再做」的優化。同理已用在 scraper 翻頁(5–25 秒隨機)。**任何對外請求的節流,間隔一律隨機化、跨度拉寬,不留固定週期。**

- **`observed` ≠「domain 抓對」——但這不是說謊,inferred 本就標記為「猜的」(2026-07-14)。**
  第一批生產批(30 筆)健康報告 observed 97%、fetch_failed 0%,但把 domain 攤開看,~13/29 其實抓錯(`shop.app`、接案公司 `techtic.com`、Shopify app 商 `hulkapps.com`、工具 `n8n.io` / `qikify.com`、新聞 `marketwatch.com` 等),真實命中率約 5 成。**關鍵澄清:健康報告的 `observed` 量的是「inference 有沒有被限流 / 跑不跑得動」(系統健康),從設計上就不量、也不宣稱「domain 對不對」(推論精確度)。** 而且這批 observation 的 `confidence` 全是 **`inferred`**(誠實標記「這是推論的、可能錯」),`status='observed'` 只代表「成功產出了一個推論值」,不代表值正確。所以「observed 高但抓錯多」不是報告說謊,是**兩個不同維度**:健康(限流)vs 精確度(對不對)。精確度是 `inferred_domain` 這個元特徵**未來要被評估的對象**,現在正被真實資料揭露。

- **不採用黑名單擴充來「濾掉」錯 domain——入口丟棄 = 在觀測層做判斷,違反「抓取不判斷」(2026-07-14,Jeff 定案)。**
  面對抓錯的 domain(`shop.app` 等),直覺是「把它們加進黑名單濾掉」。**明確不採用。** 理由:黑名單 = 在**入口就丟棄**觀測 = 在**觀測/抓取層做了「這個對不對」的判斷**,違反 P2(Knowledge 中立)/ 觀測層只記錄不評分的原則。一旦在入口丟資料,就再也回不來,且把「判斷」偷渡進了本該中立的抓取層。**正解:抓取層照實記錄它推論到什麼(哪怕是 `shop.app`),可信度/對錯的裁決留給未來系統的上層(Insight/Hypothesis 層),且要等**累積夠多錯誤 pattern** 後,才有依據去設計「怎麼判斷可信度」——不預先拍腦袋定規則(呼應「先有 Observation 再演化」)。(附註:`inference.py` 現存的 `_BLACKLIST` 是把「明顯非商店官網的平台/聚合站」在推論**當下**排除以選出候選,性質上也踩到同一條線;此決策之後,既有黑名單是否該退場、改由上層裁決,一併留待未來累積 pattern 後重審。)

- **預建的 APScheduler `CronTrigger` 不繼承 scheduler 的 timezone,會抓系統本地時區(2026-07-14)。**
  排程設 `AsyncIOScheduler(timezone="UTC")` + `add_job(_job, CronTrigger(hour=2))`(trigger 未給 tz),原以為 02:00 UTC 跑;實際第一批跑在 **18:00 UTC = 02:00 台灣**——因為**預先 new 出來的 `CronTrigger` 不會繼承 scheduler 的 timezone,而是在建構時抓 `get_localzone()`(系統本地 = Asia/Taipei)**。結果 `hour=2` 變成「02:00 台灣」,比意圖早 8 小時。修法:**trigger 一律明確帶 `timezone=`**(`CronTrigger(hour="2,10,21", minute=0, timezone="Asia/Taipei")`),不靠繼承。教訓:凡是「時間 × 時區」的設定,把時區**寫死在最靠近該設定的地方**,不要依賴外層預設會傳進來——時區的預設繼承是常見的坑。

- **batch_id 是 Provenance 的第四軸:「哪一批」——與 producer/source/crawler_version 正交(2026-07-14)。**
  Provenance 原本三軸:`source`(哪個管道)/ `producer`(哪個方法模型)/ `crawler_version`(哪版程式碼)。加 `batch_id`(哪一次 run)成第四軸,各答不同問題。格式 `YYYY-MM-DD-NN`(台灣日期+當天批序)刻意人讀友好+可排序+同日多批分開,而**不是**用不透明的 UUID run_id——因為它要被人直接讀(健康報告、log、SQL 篩選)。**只加 `observation_log`(觀測事件的屬性),不加 `knowledge_state`**:後者是當前值物化,一個 (entity, feature) 的當前值可能由不同批的觀測投影而來,「哪一批」對它語義不清——欄位只加在語義清楚的那張表,不為對稱而硬加。批號的 NN **固定語義**(2026-07-14 定案):`-01/-02/-03` = 三個排程時段(02:00/10:00/21:00 台灣,由 scheduler 傳 slot),`-04+` = 手動 run;同時段重跑沿用同批號。**為何不用「數今天幾批 +1」:** 那會被測試/手動 run 灌水(實測今晚 21:00 排程批因當天測試資料被編成 `-04`),且序號不帶「哪個時段」語義。固定槽位讓批號本身編碼時段——看 `-03` 就知道是晚上那批,直接支援「比較同日各時段 fetch_failed」的核心判讀,並免疫污染。教訓:**當一個識別碼要被人拿來比較/篩選時,讓它的結構直接編碼你要比較的維度(時段),別用「產生順序」這種會被無關事件干擾的隱性計數。**

- **一天三批是為了測「一天總量」對 DDG 的累積效應,不是挑戰邊界(2026-07-14)。**
  從一天一批(30)改三批(3×30=90),意圖是**溫和**把一天總量加上去,用真實 `fetch_failed` 判斷 DDG 撐不撐得住——不是壓縮間隔爆量(那是把井打壞,已驗證 2–5s→75%、5–15s→100%)。所以三批**分散**在台灣 02:00/10:00/21:00(間隔 8h/11h/5h),讓 DDG 中間有喘息,測的是「一天累積」而非「短時峰值」。判讀關鍵:**比較同日『越晚的批 fetch_failed 是否越高』**——若越晚越高,代表一天總量觸發了累積限流,該降總量;若三批都低,代表 90/日 撐得住、之後可再加。**batch size=30 / 間隔 20–150 / 一天幾批,全是待真實負載修正的暫定值,非已驗證安全基準**;這正是用三批的真實資料去修正它們的過程(先有 Observation 再演化)。

- **供給瓶頸比限流先到:單一 review app 的新種子池會被抽乾——解法是「加來源」而非「撈更兇」(2026-07-15)。**
  改三批後第二天,10:00 批只湊到 11/30——**不是 DDG 限流(這批 100% observed),是 Loox 的「未撈過的新 Store Name」在 `MAX_PAGES=12`(約 120 名)範圍內見底了**。Seed 去重使我們**不重複撈同店湊數**,所以供給一乾就誠實反映成小批(健康報告的 `actual < requested`)。這揭露:**在真實世界,供給上限往往比 DDG 限流上限先撞到**。解法**不是**把單一來源撈更兇(翻更深 = 越舊越不活躍、也更像爬蟲),而是**加來源**:scraper 從只抓 loox 擴到五個 review app(judgeme/yotpo/okendo/stamped),供給立刻放大數倍(實測 page 1 各 app 就湊滿 30,loox 已貢獻 0)。呼應 P6:能力(找新種子)不綁死在單一來源上。**教訓:量能不足時,先問「能不能多接一個來源」,而不是「能不能對現有來源更用力」。**

- **處理狀態 ≠ 觀測資料:store_harvest_state 可自由 UPDATE,與 Append-Only/entity 純淨無關(2026-07-15 Phase 1-D)。**
  「哪些 store 待抓 feature」是**系統內部的待辦狀態**(pending/done/failed),不是市場觀測。它該可自由 UPDATE(抓完就改 done),與「觀測資料 Append-Only、entity 只作觀測掛載點」的鐵律是**兩回事**。所以放**獨立表** `store_harvest_state`(非在 entity 加欄):entity 保持純淨,狀態有自己的家。判準:一個欄位/表存的是「發生過的觀測事實」(→ Append-Only、不可改)還是「當前的處理進度」(→ 可 UPDATE 的狀態)?兩者要分開,不要把可變狀態塞進不可變的觀測/實體表。

- **戳店面與戳 DDG 是兩條獨立鏈路,限流獨立、可並行(2026-07-15 Phase 1-D)。**
  baseline(Name→Domain)戳的是 **DuckDuckGo**;feature 抓取戳的是 **各店自己的伺服器/Cloudflare**。不同對象 → 限流計數獨立、失敗互不牽連。所以做成**分離的獨立排程**(baseline 三批/日 vs harvest 每 3h),而非串在同一鏈路——一邊被限流不拖累另一邊,兩邊的節奏各自依自己對象的實況調整。教訓:不同外部依賴的節流/排程要**按「戳的是誰」分開管理**,別混成一條。

- **`uses_review_app` 靠 HTML script 特徵是推斷,標 inferred(不是 certain)(2026-07-15 Phase 1-D)。**
  9 個 feature 裡 8 個是直讀店家自報(products.json、Shopify.* 變數)→ certain;唯獨 `uses_review_app` 是靠首頁 script/DOM 特徵**比對推斷**「這家在用某 app」——script 可能殘留(裝過已移除)、可能誤判 → 誠實標 **inferred**,如同 inferred_domain。沒命中 signature ≠ 沒裝,只代表「現有 signature library 沒識別到」→ status `not_found` + inferred。**判準同一條:直讀事實=certain,比對/推斷得到的=inferred。**

- **SQLAlchemy JSONB 欄的 `None` 預設是 JSON `null` 不是 SQL NULL,會打破 CHECK(2026-07-15,踩坑)。**
  ORM 明寫 `value_json=None` 時,SQLAlchemy 的 `JSONB` 型別預設把 Python `None` 存成 **JSON `'null'`(一個非 NULL 的 JSONB 值)**,於是 `value_json IS NULL` 為 **false**,打破 discriminated-union 的 value_typed CHECK(「其餘 typed 欄全 NULL」)。詭異點:同樣的值用 raw SQL / 不明寫該欄 → SQL NULL → 過;ORM 明寫 None → JSON null → 不過(先前測試沒明寫才沒暴露)。修:欄位宣告 `JSONB(none_as_null=True)`,讓 Python None → SQL NULL。教訓:**JSON/JSONB 欄一律設 `none_as_null=True`**,否則「空值」有兩種(SQL NULL vs JSON null)會靜默咬人。

- **外部平台的 URL handle 是歷史遺留,必須實測、不能憑名字猜(2026-07-15)。**
  五個 review app 的 App Store 評論頁 handle 只有 loox=`loox` 直覺;其餘都不等於 app 名:yotpo=`yotpo-social-reviews`、okendo=`okendo-reviews`、**stamped=`product-reviews-addon`**(該 app 原名 Product Reviews Addon,slug 是歷史遺留)。若憑「app 名當 handle」猜,四個裡三個 404。做法:**先打候選 URL 看 200 + 能不能解析出名字,確認了才寫進 `REVIEW_APP_HANDLES`**(該常數 2026-07-31 已改名 `SEED_SOURCE_HANDLES`,因來源已不限 review app);selector 則先驗證是 App Store 通用 HTML(五個都用同一條)。再次印證「碰外部世界先探再寫、不憑想像」。

- **驗收驗能力,不卡時間——用「連續 N 天」當驗收會誤判成敗(2026-07-15,範式校正)。**
  Phase 驗收要回答的是「這個能力做出來了嗎」(是非題),不是「跑了多久」。**用時間・區間(連續 7 天、規模到 1000 家)當驗收,不管 N 設多少都不合理:** 任何有限區間都證不了「持續」,而持續是系統存在就會做的**常態**,不是要達成的目標。更糟的是它會**顛倒成敗判定**——真實案例:Loox 種子池**第一天就抽乾**;若用「連續 7 天有新增」當驗收,會判**失敗**,但那其實是**成功**(系統誠實反映供給見底、並用「加來源(五 app)」解掉)。反過來,若某 7 天剛好沒撞到問題,會把「運氣好」誤判成能力達成。**結論:驗收條件一律改成能力描述(能不能把 Seed 轉成誠實、結構對、可追溯的 Observation),時間・規模移除。時間在 MES 是資料累積的介質,不是成就的度量。** 原則入典於 `CLAUDE.md`。

- **警鈴只主動回報,不自動調整——先記「異常+原因」當未來自動化的燃料(2026-07-17)。**
  MES 加了「痛覺神經」:偵測異常 → 診斷原因 → 推 Telegram 喊 Jeff,但**不自動調策略**。理由:「遇到什麼狀況該怎麼調」的規則現在還是猜的,要先累積真實經驗才能自動化。所以每次觸發除了推播,也**結構化記進 `alert_log`**(異常類型 + 診斷 + 當天三批數據);若只推完就沒了,未來要做「自動生成調整策略」沒有歷史可學。核心是**診斷不能只報數字**——同樣「0 observed」,fetch_failed 佔滿(限流,該調節奏)vs not_found 佔滿(市場搜不到,未必動作)vs 無新 Seed(池子乾,該加來源)vs 批次無記錄(執行異常)是**四個完全不同的調整方向**,不分辨就等於沒診斷。

- **痛覺神經要獨立於「可能故障的東西」——警鈴不跟 harvest daemon 綁同一程序(2026-07-17)。**
  警鈴掛成**獨立** launchd(`com.mes.alarm`,一次性),不塞進常駐的 harvest daemon 的 scheduler。理由:若 harvest daemon 死了/卡住,綁在它裡面的警鈴也一起啞——**正好在最需要叫痛的時候失聲**。獨立程序才能在 daemon 掛掉時照跑,並報出「批次無記錄 → 執行異常」。教訓:**監控/告警程序要與被監控對象在故障域上隔離**,不能同生共死。

- **警鈴門檻(<10 seeds / >15 fetch_failed)是暫定起點,非安全基準(2026-07-17)。**
  「連續兩批新 Seed <10」「連續兩批 fetch_failed >15」「單批 0 observed」的數字都是**拍出來的起點**,不是驗證過的閾值——標在 `alarm.py` 註解。等 `alert_log` 累積真實觸發記錄(真陽/假陽),再回頭校準門檻。這與「先有 Observation 再演化」一致:先讓它會叫,叫得準不準用真實案例調。

- **接外部推送服務(Telegram)要 credential-gated + 永不 raise(2026-07-17)。**
  MES 原本零接 Telegram(獨立專案,不繼承 EF_WorkFlow)。接上時:憑證(bot token / chat_id)缺就 **graceful no-op**(記 log、回 False),不是報錯——因為警鈴的 DB 記錄不該因「還沒設推送」而失敗;且推送本身**永不 raise**(用 try/except 包住 HTTP),一次推播失敗不能中斷整個巡檢。分層:偵測+記錄(核心,一定要成功)vs 推送(盡力而為,可失敗)。

- **投影引擎:`value` 與 `current_status` 掃的是不同子集(2026-07-19 Phase 2 第二批,核心設計)。**
  同一 (entity, feature),`value` 只從 **observed 子集**依取值規則挑(時間優先);`current_status` 從 **全部觀測(含 fetch_failed/not_found)** 取最新那筆的 status。這是決定 2 能成立的關鍵——半年前 observed=196、今天 fetch_failed → `value=196`(值不因網路波動歸零)、`observed_at=半年前`(誠實標新鮮度)、`current_status=fetch_failed`(誠實標最近一次沒抓到)。**若兩者掃同一子集就會退化**:只看 observed → current_status 永遠是 observed(看不到失敗);只看全部 → value 會被失敗筆的空值污染。真實資料驗證:2 列 value 保留 + current_status=fetch_failed。教訓:「當前值」與「當前狀態」是**兩個不同的問題**,要用兩個不同的掃描回答。

- **純函數重建禁用系統時間——重建冪等靠「時間全從資料投影」(2026-07-19 Phase 2)。**
  投影/重建**不准用 `now()` / `CURRENT_TIMESTAMP`**:所有寫入 knowledge_state 的時間維度(`observed_at` = 被取那筆 observed 的時間、`updated_at` = 最新一筆觀測的時間)100% 由 observation_log 的 `observed_at` 投影而來。理由:重建能力是驗收條件,而「砍表重建後與重建前完全一致」只有在**輸出不依賴執行當下的牆鐘**時才成立。連 tiebreaker 都要決定性——同 observed_at 同 confidence 時用 `observation_id`(穩定 UUID)收尾,否則 set 迭代序不同會讓兩次投影挑到不同筆。教訓:**要冪等的投影,任何「執行當下才知道的值」(牆鐘、隨機、迭代序)都是污染源,一律改由輸入資料決定。**

- **冗餘欄先驗證恆等再合併,不憑假設直接刪(2026-07-19 Phase 2,順序紀律)。**
  第一批加的 `last_observed_at`(= 被取為當前值那筆 observed 的時間)語義疑似與既有 `observed_at` 完全相同。合併前**先把投影邏輯寫出來、實跑、SQL 驗證兩欄在所有列恆等**(2905 列 0 不符),確認後才 migration 移除 `last_observed_at`、CHECK 改綁 `observed_at`。**順序不可反**——若一開始就假設恆等直接刪欄,萬一有場景不等(如未來若投影「無值列」,無值列的 observed_at 語義會與 last_observed_at 分岔)就會靜默丟資料。教訓:**刪任何「看起來多餘」的欄前,先讓真實資料證明它多餘。**

- **投影「無值列」會逼放寬 Provenance NOT NULL 鐵律——Jeff 定案:不建無值列、保鐵律(2026-07-19 Phase 2,鐵律 vs 效益的權衡)。**
  「從無成功觀測、只有失敗」的 (entity, feature) 若要投影一列(value 全 NULL + current_status),會逼 `source_observation_id` / `observed_at` / `confidence` / `producer` / `selection_rule_version` 五個 NOT NULL 欄(其中 source_observation_id 是 Phase 0 Provenance 鐵律)全部改 nullable——因為這些欄都是「描述當前值」的,無值時無意義。判斷:**放寬鐵律是動到資料層硬約束的大事,不由實作單方決定**——攤開 (A) 不建無值列保鐵律 / (B) 改投影無值列(需一組欄 nullable + gated CHECK)兩個選項給 Jeff。**Jeff 定案 (A):不建無值列。** knowledge_state 只答「當前已知值」,從無 observed 就是**查無此列**;要看「為何沒值 / 試過幾次」去查 observation_log(Append-Only 誠實記著所有失敗)。職責分工:knowledge_state = 當前值,observation_log = 完整歷史含失敗。教訓:遇到「要達成某功能就得鬆一條硬約束」時,**先停下來把選項和代價攤開給人決定,不要為了功能完整度默默鬆綁鐵律。**

- **`generated_at` 用 now() 不違反「投影禁用系統時間」——判準是「這個時間在描述什麼」(2026-07-19 Phase 2.5)。**
  Phase 2 立過鐵律:投影 knowledge_state 禁用 `now()`/`CURRENT_TIMESTAMP`,時間全由 observation_log 的 observed_at 投影(才能冪等重建)。但 Phase 2.5 的 `insight_store.generated_at` **刻意用執行時間**,這不是破例,是**兩者語義本就不同**:`observed_at` = 「這個**事實**何時被觀測」(歷史事實,錯了就是竄改歷史);`generated_at` = 「這個**描述**何時被產生」(本來就是執行當下,不在描述歷史)。代價是 insight_store **不冪等**(今天重算與明天重算 generated_at 不同)——**已知且接受**:insight 是「每天對當前 Knowledge 重新描述一次」的快照,不是歷史真相的投影;真相在 observation_log,insight 只是描述層。教訓:**「禁用系統時間」不是無差別禁令,判準是「這個時間欄在描述歷史事實,還是描述本次執行」**;前者禁、後者本來就該用。照抄原則而不看語義,會把描述層也做成假的歷史投影。

- **受控清單放 DB CHECK 還是應用層,用「穩不穩定」決定,不是一律下沉(2026-07-19 Phase 2.5)。**
  MES 既有慣例是受控字串一律 VARCHAR + DB CHECK(entity_type / source / producer / status / confidence),但 `insight_store.value_text`(標籤如 High SKU)**刻意不下沉 DB CHECK**,改用應用層 registry(`src/mes/insight_registry.py`)驗證。理由:**insight 標籤是正在創造、會演化的東西** —— 每加一個標籤就要改 migration,且「受控清單只能往前加、難往後收」(Phase 1-C 踩過:窄化 CHECK 會被既有資料擋住)。對比 `confidence` 是 Phase 0 既定三級、穩定不動 → 同一張表裡它就有 DB CHECK。**判準:「這份清單會不會頻繁改?」會 → 應用層(改一行 Python,不動 schema);不會 → DB CHECK(物理鎖死最強)。** 等 insight 類型穩定後再考慮下沉。**受控本身不打折**(不合法一樣明確報錯、不靜默通過),只是守門的位置換一層。

- **沒有下游行為,就不該設門檻——該記錄原始數值(2026-07-19 Phase 2.5,Jeff 定案)。**
  GrowthStatProducer 原本要做的是「成長率 → 吐 `Growth` 標籤」,改成**只記錄實際成長率數值、不設門檻**。理由:**門檻是一種判斷,而判斷該由「後面要做什麼行為」決定。** 現階段沒有任何下游行為(Phase 4 才會有),此時設門檻等於**憑空造判斷**,而且**會丟失資訊**——+19% 與 −50% 一旦被壓成同一類「非 Growth」,那個差異就永遠不見了(原始觀測還在,但 Insight 層已經把它抹平)。反過來,先誠實記錄數值,未來要怎麼切由那時的真實行為決定,切法還能改。教訓:**壓縮是有損的,只有當「損掉的部分確定用不到」時才該壓。** 判準:先問「現在有誰要拿這個判斷去做什麼?」沒有答案 → 不要壓,記原值。這也讓 registry 必須支援**數值型** insight_type(驗證「可解析為數值」而非列舉)。

- **Producer 要純函數又需要歷史資料 → 讓它「聲明」需求,由 Engine 統一撈(2026-07-19 Phase 2.5)。**
  設計定「Producer 是純函數、不自己撈 DB」,但 GrowthStatProducer 需要 30 天歷史 —— 兩個要求看似衝突。解法:**把「要什麼」與「怎麼拿」分離** —— Producer 只用類別屬性**聲明**它需要哪些當前 Facts(`required_features`)與哪些歷史(`required_history`),Engine 依聲明統一撈齊、打包成記憶體 `InsightContext` 再交給它。Producer 的 `produce(ctx)` 唯一參數就是 Context,**碰不到 session** → 純函數、可測試(記憶體造資料即可)、可重現。額外好處:Engine 能把 N 個 Producer 的需求**合併成一次批次查詢**(避免 N+1),而 Producer 完全不需要知道這件事。教訓:**「不准碰 DB」不等於「不能用需要 DB 的資料」——把依賴宣告出來、由外部注入,就同時拿到純度與能力。**

- **producer 欄若不受控,Provider 競技場的計分板會壞掉(2026-07-19 Phase 2.5,補第一批缺口)。**
  第一批把 insight 的 `producer` 定為「不下沉 DB CHECK」,卻**忘了補應用層守門**,結果它一度是完全自由字串。這比 value_text 更危險:`producer` 是**Provider 競技場的核心欄位**(未來要比較 rule_v1 / stat_v1 / LLM 誰的觀察力強),一旦混入 `rule_v1` / `rule_V1` / `ruleV1` 三種寫法,聚合統計就會把同一個實作者拆成三個,**計分板直接壞掉**(同 source 欄踩過的路)。補法:各 Producer 類別透過 `__init_subclass__` **自己聲明**識別、registry 統一收攏,寫入前 `validate_producer()` 擋未登記者;仍不下沉 DB(演化中)。教訓:**決定「不下沉 DB CHECK」的同時,必須當場把應用層守門補上——否則「受控」只是說說,實際是零管制。** 兩層都不管 = 比明確選擇任一層更糟。

- **對「不該被處理的對象」記 skip,是類別錯誤,會把真訊號埋掉(2026-07-19 Phase 2.5)。**
  InsightEngine 原本掃所有有 knowledge_state 的 entity,結果一次全量產生 **5918 筆 skip 記錄,其中 2477 筆來自 `store_name_seed`** —— 但 seed 依定義就是「還沒推出 domain 的名字」,**本來就不可能有 product_count**。為它記「無 product_count」不是失敗訊號,是**類別錯誤**(等同於抱怨 review_app entity 沒有商品數)。而執行報告的用途是支撐「要不要繼續觀察**這家店**」的決策,被 80% 的噪音淹沒就等於沒用(一年會累積約 200 萬列無意義記錄)。修法:Engine 只處理 `store` entity → skip 降為 964 筆,**且未損失任何真實產出**。教訓:**「誠實記錄失敗」的前提是「這件事本來該成功」;對本來就不適用的對象記失敗,不是誠實,是雜訊。** 設計失敗記錄時,先界定清楚「誰在這個能力的適用範圍內」。

- **Growth 類 insight 要「時間序列自然長出」的前提是基礎 feature 有在採集——source 沒開,再等也長不出來(2026-07-19 Phase 2.5,誠實性標記)。**
  Roadmap 一直說「Growth 等時間序列會自然長出」,這句話**隱含一個前提:那個基礎 feature 有在被採集**。實際踩到:`GrowthStatProducer` 算的是 `review_count` 的 30 天成長率,但 **`review_count` 不在 Feature Taxonomy v1 的 9 個市場特徵內、Phase 1-D 根本沒抓**,真實觀測數為 **0**。所以它對真實資料的產出不是「現在還不夠 30 天、再等等就有」,而是**永遠 0**——**不是時間問題,是源頭沒開。** 兩者看起來都是「暫時沒資料」,但一個會自己好,一個永遠不會,**誤判成前者就會一直等一個不會來的東西**。
  **判準(未來會再用到):做任何 Growth / 趨勢 / 時間序列類的 insight 前,先確認它依賴的基礎 feature「現在有沒有在抓」** —— 有抓 → 缺的是時間,等就有;沒抓 → 缺的是採集範圍,得先去改 Taxonomy / crawler,等再久都沒用。
  **連帶的誠實性要求:** Phase 2.5 標「✅ 能力達成」是對的(驗收驗能力:給它資料算得對,測試驗證過),但**必須同時標明「GROWTH_VELOCITY 目前無資料源」** —— 否則未來有人看到 ✅、又看到 registry 裡有 GROWTH_VELOCITY,會合理地以為它可用,**缺口就被 ✅ 蓋住了**,那正是「失敗訊號偽裝成沒事」在文件層的變形。**「能力達成」與「現在有東西可產出」是兩件事,文件要分開講清楚。**

---

## 2026-07-31 — 系統性失效的發現與修復(四條鏈路裡三條沒在做該做的事)

- **★「掛上去了」≠「會執行」≠「執行有產出」—— 三件事要分別驗證(2026-07-31,這天所有問題的總根源)。**
  一天之內同時撞到這三種的兩種:`projection` / `insight` 兩個 daemon 的 plist **寫好了、放在 repo 裡、看起來一切就緒,但從未被 `launchctl load`** → 屬第一種「根本沒執行」;`store_harvest` **有 load、有跑、log 每 3 小時都在寫**,但連續 **16 天(125 批)每批都挑同樣 3 家假網域** → 屬第二種「有執行但沒產出」。而我們一直以為四條鏈路都在正常運轉。**驗證方式要對應到層次:** 「有沒有掛」看 `launchctl list`;「會不會執行」要**實際觸發一次**(`launchctl kickstart`)看程序真的起來、log 真的被寫入;「執行有沒有產出」要看 DB 真的多了列。**只做前一項就宣稱「已部署」,是這天所有問題的共同形狀。**

- **★ 橫切監控只覆蓋一條鏈路,等於沒有(2026-07-31)。**
  警鈴當初的定位是「獨立橫切系統:節點只上報、判斷集中」,但實作只做了 baseline 一條。結果:**四條鏈路裡三條靜默失效超過兩週,而警鈴每天照常回報「一切正常」** —— 因為它看的正好是唯一正常的那條。**比沒有監控更危險:它給了「有在看」的錯覺。** 教訓:**監控的覆蓋率要跟系統的實際鏈路數對齊**;新增一條鏈路時,「把它接進監控」必須是同一批工作的一部分,不能是「之後再說」。

- **★「沒跑」與「跑了但沒產出」在產出面長得一模一樣 —— 必須主動上報心跳(2026-07-31)。**
  若警鈴只去「查產出」(例如看 knowledge_state 有沒有更新),上述兩種失效**都表現為「沒有新資料」**,分不出來 —— 而它們的修法天差地遠(一個是去 load daemon,一個是修挑選邏輯)。所以每條鏈路跑完都要**主動記一筆心跳**(`job_run_log`),**即使產出為 0 也要記**。有心跳才能三分:沒跑 / 跑了沒產出 / 跑了有產出。**推論:任何「用結果反推過程」的監控都有這個盲區;過程要自己說話。**

- **★ 產出為 0 要能分辨「正常閒置」與「異常」,否則警鈴會被訓練成狼來了(2026-07-31)。**
  harvest 挑到 0 家有兩種:候選池全被最小重抓間隔 gate 住(**自適應的正常結果**)vs 候選池有幾百家可抓卻挑到 0(**挑選邏輯壞了**)。兩者在「挑到 0 家」這個數字上完全相同。做法:心跳的 summary 必須帶**足以判斷的上下文**(候選池總數 / 被間隔跳過幾家 / 實際挑幾家),警鈴據此分辨 —— `eligible == 0` 才是正常閒置。**設計失敗訊號時,要一併想「這個訊號的正常版本長什麼樣」,否則不是漏報就是誤報。**

- **★ head-of-line blocking:「狀態決定候選」+「固定排序」= 永久卡死(2026-07-31)。**
  舊挑選邏輯 `WHERE state IS NULL OR status IN ('pending','failed')` + `ORDER BY created_at LIMIT 3`:失敗的店**永遠留在候選**、又**永遠排最前** → 挑它 → 失敗 → 標 failed → 仍是候選且仍排最前 → **無限迴圈,後面 500 家永遠輪不到**。改成**「時間決定」:依「最久沒嘗試」排序(沒試過的優先)**,並讓**每次嘗試(不論成敗)都更新最後嘗試時間**。**這個改法天然產生退避** —— 試過的自動排到隊尾,要輪完一圈才會再試,**不需要另外設計退避時間或失敗次數上限**。教訓:**排隊要用「多久沒輪到」排,不要用「狀態」排;用狀態排,壞掉的那個會霸佔隊頭。**

- **★ `done` 不再重抓 = 時間序列從架構上不可能成立(2026-07-31)。**
  舊邏輯裡 `done` 的店永遠退出候選 → **每家店一輩子只被抓一次** → 每個 feature 永遠只有一個觀測點。後果不只是「資料會過期」,而是 **Growth 類 insight 在架構上根本不可能算出來**(成長率需要同店兩個時間點),而我們卻在 Phase 2.5 花力氣實作了 `GrowthStatProducer`。**教訓:實作「需要歷史」的功能前,先確認採集層真的會產生歷史。** 「資料是流動的」不只是說資料會變,而是**採集本身必須是重複的**。

- **★ 測試寫進正式 DB 的連鎖後果,遠比「有點髒」嚴重(2026-07-31)。**
  測試沒有隔離,直接寫正式庫,造成五種互相糾纏的問題:(a) store entity **85% 是測試 fixture**(3498/4092);(b) **統計數字失真** —— 我一度回報「真實 store 有 2078 家」,實際只有 **594**(漏排除一個前綴就差 3.5 倍);(c) 測試批號 `2099-*` 污染正式觀測的 provenance;(d) **harvest 佇列被假網域塞爆並卡死**(隊頭那 3 家就是測試 fixture);(e) 測試偶發性失敗(跨次累積導致「單獨跑會過、全套跑會掛」)。**治本是獨立測試庫**(`conftest.py` 切換 `MES_DATABASE_URL` + 每 session 重建 schema),而且因為 `get_settings()` 本來就每次重讀環境變數,**測試檔一行都不用改**。附帶好處:測試從 6.9s 降到 2.0s(不再掃上萬筆垃圾),flakiness 從結構上消失。

- **★ 判斷錯 ≠ 事實錯 —— 分層讓修正成本限制在該修的那一層(2026-07-31,P2 第一次被真實驗證)。**
  `product_count=520` 是**中性事實**,不管未來怎麼定義「大店」都永遠成立;`SKU_SCALE=High SKU` 是**判斷**,門檻選 500 可能選偏。因為兩者分在不同層(knowledge vs insight),**門檻改了只要重跑 insight 投影,底下的觀測一筆都不用動**。若當初把「High SKU」直接寫進觀測層,今天要改門檻就得回頭重寫歷史 —— 而歷史是 Append-Only 的,改不了。**這是 P2「Knowledge 層保持中立」第一次不是教條而是省下真實成本。判準:會隨「我們怎麼想」而改變的東西,不准寫進事實層。**

- **★ 沒有下游行為,就不該設門檻(2026-07-31,Jeff 定案)。**
  GrowthStatProducer 原本要吐 `Growth` 標籤,改成**只記錄實際成長率數值**。理由:**門檻是一種判斷,判斷該由「後面要做什麼行為」決定**;現階段沒有任何下游行為,設門檻等於**憑空造判斷**,而且**壓縮是有損的** —— +19% 與 −50% 一旦被壓成同一類,差異就永遠不見了。判準:先問「**現在有誰要拿這個判斷去做什麼?**」沒有答案 → 不要壓,記原值。等 Phase 4 有真實行為時再切,切法還能改。

- **★ 通用抓法的否決:語義問題比覆蓋率更決定性(2026-07-31)。**
  探 schema.org `aggregateRating` 當「不管裝什麼 app 都通用」的抓法,11 家真實店只有 1 家有(覆蓋率 9%)。但**真正致命的不是覆蓋率,是語義**:那筆是 `@type: Product` 的**單一商品**評論數,不是全店總數 —— **就算覆蓋率 100% 也不能用**。因此刻意**不把它當 fallback**:寧可該欄位沒有值,也不要填一個語義錯誤的值(單品數偽裝成全店數,下游無從察覺)。**判準:評估一個資料來源,先問「它的語義是不是我要的」,再問「涵蓋多少」;語義不對,覆蓋率再高都是零。** 另註:其餘店家不是沒有評分,而是評分由 **JS 執行期注入**,靜態 HTML 只有空容器 —— 靜態抓取看不到 ≠ 頁面上沒有。

- **★ 自我驗算抓到的是「看起來正常」的靜默錯誤(2026-07-31)。**
  loox 星等分佈原本用**位置式解析**(「總數後面接的 5 個數字」),在大店上完全正確,**在小店上卻抓錯**(某店序列是 `7,7,7,0,0,0,0`,位置式算出 sum=14 ≠ total=7)。這個錯是靠「**分佈加總必須等於總數**」這道驗算自己暴露的 —— 沒有它就會寫入一份看起來正常、實則錯誤的分佈,而且下游無從察覺。修法是改用**結構式解析**(錨在自我標示星等的 `aria-label="N stars, M reviews"`)。**判準:當資料內部存在可交叉檢核的關係(加總、比例、上下界),就把它寫成驗算 —— 它抓的正是「不會報錯的錯」。**

- **★ 能力邊界要記 fetch_failed,不是 not_found(2026-07-31,三值語義再深化)。**
  偵測到店家裝了 yotpo,但 MES 目前只有 loox handler → 記 **`fetch_failed`**。記 `not_found` 會謊稱「**確認這家店沒有評論**」,但真相是「**店家有評論,只是我們讀不到**」。判準沿用既有:`not_found` 是對**世界**的宣稱(確認不存在),`fetch_failed` 是對**我們自己**的宣稱(沒能取得)。**「我們還沒實作」屬於後者。** 這與 `inferred_domain` 的 `not_found` 只敢宣稱「以現有能力搜不到」是同一條線。

- **★★ 視野限制被誤讀成「市場沒了」—— 根因不是數值,是觸頂沒有訊號(2026-07-31,最重要的一條)。**
  `MAX_PAGES = 12`,而實測各來源在**第 25 頁都還是滿的**(loox 第 40 頁仍有)。所以「五個 review app 都翻到底了」是**錯的** —— **池子從來沒乾,是我們自己設了 12 頁的視野邊界,然後把「視野內看完了」當成「市場沒有了」。** 這個誤讀**已經發生兩次**(第一次擴到 5 個 app、第二次擴到 10 個),兩次的真實原因可能都是深度限制,而我們兩次都去「加來源」。
  **真正的病是:翻到上限湊不滿時,系統只回報「撈到 2 家」,沒說「我是翻到上限才停的」** —— 於是「**我們的視野限制**」與「**市場真的沒了**」在回報上長得一模一樣。**不管上限設多少,只要觸頂是靜默的,同樣的誤判就會再發生。**
  修法(兩層):`MAX_PAGES=2000` **刻意設在正常永遠到不了處**,使「觸頂」成為**高信度異常訊號**(設 50/100 反而會常態觸及,訊號充滿雜訊、久了被忽略);真正的煞車是**單批 5 小時**時限。並讓停止原因進型別(`GatherOutcome.stop_reason`),`hit_cap`(我們的上限)與 `sources_exhausted`(市場真的沒了)**在型別上就分得開**,觸頂主動推 Telegram 並說明是哪一種。
  **通則:任何「因為我們設的限制而停止」的情況,都必須與「因為外界真的沒有了而停止」分開回報。這是「失敗不偽裝」在『邊界』上的版本 —— 前面幾條講的是不要偽裝失敗,這條是不要把「我們的限制」偽裝成「世界的事實」。**

- **`max_instances` 是 per-job 不是全域(2026-07-31)。**
  APScheduler 的 `max_instances=1` 只擋「**同一個 job** 疊自己」。baseline 三個時段是**三個獨立 job**(`harvest_slot_1/2/3`),所以擋不住 slot 3(21:00)還在跑時 slot 1(02:00)啟動 —— 而 21:00→02:00 剛好 5 小時,**與蒐集時間煞車等長**,只要有一批跑滿煞車就必然首尾相接。資料不會壞(batch_id 不同 + Append-Only),但**兩批同時打 DuckDuckGo 會讓速率翻倍**,正好打在最敏感處。修法:三個 slot 共用一把 `asyncio.Lock`,並選**「等待」而非「跳過」**(跳過會讓該批消失,而供給本就不足;等待有時間煞車為界,不會累積)。

- **App Store 的 selector 跨 app 類型通用(2026-07-31,好消息也要記)。**
  既有 selector 是在五個 **review** app 上驗證的,「其他類型的 app 是否同一套 HTML」未經實測。實測 6 個非 review 類(email 行銷 / 忠誠度 ×2 / 訂閱制 ×2 / 跨境多語言)**全部可解析**。所以擴充 Seed 來源的成本是「**加一個 handle**」,不是「每類寫一套解析」。**推論當初「那是 App Store 的 HTML,不是 app 專屬」是對的 —— 但這是驗證後才敢說的,先前只是合理猜測。**

- **handle 不能憑 app 名猜(再次印證,2026-07-31)。**
  這次擴充又踩到:klaviyo 的 handle 是 `klaviyo-email-marketing`;`gorgias` / `gorgias-live-chat-helpdesk` / `recharge` 憑名猜**全部 404**。做法不變:**先打候選 URL 看 200 + 能不能解析出店名,確認了才寫進常數**。

- **第三方伺服器的限流性質不同於 per-domain(2026-07-31)。**
  harvest 戳的是**每家店自己的伺服器**(per-domain、每家一次,總量不是問題);但 loox handler 第二步打的是 **`loox.io`** —— **所有 loox 店的請求都打向同一台**,性質接近 baseline 打同一個 DuckDuckGo。故對第三方的請求**獨立節流且更保守**(8–25 秒 vs harvest 的 3–12 秒)。**判準:節流的單位是「被打的那一台」,不是「我們在處理的那個對象」。**

- **來源標記大小寫不一致會把同一來源拆成兩個(2026-07-31,producer 問題的重演)。**
  `observed_on_app_store` 的 value_text 同時存在 `Loox Review Page`(120 筆)與 `loox review page`(82 筆),根因是 `ingest_seed` 一個**過時的預設值**(多來源之前為 loox 專用留下的)。這會讓未來「哪個來源的店家品質好」的分析把同一來源當成兩個。修法**不是叫呼叫端小心**,而是**在寫入端收斂到單一正規化函式**(`normalize_source_label()`,小寫 + 收斂空白),格式由函式保證。**與 producer 受控是同一條原則:識別性欄位的格式一致性,必須由寫入端強制,不能依賴呼叫端自律。**
