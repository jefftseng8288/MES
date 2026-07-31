"""評論數 / 平均分採集 —— 通用入口 + per-app handler(可插拔)。

**目標:不管店家用什麼 review app,都要能抓到 review_count 與 avg_rating。**
「五個 app」是目前的工具限制,不是目標的邊界。

**★ 通用抓法探測結果(2026-07-31,11 家真實店實測):不可行,故以 per-app handler 為主。**
探的是 schema.org 的 `aggregateRating`(JSON-LD / microdata)—— 理論上最有希望,因為 review app
有強烈動機注入它(Google Rich Snippets 是它們的 SEO 賣點)。實測兩個獨立的致命問題:

  1. **覆蓋率只有 1/11**(僅 miramat.co.nz 有),其餘 10 家商品頁完全沒有。
  2. **就算有,語義也不對** —— 那筆是 `@type: Product` 的**單一商品**評論數(9 則),
     **不是全店總數**。拿它當 review_count 會把「單品數」偽裝成「全店數」。
  3. 沒有的那些不是「沒評分」,而是評分由 **JS 執行期注入**(靜態 HTML 只有空容器
     `loox-rating loox-widget`,靜態抓取看不到任何數字;要讀得靠 headless browser)。

**結論:通用入口(dispatch)保留,但目前沒有語義正確的通用 handler。** 未來若找到,插進
`HANDLERS` 即可,核心不動 —— 這也是為什麼架構要做成可插拔。

**★ 限流性質與 harvest 不同(重要):** harvest 戳的是每家店自己的伺服器(per-domain,
每家一次,總量不是問題);但 handler 打的是 **review app 自己的伺服器** —— 所有 loox 店的
請求都打向同一個 `loox.io`,性質接近 baseline 打同一個 DuckDuckGo。故對第三方伺服器的請求
**獨立節流**(見 `_third_party_sleep`),不比照 harvest 的寬鬆。
(通用抓法若可行就沒這問題 —— 那是從店家自己的頁面讀,這是通用路徑的另一個優點。)
"""

from __future__ import annotations

import logging
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

FEATURE_REVIEW_COUNT = "review_count"
FEATURE_AVG_RATING = "avg_rating"
FEATURE_RATING_DISTRIBUTION = "rating_distribution"

# source 新值:評論數來自 **review app 自己的 widget 頁面**,不是店家的 html_page。
# 沿用 web_search 的先例:管道欄寧可補一個誠實的新值,也不要借一個語義不符的舊值。
SOURCE_REVIEW_WIDGET = "review_widget"

# 對第三方(loox.io 等)伺服器的請求間隔 —— 比 harvest 的 per-domain 更保守,
# 因為所有店的請求都打向同一台。暫定值,待實況調整。
MIN_THIRD_PARTY_DELAY = 8.0
MAX_THIRD_PARTY_DELAY = 25.0

_LOOX_WIDGET_URL = "https://loox.io/widget/{widget_id}/reviews"
# widget id 的 markup 變體(**三種都是實測遇到的**,不是預防性猜測):
#   ① script URL   ② HTML 註解 __LXC   ③ JSON 字串內的跳脫斜線(loox.io\/widget\/xxx\/loox)
# 三家店就出現三種 → 幾乎可以確定還有第四種,取不到時記 fetch_failed 保留原因,不靜默跳過。
_LOOX_ID_PATTERNS = (
    re.compile(r"loox\.io\\?/widget\\?/([A-Za-z0-9_\-]+)\\?/loox"),  # ① 與 ③(容忍 \/)
    re.compile(r"__LXC:\s*([A-Za-z0-9_\-]+)"),  # ②
)
# ★ 總數:錨在**結構**(data-testid)而非文字 —— widget 頁的可見文字會依店家語言在地化
# (實測西班牙文店顯示 "1,019 Reseñas" 而非 "Reviews"),寫死英文會漏掉所有非英文店。
_LOOX_COUNT_RE = re.compile(
    r'data-testid="rating-summary-count"[^>]*>\s*<span>([\d,]+)', re.I
)
# 平均分與星等分佈取自 aria-label —— 實測 aria-label **不隨語言在地化**(西班牙文頁仍是
# "Average rating: 4.9 / 5"),故可跨語言使用。
_LOOX_AVG_RE = re.compile(r'aria-label="Average rating:\s*([\d.]+)\s*/\s*5', re.I)
# ★ 分佈:錨在「N stars, M reviews」的 aria-label,**依星等自我標示**,不靠位置。
# (先前用「總數後面接的 5 個數字」是位置式解析,實測在小店會抓錯 → 已改結構式。)
_LOOX_DIST_RE = re.compile(r'(\d)\s*stars?,\s*([\d,]+)\s*reviews?', re.I)


@dataclass(frozen=True)
class ReviewStats:
    """一次評論數採集的結果(三值語義由 status 承載)。"""

    status: str  # observed / not_found / fetch_failed
    review_count: int | None = None
    avg_rating: float | None = None
    # avg 由分佈計算而來(非頁面直讀)→ confidence 降級為 estimated,誠實標記。
    avg_is_computed: bool = False
    distribution: dict[str, int] | None = None
    reason: str | None = None  # 失敗/不具備的具體原因(不靜默跳過)
    app_key: str | None = None


class BaseReviewHandler(ABC):
    """每個 review app 一個 handler。加新 app 只需加一個類別,不動核心。"""

    app_key: str

    @abstractmethod
    def fetch(self, domain: str, homepage_html: str, client: httpx.Client) -> ReviewStats:
        """從該 app 取得全店評論數 / 平均分 / 星等分佈。"""


