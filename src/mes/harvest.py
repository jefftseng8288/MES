"""Phase 1-D: harvest a store's 9 market features from its own storefront.

INDEPENDENT from the baseline DDG chain — this pokes each store's own server
(products.json + homepage), a different target with independent rate-limiting; the
two chains run in parallel without interfering.

Real-world, version-sensitive. Structure verified against live Shopify stores on
2026-07-15: products.json variants carry `price` (string, no currency); the homepage
exposes `Shopify.theme.name` / `Shopify.country` / `Shopify.locale` / `Shopify.currency`
(currency lives in the HTML, NOT products.json).

Three-value discipline is enforced PER FEATURE (失敗不偽裝):
- observed     : fetched and got a valid value. is_active=false (confirmed empty/locked)
                 is a VALID observed negative, not a failure.
- not_found    : reached the store but this feature has no public value
                 (products.json 404 / password page / var absent).
- fetch_failed : couldn't reach it (timeout / blocked / connection error) — unknown.

confidence: direct-read store-reported values (products.json, Shopify.* vars) = certain;
uses_review_app = inferred (HTML signature is a guess — script may linger / misfire),
like inferred_domain.
"""

from __future__ import annotations

import json
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, ObservationLog, StoreHarvestState
from mes.jobs import JOB_HARVEST, heartbeat
from mes.reviews import (
    FEATURE_AVG_RATING,
    FEATURE_RATING_DISTRIBUTION,
    FEATURE_REVIEW_COUNT,
    SOURCE_REVIEW_WIDGET,
    ReviewStats,
    fetch_review_stats,
)

# producer for store-facing harvest (distinct from crawler / duckduckgo). NOT NULL + CHECK.
PRODUCER_STORE_CRAWLER = "mes_store_crawler_v1"
# 每批家數。**限流性質與 baseline 不同,故可比 baseline 寬鬆:** baseline 戳的是同一個
# DuckDuckGo(總頻率必須控制);harvest 戳的是**每家店自己的伺服器、每家只戳一次** →
# 限流是 per-domain 的。同批抓 N 家不同店,對每一家而言都只是一次請求。
# 「對單一店家的頻率」由 MIN_RETRY_INTERVAL_DAYS 控制,不是靠壓低批量。(暫定值,可調)
STORE_BATCH_SIZE = 15
# 最小重抓間隔:此期間內已嘗試過的店本輪跳過(暫定 7 天,待實況調整)。
# 保護用途:候選店數少時,避免同一家在短時間內被反覆戳。
MIN_RETRY_INTERVAL_DAYS = 7
_HARVEST_LOG = Path("logs/harvest_features.log")

# --- Feature -> source / confidence -----------------------------------------
FEATURE_PRODUCT_COUNT = "product_count"
FEATURE_AVG_PRICE = "avg_price"
FEATURE_PRICE_RANGE = "price_range"
FEATURE_CURRENCY = "currency"
FEATURE_IS_ACTIVE = "is_active"
FEATURE_THEME_NAME = "theme_name"
FEATURE_COUNTRY = "country"
FEATURE_LANGUAGE = "language"
FEATURE_USES_REVIEW_APP = "uses_review_app"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
STORE_MAX_PAGES = 5  # products.json pagination cap (politeness); beyond -> estimated
# Random gap between store-facing requests (防被 CDN 識別;暫定起點,待實況回饋調整).
MIN_REQ_DELAY = 3.0
MAX_REQ_DELAY = 12.0

# Homepage Shopify.* extractors (verified on real stores 2026-07-15).
_THEME_RE = re.compile(r'Shopify\.theme\s*=\s*\{[^}]*?"name"\s*:\s*"([^"]+)"')
_COUNTRY_RE = re.compile(r'Shopify\.country\s*=\s*"([^"]+)"')
_LOCALE_RE = re.compile(r'Shopify\.locale\s*=\s*"([^"]+)"')
_CURRENCY_RE = re.compile(r'Shopify\.currency\s*=\s*\{[^}]*?"active"\s*:\s*"([^"]+)"')

