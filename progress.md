# MES — Progress Log(進度日誌)

> **用途:** 逐日/逐次進度日誌 —— 記「實際做了什麼、跑了什麼、結果如何」。
> **只記真實發生的事,沒發生的不要編。** 「code 改完 ≠ 驗過」——實作與驗收分開記。
> 最新的在最上面。

---

## 2026-08-03 — Phase 3:AI 第一次進場,循環跑通(生成 → 審核 → Decision Graph)

> 這天做完 Phase 3 三批,並**實際跑通一次完整循環**:LLM 產出真實假說 → Jeff 在網頁審核
> → decision 進 Decision Graph。**AI 第一次在 MES 裡做「會死的預測」。**

### 三批

1. **第一批(`a18abee`)資料層** —— `hypothesis` / `decision` 兩張表 + predicate registry + pattern → 撈店查詢。
   兩個文件沒明寫但屬其意圖的守門:`source_insight_refs` 與 `pattern` 的**空陣列也要擋**
   (NOT NULL 擋不住 `[]`,而空引用等同沒有 Provenance)。
2. **第二批-A(`9955f42`)生成** —— LLMProvider 抽象 + Pattern 聚合 + prompt 檔案版本化 + 手動觸發。
3. **第二批-B(`9955f42`)審核** —— 本機網頁,三動作 + Decision Graph 串鏈。

### 第一次真實生成的結果

```
claude-opus-5 · 3 pattern · 3 次呼叫 · 7421 tokens
寫入 6 條 pending · 擋下 3 條(全是 predicate 未登記)
```

輸入確實單薄(163 家店全只有 `SKU_SCALE` 一維),**未為此放寬聚合或補造 insight**。
LLM 沒把單薄證據講成 `certain`(每個 pattern 各 1 條 `inferred` + 1 條 `estimated`)。

### ★ 過程中修了兩個真實 bug —— 都是我的,不是 LLM 的

1. **模型 id 憑記憶寫成 `claude-opus-4-20250514` → 404。** 改成查 `client.models.list()`。
   連「模型名稱」這種看似記得住的東西也踩到 —— **「我記得」不是查證。**
2. **★ registry 靠 import 副作用 → 測試全過、生產全掛。** 9 條產出被擋下 6 條,
   錯誤是 `insight_type 'SKU_SCALE' 未登記(已登記:[])` —— 入口點從沒 import 到 producers。
   **測試會過是因為測試檔自己 import 了它。** 這是「掛上去 ≠ 會執行 ≠ 有產出 ≠ 跑的是新 code」
   家族的第四層:**測得過 ≠ 生產跑得起來**。

### 兩個判斷(Jeff 裁決)

- **predicate 補登記行為三態** —— `SWAP` / `ADOPT` / `NO_ADOPTION`。**這是被真實產出逼出來的**:
  registry 刻意只登記一個、不預先窮舉,結果 LLM 一再想表達 `SWAP` 涵蓋不了的意圖。
  **「先留空、讓真實需求填」的價值在第一次生成就兌現了。**
  拒收 `LOCALIZATION_APP_INTENT`:**「行為意圖」與「產品範疇」是兩個正交維度,不該壓成一個欄位**
  (否則值域隨 app 類別相乘爆炸)。
- **LLM 提出「pattern 該再切一刀」→ reject,但不擴充語法。** 現行 pattern 只支援等值 AND,
  表達不了 `avg_price < 35`。**保留為 Phase 5 演化語法時的真實 test case。**

### ★ 一件我刻意沒做的事

提示詞要求「實際用它審核那 6 條假說」,但 Jeff 只對 1 條給了明確裁決 →
**只執行那 1 條,其餘 5 條留白。** approve/reject 是商業判斷;Roadmap 停止條件明載
「AI 做 approve 以外的決策 → 停」;且 `decision.actor='jeff'`,代按等於把話塞進他嘴裡,
會**污染未來演化的素材**。已寫進 CLAUDE.md 成為常駐準則。

### 驗收:2/4 達成,2/4 無法驗證

| 條件 | 狀態 |
|---|---|
| 假說結構化、帶 evidence、引用 Insight、可審核 | ✅ |
| reject 進 Decision Graph | ✅ |
| 換模型各自產生假說 | ⚠️ 無法驗證(只有一個 provider)|
| 可分別評估模型觀察力與推理力 | ⚠️ 無法驗證 |

**Phase 3 刻意不標 ✅** —— 標了會讓人誤以為 P4(Model Agnostic)已驗證。
**「架構就緒」≠「驗收通過」**:抽象層已證明正確(MockProvider 跑完整流程、零 API 呼叫),
但「換掉模型系統照樣運作」**從未實際執行過一次**。Jeff 明確表示短期不辦第二組 key ——
故記為**已知缺口,非待辦事項**。

### 當日結束狀態

hypothesis:**5 pending / 1 rejected**;decision:1 筆(jeff / reject)。
測試 275 passed;ruff / mypy 綠。審核頁 `python -m mes.review`(只綁 127.0.0.1)。

---

## 2026-08-02 — misfire:框架預設值吃掉三個批次

**查證起點:** 8/1 21:00 那批 observed=0 且無心跳,但 daemon 今早才重啟過、應該跑新 code。

**判定:第 4 種(其他)—— 不是 Lock 死鎖、不是程序掛掉、不是心跳寫入失敗。**

`APScheduler.misfire_grace_time` **預設只有 1 秒**,事件迴圈卡超過 1 秒該次任務就被**整個丟棄**。err log 的三行 `was missed by ~2s` 與缺失的三個心跳**完全對上**(`next run at` 指的是被跳過那次的下一次):12:00 harvest、21:00 baseline、21:00 harvest。

排除的可能:PID `62381` 24 小時未變(沒掛過)、`pmset` 無任何 Sleep/Wake、err log 無 Traceback、Lock 用 `async with` 保證釋放且 8/2 02:00 正常跑完。

**更早的證據:err log 顯示 7/24 就有 `missed by 1.22s`** —— 這個系統一直在用「卡頓超過 1 秒就丟掉整批」的設定跑,只是沒人看 err log。

### 修復(三件)

1. **`MISFIRE_GRACE_SECONDS = 3600`** + 保留 `coalesce=True`。理由(Jeff):batch_id 依 slot 固定,晚跑的批次仍是「那一批」,補跑沒壞處;而批次消失是實實在在的損失。**寧可晚跑,不要不跑。**
2. **新增第四種診斷分支** —— 但做成**結構化訊號**而非解析 log:接 APScheduler 的 `EVENT_JOB_MISSED` 事件寫進 `job_run_log`(`status='missed'`)。診斷會說「被排程丟棄,**不是 daemon 死了也不是 code 有問題**,方向是調 misfire_grace_time」。**關鍵細節:`missed` 不算「有跑」**(`RAN_STATUSES` 只認 success/failed),否則會把「沒跑」的警鈴餵飽而弄瞎它。
3. **`code_version` 加 `-dirty`** —— 就在今天重啟時真實發生:daemon 跑著含未 commit 改動的 code,hash 卻標成乾淨的 `b7ea865`。改用 `git describe --always --dirty`。**版本標記的價值全在邊緣情況;完美狀態下人人都誠實。**

**daemon 已重啟兩次**(07:05 載入 misfire 修正;commit 後再一次讓 hash 對齊)。四個 job 已驗證 `misfire_grace=3600s`。

**未動:** 阻塞式 `time.sleep()` 在 asyncio 事件迴圈裡(5 處)—— 真地雷(baseline 一批阻塞 52 分鐘,若跑滿 5 小時煞車會吃掉期間所有 harvest 批次),但牽動較廣,另開一批。

