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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, ObservationLog, StoreHarvestState

# producer for store-facing harvest (distinct from crawler / duckduckgo). NOT NULL + CHECK.
PRODUCER_STORE_CRAWLER = "mes_store_crawler_v1"
STORE_BATCH_SIZE = 3  # 每批 1–3 家(暫定起點,待戳店面實況回饋調整)
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


def harvest_store(
    domain: str, review_apps: dict[str, uuid.UUID], *, req_sleep: bool = True
) -> list[FeatureResult]:
    """Poke one store (products.json + homepage) and return the 9 FeatureResults."""
    with httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=20.0) as client:
        pf = fetch_products(domain, client, req_sleep=req_sleep)
        _sleep(req_sleep)
        html = fetch_homepage(domain, client)
    return parse_products_features(pf) + parse_homepage_features(html, review_apps)


# --- Batch runner (independent schedule; writes onto the store entity) --------


@dataclass(frozen=True)
class HarvestReport:
    batch_id: str
    stores: list[tuple[str, list[FeatureResult]]]  # (domain, 9 results)

    def format(self) -> str:
        lines = ["===== MES 市場 feature 撈取報告 (Store Harvest) =====",
                 f"批號 (batch_id): {self.batch_id}",
                 f"本批店數: {len(self.stores)}", ""]
        for domain, results in self.stores:
            obs = sum(r.status == "observed" for r in results)
            nf = sum(r.status == "not_found" for r in results)
            ff = sum(r.status == "fetch_failed" for r in results)
            got = {r.feature: r.value_raw for r in results if r.status == "observed"}
            lines.append(f"  {domain}: observed {obs}/9 · not_found {nf} · fetch_failed {ff}")
            lines.append(f"     {got}")
        lines.append("=" * 46)
        return "\n".join(lines)


async def _load_review_apps(session: AsyncSession) -> dict[str, uuid.UUID]:
    rows = await session.execute(
        select(Entity.canonical_key, Entity.entity_id).where(Entity.entity_type == "review_app")
    )
    return {key: eid for key, eid in rows.all()}


async def _select_stores_to_harvest(
    session: AsyncSession, limit: int
) -> list[tuple[uuid.UUID, str]]:
    """Stores with a domain whose harvest is pending/failed (no 'done' state yet)."""
    rows = await session.execute(
        select(Entity.entity_id, Entity.canonical_key)
        .outerjoin(StoreHarvestState, StoreHarvestState.entity_id == Entity.entity_id)
        .where(
            Entity.entity_type == "store",
            or_(
                StoreHarvestState.entity_id.is_(None),
                StoreHarvestState.status.in_(["pending", "failed"]),
            ),
        )
        .order_by(Entity.created_at)
        .limit(limit)
    )
    return [(eid, key) for eid, key in rows.all()]


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
        async with session_maker() as session:
            day_str = datetime.now(_TAIPEI).date().isoformat()
            batch_id = await _resolve_batch_id(session, day_str, None)
            review_apps = await _load_review_apps(session)
            targets = await _select_stores_to_harvest(session, batch_size)
            for i, (store_id, domain) in enumerate(targets):
                results = harvest_store(domain, review_apps, req_sleep=req_sleep)
                for r in results:
                    await _write_feature(session, store_id, batch_id, r)
                reachable = any(r.status != "fetch_failed" for r in results)
                await _upsert_state(session, store_id, "done" if reachable else "failed")
                await session.commit()
                stores_done.append((domain, results))
                if store_sleep and i < len(targets) - 1:
                    _sleep(True)
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