# uses_review_app HTML signatures -> review_app canonical_key. First match wins.
REVIEW_APP_SIGNATURES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"loox\.io|looxreviews|data-loox", re.I), "loox"),
    (re.compile(r"jdgm|judge\.me|judgeme", re.I), "judgeme"),
    (re.compile(r"yotpo", re.I), "yotpo"),
    (re.compile(r"okendo", re.I), "okendo"),
    (re.compile(r"stamped\.io|stamped-", re.I), "stamped"),
]


@dataclass(frozen=True)
class FeatureResult:
    feature: str
    status: str  # observed / fetch_failed / not_found
    value_type: str
    confidence: str
    source: str
    value_raw: str | None = None
    value_text: str | None = None
    value_number: float | None = None
    value_boolean: bool | None = None
    value_json: dict[str, object] | None = None
    value_entity_id: uuid.UUID | None = None


@dataclass
class ProductsFetch:
    outcome: str  # 'ok' | 'not_found' | 'failed'
    products: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = True  # False = hit pagination cap with a full last page


def _absent(
    feature: str, status: str, value_type: str, source: str, confidence: str
) -> FeatureResult:
    """A fetch_failed / not_found result: value_type retained, all value cols NULL."""
    return FeatureResult(feature, status, value_type, confidence, source)


def _sleep(enabled: bool) -> None:
    if enabled:
        time.sleep(random.uniform(MIN_REQ_DELAY, MAX_REQ_DELAY))


