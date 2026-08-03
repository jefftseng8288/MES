"""Phase 3 第一批:hypothesis / decision schema + predicate registry + pattern 查詢。

只測資料層 —— 不碰 LLMProvider / 假說生成 / 審核流程(那是第二批)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Decision, Entity, Hypothesis, InsightStore
from mes.hypothesis_registry import (
    PREDICATE_SWAP_APP_INTENT,
    PredicateError,
    register_predicate,
    registered_predicates,
    validate_predicate,
)
from mes.insight_producers import SKURuleProducer
from mes.patterns import PatternError, stores_matching_pattern, validate_pattern

_SKU = SKURuleProducer.insight_type  # "SKU_SCALE"


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _hyp(**overrides: Any) -> Hypothesis:
    base: dict[str, Any] = {
        "pattern": [{"insight_type": _SKU, "value_text": "High SKU"}],
        "predicted_outcome": PREDICATE_SWAP_APP_INTENT,
        "rationale": "這類店評論資產大,遷移成本高,對自動挽回提案應有反應。",
        "confidence": "inferred",
        "source_insight_refs": [{"insight_type": _SKU, "value_text": "High SKU"}],
        "model": "claude-opus-5",
        "prompt_version": "p1",
        "hypothesis_version": "h1",
        "status": "pending",
    }
    base.update(overrides)
    return Hypothesis(**base)


async def _expect_rejected(session: AsyncSession, obj: Any) -> None:
    session.add(obj)
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.commit()
    await session.rollback()


# --- hypothesis 表 -------------------------------------------------------------


async def test_hypothesis_writable(session: AsyncSession) -> None:
    h = _hyp()
    session.add(h)
    await session.commit()
    got = await session.get(Hypothesis, h.hypothesis_id)
    assert got is not None
    assert got.predicted_outcome == PREDICATE_SWAP_APP_INTENT
    assert got.status == "pending" and got.created_at is not None
    assert got.parent_hypothesis_id is None  # 演化鏈第一版不啟用


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "retired"])
async def test_all_four_statuses_writable(session: AsyncSession, status: str) -> None:
    """retired 是 Phase 5 用的值 —— 第一版只建值不啟用機制,但要寫得進去。"""
    session.add(_hyp(status=status))
    await session.commit()


async def test_illegal_status_rejected(session: AsyncSession) -> None:
    await _expect_rejected(session, _hyp(status="maybe"))


async def test_illegal_confidence_rejected(session: AsyncSession) -> None:
    await _expect_rejected(session, _hyp(confidence="very_sure"))


# --- ★ Provenance:上游引用不可為空 ---------------------------------------------


async def test_source_insight_refs_null_rejected(session: AsyncSession) -> None:
    await _expect_rejected(session, _hyp(source_insight_refs=None))


async def test_source_insight_refs_empty_array_rejected(session: AsyncSession) -> None:
    """★ NOT NULL 擋不住空陣列 —— 空引用等同沒有 Provenance,必須另外擋。"""
    await _expect_rejected(session, _hyp(source_insight_refs=[]))


async def test_empty_pattern_rejected(session: AsyncSession) -> None:
    """空 pattern = 「打所有店」,不是有意義的假說。"""
    await _expect_rejected(session, _hyp(pattern=[]))


# --- ★ predicted_outcome:應用層擋、DB 不擋 ------------------------------------


async def test_unregistered_predicate_passes_db_but_fails_app_layer(
    session: AsyncSession,
) -> None:
    """★ 確認 DB 層確實沒鎖 predicted_outcome(受控刻意放應用層)。"""
    bogus = "TOTALLY_MADE_UP_OUTCOME"
    session.add(_hyp(predicted_outcome=bogus))
    await session.commit()  # (a) DB 不擋
    got = await session.scalar(
        select(Hypothesis).where(Hypothesis.predicted_outcome == bogus)
    )
    assert got is not None
    with pytest.raises(PredicateError):  # (b) 應用層才是守門的
        validate_predicate(bogus)


def test_registered_predicate_passes() -> None:
    validate_predicate(PREDICATE_SWAP_APP_INTENT)
    assert PREDICATE_SWAP_APP_INTENT in registered_predicates()


def test_registry_starts_small_not_exhaustive() -> None:
    """★ 第一版只登記已確定會用的,不預先窮舉(合法值取決於未定的 Phase 4 武器)。"""
    assert registered_predicates() == (PREDICATE_SWAP_APP_INTENT,)


def test_register_rejects_blank() -> None:
    for bad in ("", "  ", " X "):
        with pytest.raises(PredicateError):
            register_predicate(bad)


# --- pattern 結構驗證 ----------------------------------------------------------


def test_valid_pattern_passes() -> None:
    conds = validate_pattern([{"insight_type": _SKU, "value_text": "High SKU"}])
    assert conds == [{"insight_type": _SKU, "value_text": "High SKU"}]


@pytest.mark.parametrize(
    "bad",
    [
        "not a list",
        [],
        ["not a dict"],
        [{"insight_type": _SKU}],  # 缺 value_text
        [{"value_text": "High SKU"}],  # 缺 insight_type
    ],
)
def test_malformed_pattern_rejected(bad: Any) -> None:
    with pytest.raises(PatternError):
        validate_pattern(bad)


def test_unregistered_insight_in_pattern_rejected() -> None:
    """★ 引用未登記的 insight → 擋。否則這條 pattern 永遠撈不到店,而且是靜默撈到 0 家。"""
    with pytest.raises(PatternError):
        validate_pattern([{"insight_type": "NEVER_REGISTERED", "value_text": "x"}])
    with pytest.raises(PatternError):
        validate_pattern([{"insight_type": _SKU, "value_text": "high_sku"}])  # 寫法不一致


# --- ★ pattern → 撈店(AND 組合)-----------------------------------------------


async def _store_with_insights(
    session: AsyncSession, labels: list[tuple[str, str]]
) -> uuid.UUID:
    """建一家店,並給它 SKU_SCALE + 一個測試用 insight_type 的標籤。"""
    store = Entity(entity_type="store", canonical_key=f"p3-{uuid.uuid4().hex}.com")
    session.add(store)
    await session.flush()
    for itype, value in labels:
        session.add(InsightStore(
            entity_id=store.entity_id, insight_type=itype, value_text=value,
            producer="rule_v1", confidence="certain", generated_at=datetime.now(UTC),
            source_knowledge_refs=[{"entity_id": str(store.entity_id), "feature": "product_count"}],
        ))
    await session.commit()
    return store.entity_id


async def test_pattern_and_semantics(session: AsyncSession) -> None:
    """★ 多條件都符合才撈出;只符合一條不撈。"""
    from mes.insight_registry import register_insight_type

    other = f"TEST_DIM_{uuid.uuid4().hex[:6]}"
    register_insight_type(other, ("Warning", "OK"))

    both = await _store_with_insights(session, [(_SKU, "High SKU"), (other, "Warning")])
    only_sku = await _store_with_insights(session, [(_SKU, "High SKU"), (other, "OK")])
    only_other = await _store_with_insights(session, [(_SKU, "Low SKU"), (other, "Warning")])

    pattern = [
        {"insight_type": _SKU, "value_text": "High SKU"},
        {"insight_type": other, "value_text": "Warning"},
    ]
    matched = await stores_matching_pattern(session, pattern)
    assert both in matched
    assert only_sku not in matched  # 只符合 SKU 那條
    assert only_other not in matched  # 只符合另一條


async def test_single_condition_pattern_matches(session: AsyncSession) -> None:
    from mes.insight_registry import register_insight_type

    dim = f"TEST_DIM_{uuid.uuid4().hex[:6]}"
    register_insight_type(dim, ("Yes", "No"))
    hit = await _store_with_insights(session, [(dim, "Yes")])
    miss = await _store_with_insights(session, [(dim, "No")])

    matched = await stores_matching_pattern(session, [{"insight_type": dim, "value_text": "Yes"}])
    assert hit in matched and miss not in matched


async def test_pattern_query_validates_before_running(session: AsyncSession) -> None:
    with pytest.raises(PatternError):
        await stores_matching_pattern(session, [{"insight_type": "NOPE", "value_text": "x"}])


# --- decision 表 ---------------------------------------------------------------


async def test_decision_chain_reject_retry_approve(session: AsyncSession) -> None:
    """★ 決策史:reject → retry → approve 串成一條鏈,可查出整條路徑。

    這正是「為什麼 Decision 要獨立成表」—— 記在 hypothesis 的單一欄位上只留得下
    最後一次結果,中間被 reject 兩次的過程整個消失。
    """
    h = _hyp()
    session.add(h)
    await session.commit()

    d1 = Decision(target_type="hypothesis", target_id=h.hypothesis_id,
                  actor="jeff", action="reject", reason="樣本太小,信心度不足")
    session.add(d1)
    await session.commit()
    d2 = Decision(parent_decision_id=d1.decision_id, target_type="hypothesis",
                  target_id=h.hypothesis_id, actor="jeff", action="comment",
                  reason="補充:等 GROWTH_VELOCITY 有資料再看")
    session.add(d2)
    await session.commit()
    d3 = Decision(parent_decision_id=d2.decision_id, target_type="hypothesis",
                  target_id=h.hypothesis_id, actor="jeff", action="approve",
                  reason="樣本補足,通過")
    session.add(d3)
    await session.commit()

    # 沿 parent 往回走,還原整條決策路徑
    chain, cur = [], d3
    while cur is not None:
        chain.append(cur.action)
        cur = (
            await session.get(Decision, cur.parent_decision_id)
            if cur.parent_decision_id else None
        )
    assert chain == ["approve", "comment", "reject"]


async def test_decision_illegal_action_rejected(session: AsyncSession) -> None:
    h = _hyp()
    session.add(h)
    await session.commit()
    await _expect_rejected(session, Decision(
        target_type="hypothesis", target_id=h.hypothesis_id, actor="jeff", action="maybe"))


async def test_decision_target_is_generic(session: AsyncSession) -> None:
    """★ 泛型指向:target_type 不鎖死 hypothesis(Decision Graph 是橫切概念)。"""
    session.add(Decision(target_type="experiment", target_id=uuid.uuid4(),
                         actor="jeff", action="comment", reason="未來的別種決策對象"))
    await session.commit()


async def test_decision_all_actions_writable(session: AsyncSession) -> None:
    for action in ("approve", "reject", "comment"):
        session.add(Decision(target_type="hypothesis", target_id=uuid.uuid4(),
                             actor="jeff", action=action))
    await session.commit()


# --- 確認沒有把第二批的東西做進來 ------------------------------------------------


async def test_parent_hypothesis_link_exists_but_unused(session: AsyncSession) -> None:
    """演化鏈只建欄位:可寫,但第一版沒有任何機制會自動填它。"""
    parent = _hyp()
    session.add(parent)
    await session.commit()
    child = _hyp(parent_hypothesis_id=parent.hypothesis_id, status="pending")
    session.add(child)
    await session.commit()
    got = await session.get(Hypothesis, child.hypothesis_id)
    assert got is not None and got.parent_hypothesis_id == parent.hypothesis_id


async def test_predicted_outcome_has_no_db_check(session: AsyncSession) -> None:
    """明確驗證:DB 層沒有任何 CHECK 綁 predicted_outcome。"""
    rows = await session.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'hypothesis'::regclass "
        "AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%predicted_outcome%'"
    ))
    assert rows.all() == []
