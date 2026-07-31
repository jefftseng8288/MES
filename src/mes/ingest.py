"""Event-sourcing write chain — the double-domino (Phase 1-C).

Deterministic: these functions take already-obtained inputs (a scraped Store Name,
an inferred domain, or a failure status) and write Observations. No network here —
scraping/inference live in ``scrape.py`` / ``inference.py``. Every write goes through
Observation_Log first (Append-Only, entity_id NOT NULL); Knowledge_State projection
is a later phase.

骨牌一 (Seed): scraping a Store Name -> a store_name_seed entity + an
              ``observed_on_app_store`` observation (certain — Reality on the page).
骨牌二 (Domain): inferring a domain from the Seed ->
   情況 A success        : a store entity + ``inferred_domain`` observation
                           (entity_ref, inferred).
   情況 B fetch_failed   : inference didn't execute (429/timeout/HTML broke) -> all
                           value columns NULL, status='fetch_failed'.
   情況 B not_found      : inference ran but found no trustworthy domain -> all value
                           columns NULL, status='not_found'. This means "with the
                           current provider we could not find a domain", NOT "the
                           store is dead".
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mes.db.models import Entity, ObservationLog
from mes.normalize import normalize_domain, seed_key

# producer = the method/model responsible for a value (goes in the NOT NULL producer
# column). Distinct from a P6 "provider" (external data source).
PRODUCER_CRAWLER = "mes_crawler_v1"
PRODUCER_DUCKDUCKGO = "duckduckgo_v1"
PRODUCER_MANUAL = "manual_v1"

FEATURE_OBSERVED_ON_APP_STORE = "observed_on_app_store"
FEATURE_INFERRED_DOMAIN = "inferred_domain"

# source = the channel a value was observed through. inferred_domain comes via a web
# search, so its source is 'web_search' (recording html_page would lie about provenance).
SOURCE_INFERENCE = "web_search"


def _now() -> datetime:
    return datetime.now(UTC)


async def _get_or_create_entity(
    session: AsyncSession, entity_type: str, canonical_key: str
) -> tuple[Entity, bool]:
    """Return (entity, created). Dedup on (entity_type, canonical_key)."""
    existing = await session.scalar(
        select(Entity).where(
            Entity.entity_type == entity_type, Entity.canonical_key == canonical_key
        )
    )
    if existing is not None:
        return existing, False
    entity = Entity(entity_type=entity_type, canonical_key=canonical_key)
    session.add(entity)
    await session.flush()
    return entity, True


def normalize_source_label(label: str) -> str:
    """來源標記的**單一正規化入口**:小寫 + 收斂空白。寫入前一律過這裡。"""
    return " ".join(label.split()).lower()


async def ingest_seed(
    session: AsyncSession,
    raw_store_name: str,
    *,
    batch_id: str,
    source_page_label: str = "unknown review page",
) -> Entity:
    """骨牌一: upsert the Seed entity and append an observed_on_app_store observation.

    The Seed entity dedupes on canonical_key ('seed:' + normalized name); the
    observation is Append-Only (re-seeing a name appends a new row). ``batch_id``
    records which run produced it (Provenance 延伸).

    ``source_page_label`` 記「這個 Seed 從哪個來源撈到」—— 未來分析「哪個來源的店家品質好」
    的依據。**格式由本函式統一正規化(小寫 + 去多餘空白),不依賴呼叫端自律** ——
    曾因舊的預設值 "Loox Review Page" 與呼叫端傳的 "loox review page" 並存,
    讓同一個來源在統計上被拆成兩個(同 producer 大小寫問題)。
    """
    seed, _ = await _get_or_create_entity(
        session, "store_name_seed", seed_key(raw_store_name)
    )
    session.add(
        ObservationLog(
            entity_id=seed.entity_id,
            feature=FEATURE_OBSERVED_ON_APP_STORE,
            value_type="string",
            value_raw=raw_store_name,
            value_text=normalize_source_label(source_page_label),
            source="html_page",
            producer=PRODUCER_CRAWLER,
            observed_at=_now(),
            confidence="certain",
            status="observed",
            batch_id=batch_id,
        )
    )
    await session.flush()
    return seed


async def ingest_inferred_domain_success(
    session: AsyncSession,
    seed: Entity,
    *,
    raw_url: str,
    domain: str,
    batch_id: str,
    producer: str = PRODUCER_DUCKDUCKGO,
) -> Entity:
    """骨牌二 情況 A: create/get the store entity and log the inferred_domain (inferred)."""
    store, _ = await _get_or_create_entity(session, "store", normalize_domain(domain))
    session.add(
        ObservationLog(
            entity_id=seed.entity_id,
            feature=FEATURE_INFERRED_DOMAIN,
            value_type="entity_ref",
            value_raw=raw_url,
            value_entity_id=store.entity_id,
            source=SOURCE_INFERENCE,
            producer=producer,
            observed_at=_now(),
            confidence="inferred",
            status="observed",
            batch_id=batch_id,
        )
    )
    await session.flush()
    return store


async def ingest_inferred_domain_failure(
    session: AsyncSession,
    seed: Entity,
    *,
    status: str,
    batch_id: str,
    producer: str = PRODUCER_DUCKDUCKGO,
) -> ObservationLog:
    """骨牌二 情況 B: log a failed inference (fetch_failed | not_found), all values NULL.

    value_type stays 'entity_ref' (the feature's expected type); every typed column
    and value_raw are NULL, per the failed-branch CHECK contract.
    """
    if status not in ("fetch_failed", "not_found"):
        raise ValueError(f"failure status must be fetch_failed|not_found, got {status!r}")
    obs = ObservationLog(
        entity_id=seed.entity_id,
        feature=FEATURE_INFERRED_DOMAIN,
        value_type="entity_ref",
        value_raw=None,
        source=SOURCE_INFERENCE,
        producer=producer,
        observed_at=_now(),
        confidence="inferred",
        status=status,
        batch_id=batch_id,
    )
    session.add(obs)
    await session.flush()
    return obs