def fetch_products(domain: str, client: httpx.Client, *, req_sleep: bool = True) -> ProductsFetch:
    """Fetch /products.json paginated. Maps HTTP reality to outcome ok/not_found/failed."""
    collected: list[dict[str, object]] = []
    for page in range(1, STORE_MAX_PAGES + 1):
        try:
            r = client.get(
                f"https://{domain}/products.json",
                params={"limit": 250, "page": page},
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return ProductsFetch("failed")
        if r.status_code in (403, 429, 500, 502, 503, 504):
            return ProductsFetch("failed")  # blocked / server error -> unknown
        if r.status_code in (401, 404):
            return ProductsFetch("not_found")  # password / no endpoint -> reachable, no data
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            return ProductsFetch("not_found")  # password HTML page etc.
        try:
            page_products = r.json().get("products", [])
        except (ValueError, json.JSONDecodeError):
            return ProductsFetch("not_found")
        collected.extend(page_products)
        if len(page_products) < 250:
            return ProductsFetch("ok", collected, complete=True)
        if page < STORE_MAX_PAGES:
            _sleep(req_sleep)
    return ProductsFetch("ok", collected, complete=False)  # hit cap, more exist


def fetch_homepage(domain: str, client: httpx.Client) -> str | None:
    """Return homepage HTML, or None if unreachable (fetch_failed)."""
    try:
        r = client.get(f"https://{domain}/", follow_redirects=True)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    return r.text


def _variant_prices(products: list[dict[str, Any]]) -> list[float]:
    prices: list[float] = []
    for p in products:
        for v in p.get("variants") or []:
            raw = v.get("price")
            if raw in (None, ""):
                continue
            try:
                prices.append(float(raw))
            except (TypeError, ValueError):
                continue
    return prices


def parse_products_features(pf: ProductsFetch) -> list[FeatureResult]:
    """5 products.json features. currency comes from the homepage, not here."""
    src = "products_json"
    if pf.outcome == "failed":
        return [
            _absent(FEATURE_PRODUCT_COUNT, "fetch_failed", "number", src, "certain"),
            _absent(FEATURE_AVG_PRICE, "fetch_failed", "number", src, "certain"),
            _absent(FEATURE_PRICE_RANGE, "fetch_failed", "json", src, "certain"),
            _absent(FEATURE_IS_ACTIVE, "fetch_failed", "boolean", src, "certain"),
        ]
    if pf.outcome == "not_found":
        # reachable but no products data (404 / password / empty endpoint) = not an open store
        return [
            _absent(FEATURE_PRODUCT_COUNT, "not_found", "number", src, "certain"),
            _absent(FEATURE_AVG_PRICE, "not_found", "number", src, "certain"),
            _absent(FEATURE_PRICE_RANGE, "not_found", "json", src, "certain"),
            _observed_bool(FEATURE_IS_ACTIVE, False),
        ]
    # ok
    n = len(pf.products)
    conf = "certain" if pf.complete else "estimated"
    count_raw = str(n) if pf.complete else f">={n} ({STORE_MAX_PAGES} pages sampled)"
    results = [
        FeatureResult(FEATURE_PRODUCT_COUNT, "observed", "number", conf, src,
                      value_raw=count_raw, value_number=n),
    ]
    prices = _variant_prices(pf.products)
    if prices:
        avg = round(sum(prices) / len(prices), 2)
        rng: dict[str, object] = {"min": min(prices), "max": max(prices)}
        results.append(FeatureResult(FEATURE_AVG_PRICE, "observed", "number", conf, src,
                                      value_raw=str(avg), value_number=avg))
        results.append(FeatureResult(FEATURE_PRICE_RANGE, "observed", "json", conf, src,
                                      value_raw=json.dumps(rng), value_json=rng))
    else:
        results.append(_absent(FEATURE_AVG_PRICE, "not_found", "number", src, "certain"))
        results.append(_absent(FEATURE_PRICE_RANGE, "not_found", "json", src, "certain"))
    results.append(_observed_bool(FEATURE_IS_ACTIVE, n > 0))
    return results


def _observed_bool(feature: str, val: bool) -> FeatureResult:
    return FeatureResult(feature, "observed", "boolean", "certain", "products_json",
                         value_raw="true" if val else "false", value_boolean=val)


def parse_homepage_features(
    html: str | None, review_apps: dict[str, uuid.UUID]
) -> list[FeatureResult]:
    """4 homepage features: theme_name / country / language / currency + uses_review_app."""
    if html is None:
        return [
            _absent(FEATURE_THEME_NAME, "fetch_failed", "string", "html_page", "certain"),
            _absent(FEATURE_COUNTRY, "fetch_failed", "string", "html_page", "certain"),
            _absent(FEATURE_LANGUAGE, "fetch_failed", "string", "html_page", "certain"),
            _absent(FEATURE_CURRENCY, "fetch_failed", "string", "html_page", "certain"),
            _absent(FEATURE_USES_REVIEW_APP, "fetch_failed", "entity_ref", "html_signature",
                    "inferred"),
        ]
    results: list[FeatureResult] = []
    for feature, pat in [
        (FEATURE_THEME_NAME, _THEME_RE),
        (FEATURE_COUNTRY, _COUNTRY_RE),
        (FEATURE_LANGUAGE, _LOCALE_RE),
        (FEATURE_CURRENCY, _CURRENCY_RE),
    ]:
        m = pat.search(html)
        if m:
            results.append(FeatureResult(feature, "observed", "string", "certain", "html_page",
                                         value_raw=m.group(1), value_text=m.group(1)))
        else:
            results.append(_absent(feature, "not_found", "string", "html_page", "certain"))

    # uses_review_app: HTML signature -> inferred (沒命中 ≠ 沒裝 -> not_found + inferred)
    hit = next((key for rx, key in REVIEW_APP_SIGNATURES if rx.search(html)), None)
    if hit is not None and hit in review_apps:
        results.append(FeatureResult(FEATURE_USES_REVIEW_APP, "observed", "entity_ref", "inferred",
                                     "html_signature", value_raw=hit,
                                     value_entity_id=review_apps[hit]))
    else:
        results.append(_absent(FEATURE_USES_REVIEW_APP, "not_found", "entity_ref", "html_signature",
                               "inferred"))
    return results


def parse_review_features(stats: ReviewStats) -> list[FeatureResult]:
    """把一次評論採集結果轉成 3 個 FeatureResult(三值語義由 stats.status 承載)。

    confidence:review app 自報的數字是直讀 → `certain`;
    avg_rating 若由分佈**計算**而來(頁面沒直接給)→ 降級為 `estimated`,誠實標記。
    """
    src = SOURCE_REVIEW_WIDGET
    if stats.status != "observed":
        return [
            _absent(FEATURE_REVIEW_COUNT, stats.status, "number", src, "certain"),
            _absent(FEATURE_AVG_RATING, stats.status, "number", src, "certain"),
            _absent(FEATURE_RATING_DISTRIBUTION, stats.status, "json", src, "certain"),
        ]

    out = [FeatureResult(
        FEATURE_REVIEW_COUNT, "observed", "number", "certain", src,
        value_raw=str(stats.review_count), value_number=float(stats.review_count or 0),
    )]
    if stats.avg_rating is None:
        out.append(_absent(FEATURE_AVG_RATING, "not_found", "number", src, "certain"))
    else:
        out.append(FeatureResult(
            FEATURE_AVG_RATING, "observed", "number",
            "estimated" if stats.avg_is_computed else "certain", src,
            value_raw=f"{stats.avg_rating:g}", value_number=float(stats.avg_rating),
        ))
    if stats.distribution is None:
        # 驗算不符或頁面沒給 → 確認這次拿不到可信分佈(不靜默採用壞資料)。
        out.append(_absent(FEATURE_RATING_DISTRIBUTION, "not_found", "json", src, "certain"))
    else:
        out.append(FeatureResult(
            FEATURE_RATING_DISTRIBUTION, "observed", "json", "certain", src,
            value_raw=json.dumps(stats.distribution, sort_keys=True),
            value_json=dict(stats.distribution),
        ))
    return out


def harvest_store(
    domain: str, review_apps: dict[str, uuid.UUID], *, req_sleep: bool = True
) -> list[FeatureResult]:
    """Poke one store (products.json + homepage + reviews) -> 12 FeatureResults.

    ★ 順序有依賴:評論數要先知道店家用哪個 review app(才知道用哪套 handler),
    故必須在 `parse_homepage_features` 解析出 uses_review_app **之後**才做。
    """
    with httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=20.0) as client:
        pf = fetch_products(domain, client, req_sleep=req_sleep)
        _sleep(req_sleep)
        html = fetch_homepage(domain, client)
        home_features = parse_homepage_features(html, review_apps)
        # 由 uses_review_app 的結果決定用哪個 handler(認不出 -> app_key None -> not_found)。
        ura = next(r for r in home_features if r.feature == FEATURE_USES_REVIEW_APP)
        app_key = None
        if ura.status == "observed" and ura.value_entity_id is not None:
            app_key = next(
                (k for k, v in review_apps.items() if v == ura.value_entity_id), None
            )
        stats = fetch_review_stats(domain, html, app_key, client, sleep=req_sleep)
    return parse_products_features(pf) + home_features + parse_review_features(stats)