測試 201 passed;ruff / mypy 綠。

---

## 2026-08-01 — 晨間查證:抓到第五種靜默失效(跑的不是最新 code)

> 昨晚警鈴報了四條。三條判定完畢,一條是真異常 —— 而查下去發現的東西,
> 讓昨天所有「已修復」的驗證都要打個折扣:**排程跑的一直是 16 天前的 code。**

### 查證結果

| 項目 | 結果 |
|---|---|
| **projection / insight 首次自動觸發** | ✅ **23:30:03 / 23:40:01**,時間戳精準,時區正確。產出 1834 列 / 35 筆 insight |
| **8/1 02:00 baseline** | 有跑,但撈到 **0 家**;無心跳、無 stop_reason、訊息仍是舊版 |
| **harvest 心跳停擺** | 屬「**有跑但沒寫心跳**」—— 每 3 小時準時跑,只是舊 code 沒有心跳功能 |

### ★ 根因:一個原因解釋全部

**`com.mes.harvest` 是 `KeepAlive=true` 的常駐 daemon,啟動於 7/15 22:04,跑了 16 天,從未載入任何 code 變更。**

決定性證據 —— 8/1 06:00 那批:**3 店 × 9 feature = 27 筆、零評論 feature**(新 code 應為 15 店 × 12 feature ≈ 180 筆)。批量、feature 數、心跳缺席,三項全是舊 code 的指紋。

**為什麼只有它中招:** 一次性 job(projection / insight / alarm)每次由 launchd **重新 spawn**,必然載入最新 code;**常駐 job 不會**。這正是為何 projection / insight 前一天才掛上、當晚就精準運作,而 harvest 掛了 16 天卻在跑舊邏輯。

**這是「掛上去 ≠ 會執行 ≠ 有產出」的第四層:動的是哪一版。** 而且它**完全沒有訊號** —— log 照常、批次照常、觀測照常寫入,看起來一切正常。

**已重啟**(PID 62381,06:55),並確認磁碟 code 為新版。⚠️ **完整驗證待 09:00 / 10:00 的排程觸發**(此刻只證明了它載入的是新 code,尚未證明產出形狀正確)。

### 修正

1. **baseline 診斷改讀心跳**(commit `a4b9ece`)—— 7/31 那則「daemon 沒跑」是誤診,真相是「有跑但撈到 0」。兩者在 observation_log 上長得一樣,而心跳早就做好了,只是既有診斷還沒改讀它。**加了新的資訊源,不等於既有判斷會自動變聰明。**
2. **心跳帶 `code_version`** —— 把「跑的是哪版 code」從需要推理的事變成看得見的事。⚠️ **關鍵細節:必須在 import 時求值**,若執行時才問 `git rev-parse`,跑舊 code 的 daemon 會回報磁碟上的新 hash,**反而掩蓋問題**。
3. **每日安好會標示** `⚠️ 跑的是舊 code(xxx,目前 yyy)—— 常駐 daemon 需重啟`。

測試 195 passed;ruff / mypy 綠。

---

## 2026-07-31 — Phase 2.5 完成 → 揭開四條鏈路裡三條靜默失效 → 五步修復 + 採集擴充

> 這天的形狀:**做完 Phase 2.5,它暴露了一個缺口;順著缺口查下去,發現系統遠比想像中壞。**
> 而且是「看起來一切正常」的壞法 —— 警鈴每天回報正常,因為它看的正好是唯一正常的那條鏈路。

### 1. Phase 2.5 Insight Engine 完成(commit `7cb16af`)

- **第一批(資料層):** `insight_store` 表(`(entity_id, insight_type)` UNIQUE → **insight_id 穩定不重生成**,Phase 3 的 Hypothesis 才引用得住)+ 應用層受控 registry。
- **第二批(核心):** `InsightEngine` + `BaseInsightProducer` + `SKURuleProducer`(≤100/101–500/>500 三級)+ `GrowthStatProducer` + 23:40 排程 + `insight_run_log`(記「為什麼沒產出」)。
- **Producer 純函數但需歷史 → 由 Engine 統一撈:** Producer 只「聲明」需要什麼,Engine 打包成記憶體 Context 交給它,Producer 碰不到 DB。
- 補上第一批漏的 **producer 應用層受控**(它是 Provider 競技場的計分主體,寫法不一致計分板就壞了)。
- **兩個定案的理由(不是實作細節,是判準):**
  - `generated_at` 用 `now()` —— 判準是「**這個時間在描述什麼**」:knowledge 的 `observed_at` 描述「事實何時被觀測」(歷史事實,禁用系統時間);insight 的 `generated_at` 描述「描述何時被產生」(本來就是執行時間)。**不是無差別禁令。**
  - 受控放 DB 還是應用層 —— 用「**穩不穩定**」決定:confidence 三級(Phase 0 既定)→ DB CHECK;insight 標籤(演化中)→ 應用層 registry。**同一張表刻意兩種待遇。**

### 2. 缺口 → 一路挖出系統性失效(commit `e0f5b74` 先標記缺口)

- `GROWTH_VELOCITY` 對真實資料產出 0 —— 但原因**不是「還沒累積夠 30 天」,是 `review_count` 根本不在 Feature Taxonomy 的 9 個 feature 內**(源頭沒開,永遠不會有)。先把這個缺口標進文件,**不讓它被 Phase 2.5 的 ✅ 蓋住**。
- 查 review_count 的樣本 → 發現**只有 2 家真實店有 uses_review_app**,再查 → **594 家真實 store 只 harvest 過 6 家**。
- 診斷(區分「沒觸發 / 有觸發沒挑到 / 挑到了但失敗」三種):**是第三種的變形** —— daemon 有跑(125 批、16 天),但**每批都挑同樣 3 家假網域**(head-of-line blocking)。
- 同時發現 **`projection` / `insight` 兩個 daemon 從未被 `launchctl load`** —— plist 寫好放在 repo 裡,但沒掛上去。
- **四條鏈路裡三條沒在做該做的事,而警鈴恰好只監測了正常的那條(baseline)。**

### 3. 五步修復(commit `e7ef1c3`)

1. **load 兩個 daemon** —— 並用 `launchctl kickstart` 驗證整條路徑(程序真起來、log 真寫入、DB 真多列),不是只確認「已 load」。
2. **測試改用獨立 DB**(`mes_test`,每 session 重建 schema)—— 測試檔一行未改(因為 `get_settings()` 本來就每次重讀環境變數)。硬指標:跑完 143 個測試,**正式 DB 計數完全不變**。
3. **清理正式 DB 的測試資料** —— 5535 個 entity + 8781 筆觀測(先 `pg_dump` 備份;Append-Only trigger 交易內停用後**立即恢復並實測仍在擋**)。store entity 4092 → 592。
4. **修 harvest** —— 排序改「最久沒嘗試優先」(天然退避)、`done` 納入候選(**這才讓時間序列成立**)、最小重抓間隔 7 天、批量 3→15(限流是 per-domain,與 baseline 性質不同)。
5. **心跳 + 警鈴擴充** —— `job_run_log`(產出為 0 也記)+ 三個新警鈴(沒跑 / 報錯 / 產出異常為 0,且**排除正常閒置**)+ 每日安好加四條鏈路狀態。

**★ 整條鏈路首次真正打通:** 真實店 → 12 個 feature → Knowledge → Insight,不靠測試資料、不靠手動觸發。首批 12 筆 SKU_SCALE 來自真實市場資料。

### 4. 採集能力擴充:review_count / avg_rating / rating_distribution(同 commit `e7ef1c3`)

