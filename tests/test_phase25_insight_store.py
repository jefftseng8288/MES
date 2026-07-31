"""Phase 2.5 第一批:insight_store schema + 應用層 value 受控機制。

DB 層測:寫入 / UNIQUE(entity_id, insight_type)/ NOT NULL / source_knowledge_refs 結構
        / **確認 value_text 沒有 DB CHECK**(刻意不下沉,擋在應用層)。
應用層測:registry 登記 + validate_insight_value 守門。

只測資料層 —— 不碰 InsightEngine / Producer / 排程(那是第二批)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, InsightStore
from mes.insight_producers import SKURuleProducer
from mes.insight_registry import (
    InsightValueError,
    allowed_values,
    register_insight_type,
    registered_types,
    validate_insight_value,
)

# SKU_SCALE 由 SKURuleProducer 自己聲明(第二批:Producer 向 registry 登記)。
INSIGHT_TYPE_SKU_SCALE = SKURuleProducer.insight_type


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _make_store(session: AsyncSession) -> uuid.UUID:
    store = Entity(entity_type="store", canonical_key=f"insight-{uuid.uuid4().hex}.com")
    session.add(store)
    await session.commit()
    return store.entity_id


def _insight(entity_id: uuid.UUID, **overrides: Any) -> InsightStore:
    base: dict[str, Any] = {
        "entity_id": entity_id,
        "insight_type": INSIGHT_TYPE_SKU_SCALE,
        "value_text": "High SKU",
        "producer": "rule_v1",
        "confidence": "certain",
        "generated_at": datetime.now(UTC),
        "source_knowledge_refs": [{"entity_id": str(entity_id), "feature": "product_count"}],
    }
    base.update(overrides)
    return InsightStore(**base)


# --- DB:寫入 + Provenance 結構 -------------------------------------------------


async def test_insight_row_writable_with_refs(session: AsyncSession) -> None:
    eid = await _make_store(session)
    session.add(_insight(eid))
    await session.commit()

    got = await session.scalar(select(InsightStore).where(InsightStore.entity_id == eid))
    assert got is not None
    assert got.insight_type == INSIGHT_TYPE_SKU_SCALE and got.value_text == "High SKU"
    assert got.producer == "rule_v1" and got.confidence == "certain"
    assert got.insight_id is not None and got.generated_at is not None
    # source_knowledge_refs 存取 (entity_id, feature) 結構
    assert got.source_knowledge_refs == [{"entity_id": str(eid), "feature": "product_count"}]


# --- DB:UNIQUE (entity_id, insight_type) ---------------------------------------


async def test_same_entity_same_type_rejected(session: AsyncSession) -> None:
    eid = await _make_store(session)
    session.add(_insight(eid))
    await session.commit()
    # 同一 (entity_id, insight_type) 第二列 → 被 UNIQUE 拒(一維度只有一個當前值)
    session.add(_insight(eid, value_text="Low SKU"))
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.commit()
    await session.rollback()


async def test_same_entity_different_type_allowed(session: AsyncSession) -> None:
    eid = await _make_store(session)
    session.add(_insight(eid))
    # 不同 insight_type → 同一 entity 可多列(GROWTH_VELOCITY 僅作 DB 層鍵測試用,
    # 其合法值集合待第二批 Producer 登記,此處不經應用層驗證)
    session.add(_insight(eid, insight_type="GROWTH_VELOCITY", value_text="Growth",
                         producer="stat_v1"))
    await session.commit()
    rows = (
        await session.execute(select(InsightStore).where(InsightStore.entity_id == eid))
    ).scalars().all()
    assert len(rows) == 2
    assert {r.insight_type for r in rows} == {INSIGHT_TYPE_SKU_SCALE, "GROWTH_VELOCITY"}


# --- DB:NOT NULL ---------------------------------------------------------------


async def test_source_knowledge_refs_not_null_rejected(session: AsyncSession) -> None:
    eid = await _make_store(session)
    session.add(_insight(eid, source_knowledge_refs=None))
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.commit()
    await session.rollback()


async def test_producer_not_null_rejected(session: AsyncSession) -> None:
    eid = await _make_store(session)
    session.add(_insight(eid, producer=None))
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.commit()
    await session.rollback()


async def test_confidence_illegal_rejected(session: AsyncSession) -> None:
    # confidence 沿用 Phase 0 既定三級(穩定)→ 有 DB CHECK
    eid = await _make_store(session)
    session.add(_insight(eid, confidence="very_sure"))
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.commit()
    await session.rollback()


# --- ★ value_text 刻意沒有 DB CHECK(擋在應用層)--------------------------------


async def test_unregistered_value_passes_db_but_fails_app_layer(session: AsyncSession) -> None:
    eid = await _make_store(session)
    bogus = "totally_made_up_label"
    # (a) DB 層不擋:直接寫入未登記的 value → 成功(證明沒下沉 DB CHECK)
    session.add(_insight(eid, value_text=bogus))
    await session.commit()
    got = await session.scalar(select(InsightStore).where(InsightStore.entity_id == eid))
    assert got is not None and got.value_text == bogus
    # (b) 應用層才是守門的:同一個值過不了驗證
    with pytest.raises(InsightValueError):
        validate_insight_value(INSIGHT_TYPE_SKU_SCALE, bogus)


# --- 應用層 registry + 驗證 -----------------------------------------------------


def test_validate_accepts_registered_value() -> None:
    for v in ("High SKU", "Medium SKU", "Low SKU"):
        validate_insight_value(INSIGHT_TYPE_SKU_SCALE, v)  # 不 raise 即通過


def test_validate_rejects_wrong_case_or_spelling() -> None:
    # 受控的重點:防不一致寫法(high_sku / HIGH)被當成不同東西
    for v in ("high_sku", "HIGH SKU", "High  SKU"):
        with pytest.raises(InsightValueError):
            validate_insight_value(INSIGHT_TYPE_SKU_SCALE, v)


def test_validate_rejects_unregistered_type() -> None:
    # 未登記的 insight_type 一樣擋(沒登記 ≠ 放行)
    with pytest.raises(InsightValueError):
        validate_insight_value("NEVER_REGISTERED_TYPE", "anything")


def test_register_and_query_roundtrip() -> None:
    t = f"TEST_TYPE_{uuid.uuid4().hex[:8]}"
    register_insight_type(t, ("A", "B"))
    assert allowed_values(t) == frozenset({"A", "B"})
    assert t in registered_types()
    validate_insight_value(t, "A")
    with pytest.raises(InsightValueError):
        validate_insight_value(t, "C")


def test_reregister_same_values_ok_conflicting_rejected() -> None:
    t = f"TEST_TYPE_{uuid.uuid4().hex[:8]}"
    register_insight_type(t, ("A", "B"))
    register_insight_type(t, ("B", "A"))  # 同一集合、重複登記 → 允許(冪等)
    with pytest.raises(InsightValueError):  # 同維度不同值集合 → 擋(不可各說各話)
        register_insight_type(t, ("A", "C"))


def test_register_empty_values_rejected() -> None:
    with pytest.raises(InsightValueError):
        register_insight_type(f"TEST_TYPE_{uuid.uuid4().hex[:8]}", ())
