"""Phase 2 knowledge_state schema 的 DB CHECK 驗證(真連 DB)。

第二批已把第一批的 last_observed_at 併回 observed_at,故 value 的閘門欄 = observed_at:
規則 1:observed_at IS NULL → value 全 NULL、current_status ∈ fetch_failed/not_found。
規則 2:observed_at IS NOT NULL → value 必非 NULL(discriminated union)。
current_status 受控三值。

只測 schema/CHECK —— 投影取值邏輯見 test_phase2_projection.py。
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
from mes.db.models import Entity, KnowledgeState, ObservationLog


def _now() -> datetime:
    return datetime.now(UTC)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _store_and_obs(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """A fresh store + one observation (for entity_id / source_observation_id FKs)."""
    store = Entity(entity_type="store", canonical_key=f"p2-{uuid.uuid4().hex}.com")
    session.add(store)
    await session.flush()
    obs = ObservationLog(
        entity_id=store.entity_id, feature="product_count", value_type="number",
        value_raw="42", value_number=42, source="products_json", producer="mes_crawler_v1",
        observed_at=_now(), confidence="certain", status="observed", batch_id="2099-01-01-01",
    )
    session.add(obs)
    await session.commit()
    return store.entity_id, obs.observation_id


def _ks(entity_id: uuid.UUID, obs_id: uuid.UUID, **overrides: Any) -> KnowledgeState:
    """Default = a valid rule-2 row (has value, observed_at set, current_status observed)."""
    base: dict[str, Any] = {
        "entity_id": entity_id, "feature": "product_count", "value_type": "number",
        "value_raw": "42", "value_number": 42, "producer": "mes_crawler_v1",
        "source_observation_id": obs_id, "observed_at": _now(), "confidence": "certain",
        "selection_rule_version": "v1", "current_status": "observed",
    }
    base.update(overrides)
    return KnowledgeState(**base)


async def _expect_rejected(session: AsyncSession, obj: Any) -> None:
    session.add(obj)
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.commit()
    await session.rollback()


# --- 規則 1:observed_at IS NULL(防禦守門;v1 投影不建此列)------------------------


async def test_rule1_null_observed_at_but_has_value_rejected(session: AsyncSession) -> None:
    eid, obs = await _store_and_obs(session)
    # observed_at NULL 但硬塞 value → 被 value CHECK 拒
    await _expect_rejected(session, _ks(eid, obs, observed_at=None, current_status="fetch_failed"))


async def test_rule1_null_observed_at_current_status_observed_rejected(
    session: AsyncSession,
) -> None:
    eid, obs = await _store_and_obs(session)
    # observed_at NULL(value 也清空)但 current_status=observed → 被 consistency CHECK 拒
    await _expect_rejected(
        session,
        _ks(eid, obs, observed_at=None, value_raw=None, value_number=None,
            current_status="observed"),
    )


# 註:v1 不建「無值列」(observed_at 保持 NOT NULL,保留 Provenance 鐵律),故規則 1 的
# observed_at IS NULL 分支不可達,為防禦守門 —— 上面兩個 rejected 測試即證 observed_at=NULL 被擋。


# --- 規則 2:observed_at IS NOT NULL ---------------------------------------------


async def test_rule2_has_observed_at_but_no_value_rejected(session: AsyncSession) -> None:
    eid, obs = await _store_and_obs(session)
    # observed_at 有值但 value 全 NULL → 被 value CHECK 拒(防「曾成功卻沒值」的空洞)
    await _expect_rejected(
        session, _ks(eid, obs, value_raw=None, value_number=None, current_status="observed")
    )


async def test_rule2_legal_fetch_failed_keeps_old_value_writable(session: AsyncSession) -> None:
    eid, obs = await _store_and_obs(session)
    # 決定 2 主場景:值還在(半年前 observed)+ 今天 fetch_failed → 合法
    ks = _ks(eid, obs, value_raw="196", value_number=196, current_status="fetch_failed")
    session.add(ks)
    await session.commit()
    got = await session.get(KnowledgeState, (eid, "product_count"))
    assert got is not None and got.value_number == 196 and got.current_status == "fetch_failed"
    assert got.observed_at is not None


# --- current_status 受控 --------------------------------------------------------


async def test_current_status_illegal_rejected(session: AsyncSession) -> None:
    eid, obs = await _store_and_obs(session)
    await _expect_rejected(session, _ks(eid, obs, current_status="broken"))


async def test_current_status_all_three_legal_writable(session: AsyncSession) -> None:
    # 三值都可掛在「有值」的列上:observed(現在也成功)/ fetch_failed(以前成功這次失敗)/
    # not_found(以前成功這次確認沒了)—— 都是合法 rule-2(有 value + observed_at)。
    for cur in ("observed", "fetch_failed", "not_found"):
        eid, obs = await _store_and_obs(session)
        session.add(_ks(eid, obs, current_status=cur))
        await session.commit()
        rows = (
            await session.execute(
                select(KnowledgeState.current_status).where(KnowledgeState.entity_id == eid)
            )
        ).scalars().all()
        assert rows == [cur]