- **先探通用抓法** —— schema.org `aggregateRating`,11 家真實店實測:覆蓋率 1/11,**且那筆是單一商品的評論數、不是全店** → **不可行,且刻意不當 fallback**(寧可沒有,也不要語義錯誤的資料)。
- 改**通用入口 + per-app handler 可插拔**,目前只有 loox handler(其餘四個 app 樣本仍不足,不憑想像寫)。
- **實測打臉三個假設:** widget id 有**第三種** markup(JSON 跳脫斜線)、widget 頁**可見文字會在地化**(西班牙店顯示 `Reseñas`,寫死英文會漏掉所有非英文店)、位置式解析在小店會抓錯(改結構式)。**其中第三個是靠「分佈加總 = 總數」的驗算自己暴露的。**
- Feature Taxonomy **v1 → v2**;`source` 新增 `review_widget`(評論數來自第三方 widget 頁,不是店家 `html_page` —— 借舊值 = 在來源追溯上說謊);`producer` **不新增**(管道由 source 承載、哪個 app 由 `uses_review_app` 承載)。

### 5. Seed 供給的三個病(commit `bf56b75` + `2a4cfb4`)

- **供給** —— 加 5 個「有規模才會裝」的非 review 類來源(klaviyo / smile / loyaltylion / seal_subscriptions / weglot)。實跑 **30/30 恢復**(原本 4/0/0/2),**總請求數反而從約 60 降到 8**(round-robin 更容易在 page 1 湊滿即停)。
- **★ 視野** —— `MAX_PAGES = 12`,但實測各來源**第 25 頁都還是滿的**(loox 第 40 頁仍有)→ **池子從來沒乾,是我們自己設了視野邊界**,並把「視野內看完了」誤讀成「市場沒有了」。**這個誤讀已發生兩次。** 根因不是數值而是**觸頂沒有訊號**:改 `MAX_PAGES=2000`(刻意設在正常到不了處 → 觸頂即高信度異常訊號)+ 單批 5 小時煞車 + `GatherOutcome.stop_reason` 讓「我們的上限」與「市場真的沒了」**在型別上分開** + 觸頂主動推 Telegram。
- **併發** —— `max_instances` 是 **per-job**,三個 slot 是三個 job,擋不住 21:00 那批還在跑時 02:00 啟動(而兩者間隔剛好 5 小時 = 時間煞車長度)。加跨 slot `asyncio.Lock`,選「等待」而非「跳過」。
- 順帶:`observed_on_app_store` 來源標記大小寫統一(120+82 → **202**),並在寫入端加 `normalize_source_label()` 單一入口(不依賴呼叫端自律)。

### 當日結束時的真實數字

| 項目 | 值 |
|---|---|
| store entity(全為真實) | 592 |
| seed entity | 754 |
| observation_log | 1961 |
| harvest 過的 store | 44(修復前 6) |
| review_count 觀測 | 6 家(7 ~ 1590 則) |
| knowledge_state | 1598 |
| insight_store | 12(全來自真實店) |
| 測試 | 189 passed(全程 ruff / mypy 綠) |

### 未竟事項(誠實記錄)

- **`GROWTH_VELOCITY` 仍產出 0**,但性質已改變:資料源已開,缺的是**第二個時間點**(需同店相隔約 30 天,最小重抓間隔 7 天 → 約一個月後)。**這次是真的「還沒累積夠」,會自己好。**
- judgeme / okendo / stamped 的 review handler **未實作**(樣本仍為 0,不憑想像寫);yotpo 僅 1 家樣本。
- 觸頂訊號**尚未納入警鈴規則**(已即時推 Telegram;等有真實觸發案例再定,同門檻校準邏輯)。

---

## 2026-07-17 — 警鈴(主動回報 + 原因診斷):系統從「哑巴」變「會叫痛」

- **Telegram 基礎設施:MES 原本零接**（整個獨立專案無任何 telegram/bot/notify 痕跡,只有 schedule.py 一句「no auto-alert」註解）。本次從零接:`src/mes/notify.py`(Telegram Bot API sendMessage)+ config 加 `MES_TELEGRAM_BOT_TOKEN`/`MES_TELEGRAM_CHAT_ID`(選填,缺則 no-op、只記 DB)。**⚠️ 實際送達待 Jeff 提供 bot token + chat_id。**
- **定位鐵律:只主動回報 + 初步診斷,不做任何自動調整/退避/加來源。** 判斷怎麼調由 Jeff 決定;現在先把「異常+原因」記下來當未來自動化的燃料。
- **`src/mes/alarm.py`**:每天 23:50 台灣獨立跑,讀當天三批(-01/-02/-03)巡檢三警鈴:(1) 連續兩批新 Seed <10;(2) 連續兩批 fetch_failed >15;(3) 任一批 observed=0(單批即觸發)。門檻暫定,待實況調。
- **原因診斷(核心):** 用既有資料判讀最可能原因跟警報一起推。0-observed 分辨 fetch_failed 佔滿(限流)/ not_found 佔滿(市場搜不到)/ 無新 Seed(池子乾)/ 批次無記錄(執行異常)—— 對應完全不同的調整方向。
- **結構化記錄:** 新增 `alert_log` 表(alert_id / fired_at / taiwan_date / alert_type / diagnosis / detail JSONB / delivered)。`alert_type` **不 CHECK 鎖**(利未來擴充新異常類型);`detail` 存當天三批數據 + 門檻。只記錄+推播,不自動調整。
- **獨立掛法(關鍵):** launchd `com.mes.alarm`,StartCalendarInterval 23:50(本地=台灣),RunAtLoad=false 一次性。**刻意與 harvest daemon 分離** —— 若那個 daemon 死了,警鈴(獨立程序)才能照跑並報「批次執行異常」;綁在一起的話 daemon 一死警鈴也啞,正好在最需要時失聲。
- **推播策略:** 只在有異常時推、正常安靜(不推「一切正常」);一天多警鈴合併成一則不洗版。
- **實測:** `pytest` **80 passed**(+10 alarm)。真實資料:7/15、7/16 健康日正確安靜、三批讀取準確;四個異常情境的訊息+診斷已驗(含多警鈴合併)。已 `launchctl load`(排程中,無 PID = 等 23:50 觸發)。
- **改的檔:** 新增 alarm.py / notify.py / alert_log migration / test_alarm.py / deploy/com.mes.alarm.plist;改 config.py / .env.example / models.py(AlertLog)/ db/__init__.py。**核心鏈路未動。未 commit。**

## 2026-07-15(深夜)— Phase 1-D:戳店面抓 9 個市場 feature(獨立排程)