# --- Batch runner (independent schedule; writes onto the store entity) --------


@dataclass(frozen=True)
class HarvestReport:
    batch_id: str
    stores: list[tuple[str, list[FeatureResult]]]  # (domain, 12 results)

    def format(self) -> str:
        lines = ["===== MES 市場 feature 撈取報告 (Store Harvest) =====",
                 f"批號 (batch_id): {self.batch_id}",
                 f"本批店數: {len(self.stores)}", ""]
        for domain, results in self.stores:
            obs = sum(r.status == "observed" for r in results)
            nf = sum(r.status == "not_found" for r in results)
            ff = sum(r.status == "fetch_failed" for r in results)
            got = {r.feature: r.value_raw for r in results if r.status == "observed"}
            lines.append(f"  {domain}: observed {obs}/12 · not_found {nf} · fetch_failed {ff}")
            lines.append(f"     {got}")
        lines.append("=" * 46)
        return "\n".join(lines)


async def _load_review_apps(session: AsyncSession) -> dict[str, uuid.UUID]:
    rows = await session.execute(
        select(Entity.canonical_key, Entity.entity_id).where(Entity.entity_type == "review_app")
    )
    return {key: eid for key, eid in rows.all()}


async def _select_stores_to_harvest(
    session: AsyncSession, limit: int, *, now: datetime | None = None
) -> list[tuple[uuid.UUID, str]]:
    """挑「最久沒嘗試的」店 —— 沒試過的優先,其次依上次嘗試時間由舊到新。

    **為什麼不是「只挑沒抓成功過的」(舊做法):**
    舊條件是 `state IS NULL OR status IN (pending, failed)` + `ORDER BY entity.created_at`,
    有兩個致命後果:
      1. **卡死(head-of-line blocking):** 失敗的店永遠留在候選、又永遠排最前 → 每批都挑
         同樣那幾家、又失敗、再挑 —— 實測曾連續 16 天(125 批)只抓同 3 家假網域。
      2. **`done` 的店永不重抓 → 每家一輩子只有一筆觀測。** 那樣 `feature_history` 每個
         feature 永遠只有一個點,**Growth 類 insight 從架構上不可能成立**,且資料抓完
         當天就開始過期、永不更新 —— 與「資料是流動的」直接牴觸。

    **現行做法:所有 store 都是候選**(含 `done`),依「最久沒嘗試」排序。
    `store_harvest_state.updated_at` 由 `_upsert_state` 在**每次嘗試後**更新(不論成功或
    失敗),故它就是「最後嘗試時間」—— 這是本排序能運作的前提。

    **天然退避:** 試過的店排到隊尾,要輪完一圈才會再輪到,不必另設退避時間或失敗次數上限。
    **實際重抓週期 = 候選店數 ÷ 每日抓取量,自適應**,不需另外設定。
    另加 `MIN_RETRY_INTERVAL_DAYS` 最小重抓間隔作保護(候選店少時避免同一家被反覆戳)。
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=MIN_RETRY_INTERVAL_DAYS)
    rows = await session.execute(
        select(Entity.entity_id, Entity.canonical_key)
        .outerjoin(StoreHarvestState, StoreHarvestState.entity_id == Entity.entity_id)
        .where(
            Entity.entity_type == "store",
            # 沒嘗試過 → 一定是候選;嘗試過 → 需超過最小重抓間隔。
            or_(
                StoreHarvestState.entity_id.is_(None),
                StoreHarvestState.updated_at < cutoff,
            ),
        )
        # 沒試過的(NULL)排最前,其餘最久沒試的優先;同時間用 created_at 決定性收尾。
        .order_by(StoreHarvestState.updated_at.asc().nulls_first(), Entity.created_at)
        .limit(limit)
    )
    return [(eid, key) for eid, key in rows.all()]


async def candidate_pool_stats(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, int]:
    """候選池概況 —— ★ 警鈴用來分辨「挑到 0 家」是**正常閒置**還是**異常**的依據。

    - `eligible` = 現在可挑的家數(沒試過,或已超過最小重抓間隔)。
    - `gated_by_interval` = 因為在最小重抓間隔內而本輪跳過的家數。

    判讀:挑到 0 家且 `eligible == 0` → 全被間隔 gate 住,**正常閒置**(自適應的結果);
    挑到 0 家但 `eligible > 0` → **挑選邏輯異常**,該叫。
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=MIN_RETRY_INTERVAL_DAYS)
    total = await session.scalar(
        select(func.count()).select_from(Entity).where(Entity.entity_type == "store")
    )
    eligible = await session.scalar(
        select(func.count())
        .select_from(Entity)
        .outerjoin(StoreHarvestState, StoreHarvestState.entity_id == Entity.entity_id)
        .where(
            Entity.entity_type == "store",
            or_(
                StoreHarvestState.entity_id.is_(None),
                StoreHarvestState.updated_at < cutoff,
            ),
        )
    )
    total, eligible = int(total or 0), int(eligible or 0)
    return {"stores_total": total, "eligible": eligible, "gated_by_interval": total - eligible}