def _third_party_sleep(enabled: bool) -> None:
    """打第三方 review app 伺服器前的節流(所有店打同一台,故獨立且較保守)。"""
    if enabled:
        time.sleep(random.uniform(MIN_THIRD_PARTY_DELAY, MAX_THIRD_PARTY_DELAY))


def extract_loox_widget_id(homepage_html: str) -> str | None:
    """從店首頁取 loox widget id —— 兩種 markup 都支援(實測已見兩種)。"""
    for pattern in _LOOX_ID_PATTERNS:
        m = pattern.search(homepage_html)
        if m:
            return m.group(1)
    return None


def parse_loox_widget(html: str) -> ReviewStats:
    """解析 loox 評論牆頁:總數 + 平均分 + 星等分佈,並做加總驗算。

    版面(實測 2026-07-31):`data-testid="rating-summary-count"` 給總數、
    `aria-label="Average rating: 4.8 / 5"` 給平均、其後 5 個數字是 5→1 星的分佈。
    """
    m_count = _LOOX_COUNT_RE.search(html)
    if not m_count:
        return ReviewStats(
            "fetch_failed", reason="loox 評論牆頁解析不到總數(版面可能改版)", app_key="loox"
        )
    count = int(m_count.group(1).replace(",", ""))

    avg: float | None = None
    m_avg = _LOOX_AVG_RE.search(html)
    if m_avg:
        avg = float(m_avg.group(1))

    # 星等分佈:每個 aria-label 自我標示星等(結構式,非位置式)。
    found = {star: int(n.replace(",", "")) for star, n in _LOOX_DIST_RE.findall(html)}
    distribution: dict[str, int] | None = None
    if found:
        # ★ 自我驗算:分佈加總必須等於總數,不符就不採用(不靜默採用壞資料)。
        if sum(found.values()) == count:
            distribution = found
        else:
            logger.warning(
                "[reviews] loox 分佈加總 %d != 總數 %d,分佈不採用(僅記總數)",
                sum(found.values()), count,
            )

    computed = False
    if avg is None and distribution and count > 0:
        # 頁面沒直接給平均 → 由分佈計算(誠實標記為計算而來)。
        avg = sum(int(star) * n for star, n in distribution.items()) / count
        computed = True

    return ReviewStats(
        "observed", review_count=count, avg_rating=avg, avg_is_computed=computed,
        distribution=distribution, app_key="loox",
    )


class LooxHandler(BaseReviewHandler):
    """loox:① 店首頁取 widget id ② 打 loox.io 的評論牆頁(第三方,需獨立節流)。"""

    app_key = "loox"

    def fetch(
        self, domain: str, homepage_html: str, client: httpx.Client, *, sleep: bool = True
    ) -> ReviewStats:
        widget_id = extract_loox_widget_id(homepage_html)
        if not widget_id:
            # 裝了 loox 卻取不到 id(可能是第三種 markup)→ 我們不知道結果 = fetch_failed。
            return ReviewStats(
                "fetch_failed",
                reason="首頁取不到 loox widget id(可能是未知的第三種 markup)",
                app_key="loox",
            )
        _third_party_sleep(sleep)
        try:
            r = client.get(
                _LOOX_WIDGET_URL.format(widget_id=widget_id),
                headers={"Referer": f"https://{domain}/"},
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            return ReviewStats(
                "fetch_failed", reason=f"loox 評論牆頁連線失敗:{type(exc).__name__}",
                app_key="loox",
            )
        if r.status_code != 200:
            return ReviewStats(
                "fetch_failed", reason=f"loox 評論牆頁 HTTP {r.status_code}", app_key="loox"
            )
        return parse_loox_widget(r.text)


# 通用入口的可插拔 handler 表。加新 app = 加一個類別丟進來,核心不動。
HANDLERS: dict[str, BaseReviewHandler] = {h.app_key: h for h in (LooxHandler(),)}


def fetch_review_stats(
    domain: str,
    homepage_html: str | None,
    app_key: str | None,
    client: httpx.Client,
    *,
    sleep: bool = True,
) -> ReviewStats:
    """通用入口:依 `uses_review_app` 的結果分派到對應 handler。

    ★ 三值語義(不可混淆):
      - **沒偵測到 review app**(uses_review_app = not_found)→ `not_found`
        「確認不具備」——沒裝就沒有評論數可抓,這是有效的負向觀測,不是失敗。
      - **偵測到了但我們沒有該 app 的 handler**(如 yotpo)→ `fetch_failed`
        店家其實有評論,只是**我們讀不到** = 系統能力邊界 → 我們**不知道**結果。
        記 not_found 會謊稱「確認沒有評論」。
      - **有 handler 但抓取/解析失敗** → `fetch_failed`。
    """
    if homepage_html is None:
        return ReviewStats("fetch_failed", reason="首頁抓取失敗,無法取得 widget 資訊")
    if not app_key:
        return ReviewStats("not_found", reason="未偵測到 review app(沒裝或 signature 認不出)")
    handler = HANDLERS.get(app_key)
    if handler is None:
        return ReviewStats(
            "fetch_failed",
            reason=f"尚無 {app_key} handler(店家有評論但我們讀不到,非確認不存在)",
            app_key=app_key,
        )
    return handler.fetch(domain, homepage_html, client, sleep=sleep)  # type: ignore[call-arg]
