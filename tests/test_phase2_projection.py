"""Phase 2 投影引擎測試。

取值 / country 特例 / current_status / 決定 2 用純函數 project_row(記憶體 ObservationLog)測;
全量重建 / 冪等 / 從無 observed / 時間序列對真實 DB 測。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, KnowledgeState, ObservationLog
from mes.knowledge import feature_history, project_row, rebuild_knowledge_state

_BATCH = "2099-01-01-01"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _obs(feature: str, *, status: str, at: datetime, confidence: str = "certain",
         value_number: float | None = None, value_text: str | None = None,
         value_type: str = "number", entity_id: Any = None) -> ObservationLog:
    """In-memory ObservationLog (observation_id set explicitly = the deterministic tiebreaker)."""
    return ObservationLog(
        observation_id=uuid.uuid4(), entity_id=entity_id or uuid.uuid4(), feature=feature,
        value_type=value_type,
        value_raw=(str(value_number) if value_number is not None else value_text),
        value_number=value_number, value_text=value_text,
        source="products_json", producer="mes_crawler_v1", observed_at=at,
        confidence=confidence, status=status, batch_id=_BATCH,
    )


# --- 取值:時間優先 + tiebreaker(純函數)---------------------------------------


def test_time_priority_new_inferred_beats_old_certain() -> None:
    eid = uuid.uuid4()
    old = _obs("product_count", status="observed", at=_T0, confidence="certain", value_number=100,
               entity_id=eid)
    new = _obs("product_count", status="observed", at=_T0 + timedelta(days=30),
               confidence="inferred", value_number=200, entity_id=eid)
    ks = project_row(eid, "product_count", [old, new])
    assert ks is not None
    # 較新 inferred 贏較舊 certain(決定 1:時間優先於信心度)
    assert ks.value_number == 200 and ks.confidence == "inferred"
    assert ks.observed_at == new.observed_at and ks.selection_rule_version == "default_v1"


def test_same_time_confidence_tiebreaker() -> None:
    eid = uuid.uuid4()
    a = _obs("product_count", status="observed", at=_T0, confidence="inferred", value_number=1,
             entity_id=eid)
    b = _obs("product_count", status="observed", at=_T0, confidence="certain", value_number=2,
             entity_id=eid)
    ks = project_row(eid, "product_count", [a, b])
    assert ks is not None and ks.value_number == 2 and ks.confidence == "certain"


# --- 決定 2:fetch_failed 保留舊值(純函數)-------------------------------------


def test_decision2_fetch_failed_keeps_old_value() -> None:
    eid = uuid.uuid4()
    old = _obs("product_count", status="observed", at=_T0, value_number=196, entity_id=eid)
    fail = _obs("product_count", status="fetch_failed", at=_T0 + timedelta(days=180),
                confidence="certain", entity_id=eid)
    fail.value_raw = None  # failed row carries no value
    ks = project_row(eid, "product_count", [old, fail])
    assert ks is not None
    assert ks.value_number == 196 and ks.observed_at == old.observed_at  # 保留舊值 + 舊新鮮度
    assert ks.current_status == "fetch_failed"  # 誠實標明最近一次嘗試失敗


# --- 從無 observed:不投影列(純函數)-------------------------------------------


def test_never_observed_returns_none() -> None:
    eid = uuid.uuid4()
    f1 = _obs("product_count", status="fetch_failed", at=_T0, entity_id=eid)
    f2 = _obs("product_count", status="not_found", at=_T0 + timedelta(days=1), entity_id=eid)
    for f in (f1, f2):
        f.value_raw = None
    assert project_row(eid, "product_count", [f1, f2]) is None


# --- current_status 掃不同子集(純函數)----------------------------------------


def test_current_status_from_latest_attempt_not_value() -> None:
    eid = uuid.uuid4()
    obs = _obs("product_count", status="observed", at=_T0, value_number=50, entity_id=eid)
    nf = _obs("product_count", status="not_found", at=_T0 + timedelta(days=1), entity_id=eid)
    ff = _obs("product_count", status="fetch_failed", at=_T0 + timedelta(days=2), entity_id=eid)
    for x in (nf, ff):
        x.value_raw = None
    ks = project_row(eid, "product_count", [obs, nf, ff])
    assert ks is not None
    # value 來自唯一的 observed(t0);current_status 來自全部最新那筆(t2 fetch_failed)
    assert ks.value_number == 50 and ks.current_status == "fetch_failed"


# --- country 特例(純函數)-----------------------------------------------------


def test_country_new_inferred_does_not_override_old_certain() -> None:
    eid = uuid.uuid4()
    old = _obs("country", status="observed", at=_T0, confidence="certain", value_text="US",
               value_type="string", entity_id=eid)
    new = _obs("country", status="observed", at=_T0 + timedelta(days=30), confidence="inferred",
               value_text="CA", value_type="string", entity_id=eid)
    ks = project_row(eid, "country", [old, new])
    assert ks is not None
    # 新 inferred 不覆蓋舊 certain(剛性事實不被猜測污染)
    assert ks.value_text == "US" and ks.confidence == "certain"
    assert ks.selection_rule_version == "country_v1"


def test_country_new_certain_overrides_old_certain() -> None:
    eid = uuid.uuid4()
    old = _obs("country", status="observed", at=_T0, confidence="certain", value_text="US",
               value_type="string", entity_id=eid)
    new = _obs("country", status="observed", at=_T0 + timedelta(days=30), confidence="certain",
               value_text="UK", value_type="string", entity_id=eid)
    ks = project_row(eid, "country", [old, new])
    assert ks is not None
    # 同 confidence → 時間優先取新
    assert ks.value_text == "UK"


# --- P2 中立:投影結果無評分/判斷/排序欄 -------------------------------------


def test_no_scoring_columns() -> None:
    cols = set(KnowledgeState.__table__.columns.keys())
    banned = {"score", "rank", "priority", "grade", "rating", "judgement", "recommendation"}
    assert cols.isdisjoint(banned)


# --- 全量重建 / 冪等 / 從無 observed→無列 / 時間序列(真實 DB)-------------------


async def _persist_store_with_obs(
    session: AsyncSession, obs_specs: list[dict[str, Any]]
) -> uuid.UUID:
    store = Entity(entity_type="store", canonical_key=f"proj-{uuid.uuid4().hex}.com")
    session.add(store)
    await session.flush()
    for spec in obs_specs:
        session.add(ObservationLog(
            entity_id=store.entity_id, batch_id=_BATCH, source="products_json",
            producer="mes_crawler_v1", **spec))
    await session.commit()
    return store.entity_id


async def test_rebuild_idempotent_and_projects_expected(session: AsyncSession) -> None:
    eid = await _persist_store_with_obs(session, [
        dict(feature="product_count", value_type="number", value_raw="10", value_number=10,
             observed_at=_T0, confidence="certain", status="observed"),
        dict(feature="product_count", value_type="number", value_raw="20", value_number=20,
             observed_at=_T0 + timedelta(days=1), confidence="certain", status="observed"),
    ])

    def snapshot(rows: list[KnowledgeState]) -> dict[tuple[Any, str], tuple[Any, ...]]:
        return {(r.entity_id, r.feature): (r.value_raw, r.value_number, r.observed_at,
                                           r.current_status, r.selection_rule_version,
                                           r.source_observation_id, r.updated_at) for r in rows}

    await rebuild_knowledge_state(session)
    first = snapshot((await session.execute(select(KnowledgeState))).scalars().all())
    session.expunge_all()
    await rebuild_knowledge_state(session)
    second = snapshot((await session.execute(select(KnowledgeState))).scalars().all())
    # 連跑兩次完全一致(純函數 / 冪等 / 砍表重建一致)
    assert first == second
    # 我們這家:取最新那筆(20)
    assert first[(eid, "product_count")][1] == 20


async def test_never_observed_store_has_no_row(session: AsyncSession) -> None:
    eid = await _persist_store_with_obs(session, [
        dict(feature="product_count", value_type="number", value_raw=None,
             observed_at=_T0, confidence="certain", status="fetch_failed"),
    ])
    await rebuild_knowledge_state(session)
    assert await session.get(KnowledgeState, (eid, "product_count")) is None


async def test_feature_history_sorted(session: AsyncSession) -> None:
    eid = await _persist_store_with_obs(session, [
        dict(feature="product_count", value_type="number", value_raw="30", value_number=30,
             observed_at=_T0 + timedelta(days=2), confidence="certain", status="observed"),
        dict(feature="product_count", value_type="number", value_raw="10", value_number=10,
             observed_at=_T0, confidence="certain", status="observed"),
        dict(feature="product_count", value_type="number", value_raw=None,
             observed_at=_T0 + timedelta(days=1), confidence="certain", status="fetch_failed"),
    ])
    hist = await feature_history(session, eid, "product_count")
    # 只回 observed、依 observed_at 排序(fetch_failed 那筆不在時間序列)
    assert [o.value_number for o in hist] == [10, 30]