- **架構:與 baseline DDG 鏈路分離、獨立。** 新增 `src/mes/harvest.py`:讀「有 domain、待抓」的 store → 戳該店 products.json + 首頁 HTML → 寫 9 feature(掛 store entity)。戳的是各店伺服器/Cloudflare,與 DDG 不同對象、限流獨立、兩鏈路並行不干擾。獨立排程 `store_harvest`(每 3h,≈8 批/日、每批 1–3 家)。
- **狀態標記放哪(選獨立表,理由):** 新增 `store_harvest_state(entity_id PK, status, updated_at)`,status ∈ pending/done/failed。**選獨立表而非 entity 加欄** —— 保持 entity 只作「觀測掛載點 + 去重鍵」的純淨(P2:entity 不存會變的事實);處理狀態是「系統辨識用、可自由 UPDATE」的東西,與 Append-Only/觀測資料本質不同,放它自己的表最乾淨。排程用 LEFT JOIN 挑「無 state 或 pending/failed」的 store,抓完 upsert done(連不上 failed 可重試)。
- **9 feature 來源:** products.json(product_count/avg_price/price_range/is_active)+ 首頁 Shopify.* 變數(theme_name/country/language/**currency** —— currency 在 HTML 不在 products.json)+ script 特徵(uses_review_app)。producer=`mes_store_crawler_v1`(擴充 CHECK)。
- **三值 + confidence 誠實:** 逐 feature 走三值(連不上=fetch_failed / 確認沒有=not_found / 拿到=observed;is_active=false 是 observed 負向觀測非失敗)。confidence:直讀=certain,`uses_review_app`=**inferred**(script 特徵是推斷,可能誤判),products 翻頁不全=estimated。
- **首次實跑真實 3 家:** flated.co.nz **9/9**(NZ/NZD)、vaniabath.com **9/9**(196 商品、loox)、centricoffee.com **8/9**(uses_review_app 誠實 not_found)。products.json / Shopify.* 結構如探測預期,未被擋。
- **踩坑(重要):** SQLAlchemy JSONB 欄預設把 Python `None` 存成 **JSON `null`(非 SQL NULL)**,害 `value_json IS NULL` 為 false、打破 value_typed CHECK。修:兩個 value_json 欄加 `JSONB(none_as_null=True)`。(先前測試沒明寫 value_json=None 才沒踩到。)
- **測試:** `pytest` **70 passed**(+11 harvest);ruff/mypy 綠;migration 已套用(store_harvest_state + producer CHECK)。daemon 已重載跑 4 jobs(3 baseline + store_harvest)。
- **核心(雙骨牌/三值/producer/Append-Only/batch_id/discriminated union)未動。未 commit。**

## 2026-07-15(晚)— scraper 擴到五個 review app(解 Loox 供給見底)

- **動機:** 10:00 批(2026-07-15-02)只湊到 **11/30** —— Loox 種子供給在 `MAX_PAGES=12`(約 120 名)範圍內第二天就抽乾(健康報告誠實標「供給不足」;Seed 去重生效、未重複撈同店湊數;DDG 這批 100% 沒事)。
- **做法:** scraper 從只抓 loox 擴到**五個 review app**。先實測真實 App Store handle(非都等於 app 名):`loox` / `judgeme` / `yotpo-social-reviews` / `okendo-reviews` / `product-reviews-addon`(stamped 的歷史 slug)。selector 各 app **通用**(是 App Store 的 HTML,非 app 專屬)。
- **`_gather_new_store_names` 改跨 app round-robin(by page)**:page 1 各 app 輪一遍 → 分散負載、最快找到新供給;回傳 `(store_name, app_key)`,`observed_on_app_store` 的 value_text 記來源 app。
- **驗證(只跑蒐集、不碰 DDG、不寫入):** 一次湊到 **30 個新 Store Name**(judgeme 10 + yotpo 10 + okendo 10,page 1 就夠;**loox 貢獻 0 = 確認已抽乾**)。供給問題解決。
- **改的檔:** `scrape.py`(`REVIEW_APP_HANDLES` + 標題)、`pipeline.py`(跨 app 蒐集 + 帶 app 來源 + 供給不足訊息改「五個 review app」)、`test_phase1_harvest.py`(+1 handle sanity 測試)、task_plan/progress/findings。核心(雙骨牌/三值/producer/Append-Only/CHECK)未動。
- **測試:** `pytest` 59 passed;ruff/mypy 綠。daemon 已重載跑新 code —— 今晚 21:00(-03)起能拿滿 30。

## 2026-07-15(早)— 首個乾淨排程批(02:00)+ 測試批號 sentinel 化 + 污染標記

- **測試批號改 sentinel:** 測試 fixture 原硬編 `2026-07-15-01` 等真實日期批號,pytest 一跑就往 dev DB 寫,**正好撞上真實排程 02:00 槽位**。改全部測試批號為 `2099-*`(sentinel 年份,永不撞真實 20xx 排程)。(test_phase1b `_obs` / raw INSERT、test_phase1c `_BATCH`、test_phase1_harvest `_BATCH`。)
- **標記已污染資料(Jeff:標記即可、不刪):** `2026-07-15-01` 內混了 **81 筆**測試殘留(全在 13:00 UTC;真實批全在 18:00 UTC)。一次性 dev 維護:暫 `DISABLE TRIGGER observation_log_no_update` → 把那 81 筆 batch_id 改標為 `2099-01-01-01`(隔離)→ `ENABLE`(已復原)。**技術點:** `docker exec` 要加 `-i` 才會把 heredoc SQL 餵進 psql(第一次沒加 → UPDATE 沒跑)。標記後 `2026-07-15-01` 只剩真實 30 seed + 30 inferred。
- **首個乾淨排程批 2026-07-15-01(02:00 台灣):** 30 筆 · **observed 29(97%)/ not_found 0 / fetch_failed 1(3%)** —— 生產 20–150s 間隔下 02:00 時段 DDG 基本沒事(僅 1 筆瞬時 fetch_failed)。
- **可用比例(眼看評估,非人工核實):** 29 個 observed domain 中,**可用(疑似真店家官網)19、不可用 10**(shop.app×3、duckduckgo.com、shopify.dev、ecomscout/retailbrew/storeverify/studocu/webinopoly)。→ **可用 ≈ 19/30 = 63%(佔全批)、19/29 = 66%(佔 observed)**。這再次印證 observed(限流健康)≠ 可用(domain 對不對);可用性判斷屬未來上層元特徵評估,不在抓取層做。
- **測試:** `pytest` 58 passed;ruff/mypy 綠。核心未動。**測試檔改動待 commit;污染標記是 dev DB 資料維護(不進版控)。**

## 2026-07-14(深夜)— 批號改固定槽位語義(-01/-02/-03 = 三排程時段,-04+ = 手動)

- **原「數今天已有幾批 +1」改為固定槽位:** scheduler 拆成三個 job,各帶固定 `slot`(02:00→1、10:00→2、21:00→3),`run_daily_batch(slot=...)` → 批號 `台灣日期-0{slot}`。**手動 `--once`(slot=None)從 -04 起編**(保留 1~3 給排程時段)。**同時段重跑沿用同批號**(append 進該時段當天的桶)。
- **動機:** 舊法被測試/手動灌水(今晚 21:00 排程批被編成 `-04`)。固定槽位下,看批號就知道時段,直接支援「比較同日各時段 fetch_failed」的判讀,且免疫測試污染。
- **決策(Jeff):** 重跑維持同批號;手動從 04 往下編 →「123 固定三時段,4+ 手動」。
- **改的檔:** `schedule.py`(三 job + SCHEDULED_SLOTS)、`pipeline.py`(`_resolve_batch_id` + `run_daily_batch(slot)`)、`test_phase1_harvest.py`(+2 測試)、docs/task_plan/findings。核心未動。
- **今晚 21:00 排程批(`2026-07-14-04`,舊 code 跑的)完美收尾:30 筆全 observed、0 not_found、0 fetch_failed** —— 生產 20–150s 間隔下,21:00 時段 DDG **零限流**。(此批在改碼前跑,故仍是 -04;歷史資料 Append-Only 不動;明起 21:00 批將固定為 -03。)
- **測試:** `pytest` **58 passed**(+2:排程槽位固定、手動從 04 起);ruff/mypy 綠。daemon 已重載跑三 job 新 code,印 `02:00->-01, 10:00->-02, 21:00->-03`。
- **未 commit。**

## 2026-07-14(晚)— 改一天三批 + 新增 batch_id 欄 + 既有資料回填

- **改一天三批:** `schedule.py` 由單批改 `CronTrigger(hour="2,10,21", timezone="Asia/Taipei")` → **台灣 02:00 / 10:00 / 21:00** 各跑一批(分散 8h/11h/5h,測「一天總量 90 次 DDG」而非短時爆量)。已驗證下三次觸發 = 台灣 21:00 → 隔日 02:00 → 10:00。daemon 已重載跑新 code(新 PID;今晚 21:00 台灣自然跑第一個三批之一)。**未硬跑整批**(DDG 狀態未知)。
- **新增 `batch_id` 欄(observation_log,NOT NULL + 格式 CHECK):** 格式 `YYYY-MM-DD-NN`(台灣日期 + 當天批序,例 `2026-07-15-01`)。批號由 `run_daily_batch` 依「台灣日期 + DB 既有同日批數 +1」自動產生(self-contained,重啟/--once/三批各自拿對序號)。**只加 observation_log,不加 knowledge_state**(當前值可能混不同批,批號語義不清)。批號是 Provenance 延伸(producer/source/crawler_version 之外再加「哪一批」)。
- **既有資料保留 + 回填(不清除):** migration `2e13ecff13c6` 加欄後,依每列 `observed_at`(台灣日期 + >10min 間隔分群)回填 batch_id。**技術點:** observation_log 有 Append-Only trigger 擋 UPDATE → 在 migration 內 `DISABLE TRIGGER observation_log_no_update` 回填後 `ENABLE`,不走應用層 UPDATE。回填結果:8 個批號,那批乾淨的 30 筆(02:00 台灣 07-14)正確歸 `2026-07-14-01`;0 筆 NULL;trigger 已復原(both enabled)。
- **健康報告改按批號:** `HealthReport` 以 `batch_id` 為鍵,`compute_health_for_date` → `compute_health_for_batch`;報告標「批號」+ 三比例分開 + 提示「比較同日越晚的批 fetch_failed 是否越高」(判斷一天總量累積限流)。
- **改的檔:** `schedule.py` / `pipeline.py` / `ingest.py`(三函式加 `batch_id`)/ `models.py`(batch_id 欄+CHECK)/ 新 migration / 三個測試檔 / `docs/MES_Observation_Schema_v1.md` / task_plan / findings。核心(scrape/infer/雙骨牌/三值/producer/Append-Only/value CHECK)未動。
- **測試:** `pytest` **56 passed**(+8:batch_id 寫入/NULL 拒/格式 CHECK 拒/按批號報告);ruff/mypy 綠;migration down→up 回滾通過。
- **暫定值提醒:** batch size=30 / 間隔 20–150 / 一天三批,皆為待真實負載修正的暫定值,非安全基準。
- **未 commit。**

## 2026-07-14 — 第一批生產批(排程自動跑)+ timezone 修正 + 黑名單決策

- **第一批自動跑了,結果乾淨:** 30 筆 · observed **29(97%)** · not_found **1(3%)** · fetch_failed **0(0%)**。DDG 冷卻後,20–150s 隨機間隔跑 30 筆**零限流** → 生產間隔可行。唯一 not_found:`Rocky Road Designs`。
- **但跑錯時間(已修):** 這批實際在 **18:00 UTC 07-13 = 02:00 台灣 07-14** 跑,不是意圖的 10:00 台灣。根因:預建的 `CronTrigger` 不繼承 scheduler 的 `timezone="UTC"`,抓了系統本地時區 Asia/Taipei,使 `hour=2` 變「02:00 台灣」。**修法:** trigger 明確帶 `timezone="Asia/Taipei"` + `hour=10`;已重載 daemon(新 PID),驗證下次觸發 = **2026-07-15 10:00 台灣(02:00 UTC)**。(詳見 findings 的 CronTrigger 時區教訓。)
- **撈資料揭露的真相:observed ≠ 抓對。** 把 29 筆 domain 攤開,~13 筆抓錯(`shop.app`、`techtic.com`、`hulkapps.com`、`n8n.io`、`marketwatch.com` 等),真實命中率約 5 成。健康報告的 observed 量的是「限流/系統健康」,不是「domain 精確度」;且該批 confidence 全為 `inferred`(誠實標記猜的)。非報告說謊,是兩個維度。
- **黑名單:不採用(Jeff 定案)。** 加黑名單濾掉 `shop.app` 等 = 入口丟棄 = 在觀測層做判斷,違反「抓取不判斷」/ P2 中立。抓取層照實記錄推論到什麼,可信度裁決留給未來上層(Insight/Hypothesis),且待**累積夠錯誤 pattern** 後再設計,不預先拍腦袋。(既有 `_BLACKLIST` 是否退場一併留待未來重審。)
- **改的檔:** `src/mes/schedule.py`(timezone 明確化)、`findings.md`(3 條)、`progress.md`。**未 commit。**

## 2026-07-13 — Phase 1 每日撈取排程 + 撈取健康報告(通往 7 天驗收)

- **建的檔(核心鏈路未動,只在外面包一層):** `src/mes/pipeline.py`(`run_daily_batch` 批次執行 + `HealthReport` 三比例報告 + `compute_health_for_date` 隔天回看)、`src/mes/schedule.py`(APScheduler daemon,cron 09:00 UTC,`--once` 手動觸發)、`tests/test_phase1_harvest.py`(5 測試)。改:`pyproject.toml`(+apscheduler、mypy 忽略其 stubs)、`.gitignore`(+`logs/`)。
- **排程:** 每天一批 = 30 筆 Seed;每筆之間 **20–150 秒隨機** sleep(隨機為硬性要求;跨度 130 秒刻意拉寬,更不規則、更像人類;30×~85s ≈ 42 分鐘/批)。第一版**不做**自動告警/退避——先累積一週經驗再說。
- **健康報告(誠實三比例,不合併):** observed / not_found / fetch_failed **分開**呈現 + 各自百分比;判讀說明點名 **fetch_failed 是「該不該調整節奏」主儀表**,not_found 高只代表這批死店多、非系統問題。印出 + append 到 `logs/harvest_health.log`。
- **Seed 去重仍生效:** 只取未撈過的新 Store Name,不重複撈同店湊數;供給不足以 `actual < requested` 如實呈現。
- **首次實跑真實觀察(縮小驗證,間隔刻意縮短以快速跑完整條線,非生產 20–150s):**
  - 第一批 8 筆(間隔 2–5s):observed **2(25%)** / not_found **0** / fetch_failed **6(75%)**。
  - 第二批 6 筆(間隔 5–15s):**fetch_failed 6(100%)**。
  - **DuckDuckGo 限流實況:** 今天連續兩次壓縮間隔猛打 → DDG 已對本機**硬限流**(第二批 100% fetch_failed)。這正是健康報告要暴露的訊號,報告誠實顯示(**未**美化成「成功率」把限流藏起來);雙骨牌仍正確寫入(fetch_failed 全欄 NULL、過 CHECK)。強力印證:**壓縮間隔會把井打壞、20–150 秒寬隨機保守起點的必要**。真實三比例要在真實節奏 + DDG 冷卻後靠一週資料才看得準(對照上一輪首次在較寬有效間隔下曾 5/5 命中)。
  - **Loox 供給:** 各批順利湊到新 Seed,無短缺;30×7=210 的完整週供給**未驗**(取決於評論頁翻頁深度;`MAX_PAGES=12` 上限約 120 名/批)。
  - 排程接線驗證:`build_scheduler()` 產生 `daily_harvest` job(cron 09:00 UTC)。
- **測試:** `pytest` **48 passed**(43 + 5 harvest);ruff / mypy 綠。既有雙骨牌/三值/CHECK 測試全過,核心未被弄壞。
- **Phase 1 驗收狀態:** 維持 **⬜ 未驗收**——排程機制就緒,但「連續 7 天實跑」尚未開始。啟動 daemon 跑出第一批日報後才轉 🔄 驗收中。

### 排程時間 + daemon 上線(2026-07-13 晚)

- **排程時間改為 02:00 UTC = 台灣 10:00**(`schedule.py` `HARVEST_HOUR=2`)。已驗證下次觸發 = **2026-07-14 02:00 UTC / 台灣 10:00**。
- **daemon 掛法:macOS launchd LaunchAgent**(`deploy/com.mes.harvest.plist`,已裝到 `~/Library/LaunchAgents/`)。`RunAtLoad`+`KeepAlive` → 常駐、崩潰自動重啟、重新登入後存活。跑 `.venv/bin/python -m mes.schedule`,`WorkingDirectory` = 專案根(讓 `.env` / `logs/` 解析正確)。已 `launchctl load`,PID 常駐、stdout 印出排程訊息、**未立即跑批**(等冷卻後明早自然跑,DDG 今天壓縮實測後正限流)。
- **明早第一批用生產間隔 20–150s 隨機**(非今天測試的壓縮間隔)。
- **管理指令(記給日後):**
  - 狀態:`launchctl list | grep com.mes.harvest`
  - 啟動:`launchctl load ~/Library/LaunchAgents/com.mes.harvest.plist`
  - 停止:`launchctl unload ~/Library/LaunchAgents/com.mes.harvest.plist`
  - daemon 日誌:`logs/harvest_daemon.out.log` / `.err.log`;每批健康報告:`logs/harvest_health.log`
  - 手動跑一批(冷卻後才用):`uv run python -m mes.schedule --once`
- **操作依賴(注意):** 批次寫入需 PostgreSQL 在跑(`docker compose up -d`)。compose 未設 restart 政策,Mac 重開機後需手動起 DB;若 02:00 UTC 時 DB 沒起,該批會失敗(錯誤進 `harvest_daemon.err.log`)。此為已知待辦,非本次範圍。
- **本批一起 commit(排程 + pipeline + 健康報告 + 時間 + daemon plist)。**

## 2026-07-12 — 口徑校正:六大原則由「憲法/教條」改為「可演化系統原則」+ 立三層口徑

- **純措辭/框架校正,不改任何 code、不改任何實際約束行為。未 commit(待 Jeff 核對)。**
- grep 全專案的「憲法/不可違背/鐵律/教條/神聖/永遠…」等措辭,逐一分類 A(可演化系統原則,軟化)vs B(資料層硬約束,保留強硬)。
- **改的(A,共 2 處,皆在 Roadmap):** `## 五原則(憲法)` → `系統原則/工程基線(可演化,非教條)` + 加口徑定性段;Phase 0 產出表「Principles … 憲法」→「系統原則/工程基線(可演化,非教條)」。六原則 P1~P6 的**內容不動**,只改「教條框架」外殼。
- **保留強硬的(B,未動):** 所有 Append-Only「鐵律/絕不 update」、Provenance NOT NULL、失敗三值不偽裝、value 欄 CHECK、Knowledge_State「不可動搖/唯一真相/無後門」、value_raw 邊界——這些是「破壞就資料腐敗」的本版硬約束,不因口徑調整鬆動。
- **CLAUDE.md 新增「A+. 三層口徑」一節:** 第一層終極目的(實質行銷效益)/ 第二層紅線防護(唯一絕對底線=不違法、不惡意詐欺)/ 第三層系統原則(可演化);明寫判斷順序(先法律紅線 → 再實質效益 → 系統原則被這兩者檢驗)+ 「系統原則 ≠ 資料層硬約束」的區分。
- 未發現「A/B 混在同一句」需拆分的情況;無 code / 約束變動。

## 2026-07-11 — 文件一致性補齊 + 驗收獨立成三態子區塊

- **一致性檢查(只補漏記/不一致,不新增設計):**
  - `MES_Observation_Schema_v1.md` §2 欄位區塊漏列 `producer` → 補上(§7 早有定義、code 為 NOT NULL)。
  - `MES_Entity_Model_v1.md` §2 清單 + §3 schema 未列 `store_name_seed` → 補上(Phase 1-C 已用)。
  - `MES_Entity_Model_v1.md` §4 未記 store_name_seed 的 canonical_key 正規化(`seed:` 前綴)→ 補上。
  - `findings.md`:downgrade 教訓補「受控清單近乎只能往前加、難往後收,新增受控值要更慎重」引申;fetch_failed/not_found 教訓補「真實 DDG 限流下三值誠實運作」的 live 驗證。
  - code ↔ schema 其餘(discriminated union 六欄、source 六值、crawler_version 語義)核對**一致**,無需再改。
- **驗收(Acceptance)獨立成三態子區塊(做法 A):** task_plan 每個 Phase(0/1/2/2.5/3/3.5/4/5)把原埋在內文的「驗收標準」抽成獨立 `### ✅ 驗收(Acceptance)` 子區塊,含四態狀態(⬜未驗收 / 🔄驗收中 / ✅通過 / ❌未通過)+ 可勾選驗收條件 + 停止條件;頂部加三態定義說明。工作項目 checklist 保留不動。
  - **Phase 0 → ✅ 通過**(五份 schema 定稿並落成 code)。
  - **Phase 1 → ⬜ 未驗收(誠實標註):** A/B/C 核心工作已完成、43 測試過,但驗收條件「連續 7 天自動跑 + 規模累積(目標~1000家)」尚未做,本次僅偵察 live run 5 家。**未因 checklist 勾滿而謊報通過。**
  - **Phase 2/2.5/3/4/5 → ⬜ 未驗收**(尚未開始);**Phase 3.5 → 🔄 驗收中**(Reddit 養成進行中)。
- 驗證:`pytest` 43 passed、ruff / mypy 綠(無 code 變動,僅文件)。
- 本次連同 B/1-C 全部進度一起 commit。

## 2026-07-11 — schema 細化:source 加 web_search + 新增 producer 欄 + crawler_version 歸位

- **兩個已定案細化(版號不動,物理落地 DB CheckConstraint),趁 DB 近空表一次改乾淨。**
- **決定一:source 加 `web_search`。** inferred_domain 原暫記 `html_page` = 在 Provenance 管道欄說謊。改法:SOURCES 加 `web_search` 並擴充 DB source CHECK(物理);`ingest.py` 的 inferred_domain source 改 `web_search`;`docs/MES_Observation_Schema_v1.md` §5 source 清單升 v2。
- **決定二:新增 `producer` 欄 + crawler_version 歸位。**
  - `producer`(observation_log + knowledge_state 兩表同構)**NOT NULL**(ORM + DB 雙層)+ VARCHAR + CHECK,三值:`mes_crawler_v1` / `duckduckgo_v1` / `manual_v1`。語義=產生此值的方法/模型(責任主體);與 P6 的 provider(外部資料源)刻意區分,命名嚴格用 producer。
  - crawler_version 歸位:拔除暫塞的 `duckduckgo_v1`,回歸只存 git SHA-1;移轉到 producer。
  - 三欄分工:`source`=管道 / `producer`=方法模型 / `crawler_version`=程式碼版本。
  - 寫入鏈路對齊:observed_on_app_store → producer=mes_crawler_v1;inferred_domain(A/B 皆)→ source=web_search + producer=duckduckgo_v1。inference.py 的 result 欄位由 provider 改名 producer。
- **Migration `d9eb673e28aa`(接在 f215 後):** source CHECK 擴充、兩表加 producer(NOT NULL + CHECK,含 backfill 安全)、crawler_version 清 duckduckgo_v1。
- **dev 資料處理:選擇清空重跑(非遷移)。** 理由:最乾淨,且避免 backfill 把舊 inferred_domain 誤標成 mes_crawler_v1。作法:`TRUNCATE observation_log/knowledge_state/entity CASCADE`(row-level Append-Only trigger 對 TRUNCATE **不**觸發,故可清)→ `downgrade base` → `upgrade head` 完整重建(5 種子回來)→ 重跑 live。
- **實跑結果:**
  - `pytest` 43 passed(36 + 7 新:source=web_search 可寫、producer 三值可寫、非法 producer 被拒、producer=NULL 被拒、knowledge producer=NULL 被拒);ruff / mypy 綠。B/C 既有測試全過。
  - **migration 回滾:** 空表上 `downgrade -1 → upgrade head` 通過。**教訓記下:** 有 `web_search` 資料時 downgrade **會**(且應該)失敗——收窄受控詞彙的 downgrade 本就要求該值資料先清掉,這是正確行為不是 bug。
  - **live run 三值全現:** 真實店跑 DDG,observed / fetch_failed / not_found 都出現(連續查詢後 DDG 開始限流 → 多筆 fetch_failed,三值語義誠實運作)。DB 實查:observed_on_app_store→(html_page, mes_crawler_v1);inferred_domain→(web_search, duckduckgo_v1);crawler_version=='duckduckgo_v1' 計數 **0**(歸位確認)。
- **未 commit。**

## 2026-07-11 — Phase 1-C:Scraper + Name→Domain Inference + 雙骨牌寫入鏈路

- **建的檔:** `src/mes/normalize.py`(domain / seed name 正規化,單一收斂)、`src/mes/ingest.py`(雙骨牌寫入鏈路,確定性)、`src/mes/scrape.py`(Loox 評論頁 scraper)、`src/mes/inference.py`(DuckDuckGo Name→Domain)、`tests/test_phase1c_ingest.py`(6 個寫入鏈路測試)。改:`models.py`(entity_type += `store_name_seed`、加 `FEATURES_META`)、migration entity_type CHECK、`pyproject.toml`(+httpx)、`docs/MES_Feature_Taxonomy_v2.md`(元特徵分類軸)。
- **受控詞彙(細化,版號不動):** entity_type 加 `store_name_seed`;feature 加 `observed_on_app_store` / `inferred_domain`(feature 欄不 CHECK 鎖,僅登錄)。
- **雙骨牌結構:** 骨牌一=撈到 Store Name → 建 `store_name_seed` entity + `observed_on_app_store` observation(certain,現場真實)。骨牌二=拿 Seed 推 domain →(A)成功:建 store entity + `inferred_domain`(entity_ref, inferred);(B)失敗:掛 Seed、全欄 NULL、status 依 fetch_failed / not_found 分流。

### Scraper/Inference 實跑的真實情況(本階段最重要)

- **robots.txt(apps.shopify.com,2026-07-11 實查):** User-agent `*` 只擋 `/internal/`、`/services/`、`*q=*`、shpxid/auth 參數;`/loox/reviews` 允許抓。另自我節流 5–25s。
- **Loox 評論頁真實結構:** 每頁 **~10 則**(非想像的 20)。每則在 `data-merchant-review` 區塊;Store Name 在 `tw-text-heading-xs tw-text-fg-primary` div 內的 `<span title="...">`,`title` 屬性存完整店名(視覺截斷也完整)。實測一頁乾淨抓到 10 個店名(如 Wölfe Cutlery、AIRDEKO、Savvy Boheme)。此 selector 版本敏感,結構一變 `parse_store_names` 會回傳變少/空——那本身是訊號,不硬湊。
- **DuckDuckGo 2026 現況:通,但只有一個 endpoint 能用。** `https://html.duckduckgo.com/html/`(POST `q`)回 200 + 10 筆 `result__a` 連結;`https://duckduckgo.com/html/` 回 3KB「browser not supported」空殼、抓不到結果。第一版用前者。
- **命中率初估(live run,前 5 家真實店):5/5 推出 domain。** Wölfe Cutlery→wolfecutlery.com、AIRDEKO→airdeko.com、RipRightWear→riprightsticks.com、Savvy Boheme→savvyboheme.com、T-Toplights→t-toplights.com。樣本極小,不宜外推;RipRightWear→riprightsticks.com 顯示「第一個非黑名單結果」規則可能選到品牌相關但非完全同名的 domain,精確度未經人工核實(這正是 `inferred_domain` 元特徵未來要評估的)。
- **可信 domain 判定規則 v1(可檢驗、可演化):** query = `"<name> shopify store"` → 取 `result__a` 連結,依序找第一個 registrable domain 不在黑名單(shopify.com / pinterest / etsy / amazon / 社群 / 聚合站 shopifyspy 等)的當候選;非 200/timeout/無結果容器 → fetch_failed;有結果但全被黑名單濾掉 → not_found。
- **Provider 版本標註:** inference 的 `duckduckgo_v1` 暫存於 observation 的 `crawler_version` 欄(v1 schema 無專屬 provider/generated_by 欄)。此為權宜,已在 findings 標記待補。

- **測試:** `pytest` 36 passed(30 B + 6 C);ruff / mypy 綠。B 階段既有測試全數保留通過,新增 entity_type/feature 未弄壞既有 CHECK。live run 已把 5 家真實店寫進 dev DB(雙骨牌完整)。
- **未 commit。**

## 2026-07-11 — schema 文件對齊 discriminated union(做法 A,版號不動)

- 把 `docs/` 三份 schema 定稿的 value 欄結構原地更新為 discriminated union,對齊已實作的 `src/mes/db/models.py`。做法 A:原地改 + 修訂註記,**版號不動**(欄位實作細化,非設計變更)。
- **改動段落:**
  - `MES_Observation_Schema_v1.md` §2:value 欄由 `value_normalized` 單欄改為 typed 六欄;新增「value 欄 CHECK 契約」小節(兩層 status↔value_raw / status↔value_type↔typed + value_raw 語義邊界)。
  - `MES_Knowledge_Schema_v1.md` §2:value 欄同步改 typed 六欄;註明與 Observation_Log 同構、無 status 分支的 CHECK、source_observation_id 不變。
  - `MES_Entity_Model_v1.md` §6:value 型別結構整段改寫為 discriminated union,保留「型別自聲明」「raw/normalized 分開=Provenance 微觀落實」論點。
- 三處各加一行修訂註記(2026-07-11 Phase 1-B 細化,版號不動)。與 code 一致:同一組六欄 value 欄、同一組 CHECK 契約。
- **未 commit**(今天結束時統一 commit)。

## 2026-07-11 — Phase 1-B 修訂:value 欄改 discriminated union + 雙層 CHECK

- **動機:** 原 value 欄是單一 `value_raw` + `value_normalized`,無法安全表達五種 value_type,且 knowledge_state 投影時需型別轉換(腐敗點)。改為 discriminated union 分欄結構。因 migration `f215450ec0a6` **尚未 commit、表還空**,直接改乾淨,不新增中間狀態 migration。
- **改了什麼:**
  - `src/mes/db/models.py`:observation_log 與 knowledge_state 兩表**同構** value 容器 —— `value_type` + `value_raw`(只存 feature 原始值原貌)+ typed 分欄 `value_text`(Text)/ `value_number`(Numeric)/ `value_boolean`(Boolean)/ `value_json`(JSONB)/ `value_entity_id`(UUID FK)。移除 `value_normalized`。CHECK 表達式由受控清單常數 + `_exactly_one_typed()` / `_all_typed_null()` 單一函式生成(收斂,不散寫)。
  - `migrations/versions/f215450ec0a6_*.py`:同步改欄位 + 加四條 value CHECK(observation 兩條、knowledge 兩條)。沿用上一批的 UUID / Provenance 雙層 / Append-Only trigger / 5 個種子,那些**未動**。
  - `tests/test_phase1b_schema.py`:更新 helper(改用 typed 欄),擴充逐分支測試。
- **雙層 CHECK 契約(observation_log):**
  - 層一 status↔value_raw:observed → value_raw 非 NULL 且 `btrim(value_raw) <> ''`(空字串一起擋);fetch_failed/not_found → value_raw NULL。
  - 層二 status↔value_type↔typed:observed → 正好一個與 value_type 相符的 typed 欄非空、其餘全空;failed/not_found → 所有 typed 欄全空,但 value_type 保留(描述預期型別)。
  - knowledge_state:無 status 欄(只投影 observed)→ value_raw 非空 + 正好一個相符 typed 欄非空。
- **實跑結果(非假裝):**
  - `pytest` → **30 passed**(真連 DB)。逐項含:5 種 value_type 正確組合可寫;`string` 卻填 `value_number`、`number` 卻同時填 `value_text`+`value_number` **被拒**;observed 但 value_raw=NULL / value_raw='   ' **被拒**;fetch_failed/not_found 帶 value_raw 或 typed 值 **被拒**,全空 + 保留 value_type **可寫**;knowledge 正確組合可寫、value_raw 空被拒、typed 不符 value_type 被拒。
  - 上一批測試(Append-Only 鎖、Provenance NOT NULL、knowledge 可 UPDATE、entity_type/source CHECK)**全數保留且通過**,未被弄壞。
  - `ruff check .` → All passed;`mypy src` → Success;migration down→up 回滾通過,5 筆種子重建。
- **文件不符提醒:** 三份 schema 定稿(Observation Schema v1 §2、Knowledge Schema v1 §2、Entity Model §6)仍記載舊的 `value_raw`/`value_normalized` 單欄形式;此次是 Jeff 指示的結構修訂,schema 文件尚未同步(本次未被要求改 schema 文件)。

## 2026-07-11 — Phase 1-B:三張核心表 ORM + Alembic migration

- **建了什麼:**
  - `src/mes/db/models.py`:`Entity` / `ObservationLog` / `KnowledgeState` 三個 ORM model,嚴格依三份 Phase 0 schema 定稿欄位。所有主鍵 ID 用 UUID、Python 端 `uuid.uuid4` 生成。受控字串(entity_type / value_type / source / confidence / status)用 VARCHAR + CHECK(非原生 ENUM)。`feature` 不 CHECK 鎖死(彈性結構),9 個 v1 feature 以常數 `FEATURES_V1` 列出供參考。
  - `src/mes/db/__init__.py`:匯出 `Base` 與三個 model。
  - `migrations/env.py`:import `mes.db.models` 讓 autogenerate 看得到 metadata。
  - `migrations/versions/f215450ec0a6_*.py`:autogenerate 三表後,手動補上 (a) observation_log 的 Append-Only trigger(plpgsql `mes_reject_mutation` + BEFORE UPDATE/DELETE 兩個 trigger),(b) 五個 ReviewApp 種子 entity(loox/judgeme/yotpo/okendo/stamped,固定 UUID)。downgrade 對稱清掉 trigger/function。
  - `tests/test_phase1b_schema.py`:12 個整合測試(真連 PostgreSQL,非 mock)。
  - `pyproject.toml`:ruff `extend-exclude = ["migrations/versions"]`(Alembic 生成碼不 lint)。
- **實跑結果(非假裝):**
  - `alembic upgrade head` → 成功;`docker exec psql` 確認 3 表 + 2 個 trigger + 5 筆 review_app 種子皆在。
  - **Append-Only 鎖(決定三關鍵)實測生效:** 對 observation_log 做 UPDATE / DELETE **被 DB 拒絕**,錯誤訊息含 "append-only"。knowledge_state 的 UPDATE **正常成功**(物化語義未被誤鎖)。
  - **Provenance 硬約束實測生效:** observation_log 寫 `entity_id=NULL` 被拒;knowledge_state 寫 `source_observation_id=NULL` 被拒。
  - **受控字串 CHECK 實測生效:** 非法 status/confidence/value_type/source 四種各自被拒。
  - `pytest` → **12 passed**;`ruff check .` → All checks passed;`mypy src` → Success。
  - **migration 回滾驗證:** `downgrade base`(trigger/function 歸零)→ `upgrade head`(重建 + 重新種子 5 筆)round-trip 通過。
- **未碰:** C(Scraper / Inference / 寫入鏈路)與 D(9 個 feature 實際抓取)仍未開始,屬後續階段。

## 2026-07-10 — DB 連線骨架:同步 → async 校正

- **動機:** 既有連線骨架用同步 engine,與 Roadmap v8 / CLAUDE.md 的「SQLAlchemy 2(Async)」基線不符。本次把連線層校正為 async,code 與文件同步更新。
- **改的檔案:**
  - `pyproject.toml`:相依加入 `asyncpg>=0.29`;`sqlalchemy>=2.0` 改為 `sqlalchemy[asyncio]>=2.0`(async 需 greenlet,由此 extra 帶入)。psycopg 保留供 Alembic 同步 migration 使用。
  - `src/mes/db/session.py`:`create_engine` → `create_async_engine`;`sessionmaker` → `async_sessionmaker`/`AsyncSession`;`get_session` 改 async context manager;`check_connection` 改 async。
  - `tests/test_database_connection.py`:改為 async 測試,實連 PostgreSQL(asyncpg)跑 `SELECT 1`,維持非 mock、真連線。
  - `.env` / `.env.example`:`MES_DATABASE_URL` driver 由 `postgresql+psycopg` 改為 `postgresql+asyncpg`(URL 仍由 `MES_DATABASE_URL` 供給,機制不變)。
  - `migrations/env.py`:Alembic migration 本質同步,於 env.py 內把 asyncpg URL 轉回 `+psycopg` 供其連線,避免 async URL 弄壞既有 migration 連線。
- **實跑結果(非假裝):**
  - `uv run pytest` → **1 passed**(async 連線 + `SELECT 1` 通過;PostgreSQL 容器 healthy)。
  - `uv run ruff check .` → All checks passed;`uv run mypy src` → Success, no issues。
  - `uv run alembic current` → 正常連線(sync psycopg driver),未被 async URL 破壞。
- **未碰:** Phase 1-B 的 ORM model 仍未建;本次只動連線層。B 階段之後建 model 與寫入邏輯時,直接長在 async 基礎上,無額外「延後待辦」。

## 2026-07-10

- 建立四份專案常駐文件:`task_plan.md`、`CLAUDE.md`、`progress.md`、`findings.md`。
  - `task_plan.md`:已存在,依「修改不重建」原則在既有結構上更新(未整份覆蓋)。
  - `CLAUDE.md`、`progress.md`、`findings.md`:本次新建。
- 建立前已實際讀取 `docs/` 下六份定稿文件當前內容為依據:`MES_Roadmap_v8.md`(主依據)、`MES_Entity_Model_v1.md`、`MES_Observation_Schema_v1.md`、`MES_Knowledge_Schema_v1.md`、`MES_Feature_Taxonomy_v2.md`、`MES_Build_vs_Buy_Matrix_v1.md`。六份皆存在、檔名相符。