async def _write_feature(
    session: AsyncSession, store_id: uuid.UUID, batch_id: str, r: FeatureResult
) -> None:
    session.add(
        ObservationLog(
            entity_id=store_id,
            feature=r.feature,
            value_type=r.value_type,
            value_raw=r.value_raw,
            value_text=r.value_text,
            value_number=r.value_number,
            value_boolean=r.value_boolean,
            value_json=r.value_json,
            value_entity_id=r.value_entity_id,
            source=r.source,
            producer=PRODUCER_STORE_CRAWLER,
            observed_at=datetime.now(UTC),
            confidence=r.confidence,
            status=r.status,
            batch_id=batch_id,
        )
    )


async def _upsert_state(session: AsyncSession, store_id: uuid.UUID, status: str) -> None:
    stmt = pg_insert(StoreHarvestState).values(entity_id=store_id, status=status)
    stmt = stmt.on_conflict_do_update(
        index_elements=["entity_id"], set_={"status": status, "updated_at": func.now()}
    )
    await session.execute(stmt)


async def run_store_harvest_batch(
    *,
    batch_size: int = STORE_BATCH_SIZE,
    store_sleep: bool = True,
    req_sleep: bool = True,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    emit: bool = True,
) -> HarvestReport:
    """One store-harvest batch: pick pending stores, poke each, write 9 features, update state.

    batch_id is resolved via the shared date-NN scheme (slot=None -> -04+, distinct from
    the baseline's -01/-02/-03; distinguished from manual baseline runs by producer/feature).
    """
    from mes.pipeline import _TAIPEI, _resolve_batch_id  # local import avoids cycle

    engine = None
    if session_maker is None:
        engine = create_async_engine(get_settings().database_url)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    stores_done: list[tuple[str, list[FeatureResult]]] = []
    batch_id = ""
    try:
        async with heartbeat(JOB_HARVEST) as beat, session_maker() as session:
            day_str = datetime.now(_TAIPEI).date().isoformat()
            batch_id = await _resolve_batch_id(session, day_str, None)
            review_apps = await _load_review_apps(session)
            # 先拍候選池快照 —— 挑到 0 家時,警鈴靠它分辨正常閒置 vs 挑選邏輯異常。
            pool = await candidate_pool_stats(session)
            targets = await _select_stores_to_harvest(session, batch_size)
            written = 0
            for i, (store_id, domain) in enumerate(targets):
                results = harvest_store(domain, review_apps, req_sleep=req_sleep)
                for r in results:
                    await _write_feature(session, store_id, batch_id, r)
                    written += 1
                reachable = any(r.status != "fetch_failed" for r in results)
                await _upsert_state(session, store_id, "done" if reachable else "failed")
                await session.commit()
                stores_done.append((domain, results))
                if store_sleep and i < len(targets) - 1:
                    _sleep(True)
            beat.summary = {
                "batch_id": batch_id, "batch_size": batch_size, **pool,
                "selected": len(targets),
                "reachable": sum(
                    1 for _, rs in stores_done if any(r.status != "fetch_failed" for r in rs)
                ),
                "observations_written": written,
            }
    finally:
        if engine is not None:
            await engine.dispose()

    report = HarvestReport(batch_id, stores_done)
    if emit:
        text = report.format()
        print(text)
        _HARVEST_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _HARVEST_LOG.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")
    return report
