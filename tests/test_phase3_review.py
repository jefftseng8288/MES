"""Phase 3 第二批-B:審核 —— 三個動作 + Decision Graph 串鏈 + 頁面渲染。

核心邏輯(`apply_decision`)與 HTTP 無關,可獨立測試 —— 這也是它被切出來的理由。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Decision, Entity, Hypothesis, InsightStore
from mes.hypothesis_registry import PREDICATE_SWAP_APP_INTENT
from mes.insight_producers import SKURuleProducer
from mes.review import (
    ReviewError,
    apply_decision,
    decision_history,
    describe_pattern,
    latest_decision,
    load_views,
    render_page,
)

_SKU = SKURuleProducer.insight_type


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _make_hyp(session: AsyncSession, **over: Any) -> Hypothesis:
    base: dict[str, Any] = {
        "pattern": [{"insight_type": _SKU, "value_text": "High SKU"}],
        "predicted_outcome": PREDICATE_SWAP_APP_INTENT,
        "rationale": "評論資產大、遷移成本高。",
        "confidence": "inferred",
        "source_insight_refs": [{"insight_type": _SKU, "value_text": "High SKU"}],
        "model": "claude-opus-5", "prompt_version": "hypothesis_v1",
        "hypothesis_version": "h1", "status": "pending",
    }
    base.update(over)
    h = Hypothesis(**base)
    session.add(h)
    await session.commit()
    return h


# --- 三個動作 -------------------------------------------------------------------


async def test_approve_sets_status_and_writes_decision(session: AsyncSession) -> None:
    h = await _make_hyp(session)
    d = await apply_decision(session, h.hypothesis_id, "approve", "樣本足夠")
    await session.refresh(h)
    assert h.status == "approved"
    assert d.action == "approve" and d.actor == "jeff"
    assert d.target_type == "hypothesis" and d.target_id == h.hypothesis_id


async def test_reject_sets_status_reason_and_decision(session: AsyncSession) -> None:
    h = await _make_hyp(session)
    await apply_decision(session, h.hypothesis_id, "reject", "樣本太小")
    await session.refresh(h)
    assert h.status == "rejected"
    assert h.rejection_reason == "樣本太小"
    hist = await decision_history(session, h.hypothesis_id)
    assert [d.action for d in hist] == ["reject"]


@pytest.mark.parametrize("blank", ["", "   ", None])
async def test_reject_requires_reason(session: AsyncSession, blank: str | None) -> None:
    """★ Reject 必須填理由 —— 理由是未來演化的素材,空的等於沒記。"""
    h = await _make_hyp(session)
    with pytest.raises(ReviewError, match="必須填理由"):
        await apply_decision(session, h.hypothesis_id, "reject", blank)
    await session.refresh(h)
    assert h.status == "pending"  # 沒被改動


async def test_comment_keeps_status_pending(session: AsyncSession) -> None:
    """★★ 這條最容易寫錯:Comment 是純註記,**不可改 status**。"""
    h = await _make_hyp(session)
    await apply_decision(session, h.hypothesis_id, "comment", "等 GROWTH_VELOCITY 有資料再看")
    await session.refresh(h)
    assert h.status == "pending"  # 維持待審
    assert h.rejection_reason is None
    hist = await decision_history(session, h.hypothesis_id)
    assert [d.action for d in hist] == ["comment"]
    assert hist[0].reason == "等 GROWTH_VELOCITY 有資料再看"


async def test_comment_without_reason_allowed(session: AsyncSession) -> None:
    """Comment 可以不填(只是留個記號);只有 reject 強制要理由。"""
    h = await _make_hyp(session)
    await apply_decision(session, h.hypothesis_id, "comment", "")
    await session.refresh(h)
    assert h.status == "pending"


async def test_unknown_action_rejected(session: AsyncSession) -> None:
    h = await _make_hyp(session)
    with pytest.raises(ReviewError):
        await apply_decision(session, h.hypothesis_id, "delete", "x")


async def test_missing_hypothesis_rejected(session: AsyncSession) -> None:
    with pytest.raises(ReviewError, match="找不到假說"):
        await apply_decision(session, uuid.uuid4(), "approve", "x")


# --- ★ Decision Graph 串鏈 -------------------------------------------------------


async def test_parent_decision_chains(session: AsyncSession) -> None:
    """★ 後續決策的 parent 指向該假說**上一次**的 decision(comment → reject 要串起來)。"""
    h = await _make_hyp(session)
    d1 = await apply_decision(session, h.hypothesis_id, "comment", "先觀察")
    d2 = await apply_decision(session, h.hypothesis_id, "comment", "再想想")
    d3 = await apply_decision(session, h.hypothesis_id, "reject", "還是不行")

    assert d1.parent_decision_id is None  # 根決策無父
    assert d2.parent_decision_id == d1.decision_id
    assert d3.parent_decision_id == d2.decision_id

    # 沿 parent 往回走可還原整條路徑
    chain, cur = [], d3
    while cur is not None:
        chain.append(cur.action)
        cur = (
            await session.get(Decision, cur.parent_decision_id)
            if cur.parent_decision_id else None
        )
    assert chain == ["reject", "comment", "comment"]


async def test_chains_are_per_hypothesis(session: AsyncSession) -> None:
    """不同假說的決策鏈互不串接(parent 只看同一條假說的歷史)。"""
    h1, h2 = await _make_hyp(session), await _make_hyp(session)
    await apply_decision(session, h1.hypothesis_id, "comment", "a")
    d2 = await apply_decision(session, h2.hypothesis_id, "comment", "b")
    assert d2.parent_decision_id is None  # h2 的第一筆,不該接到 h1 的


async def test_latest_decision_returns_most_recent(session: AsyncSession) -> None:
    h = await _make_hyp(session)
    assert await latest_decision(session, h.hypothesis_id) is None
    await apply_decision(session, h.hypothesis_id, "comment", "one")
    d2 = await apply_decision(session, h.hypothesis_id, "comment", "two")
    latest = await latest_decision(session, h.hypothesis_id)
    assert latest is not None and latest.decision_id == d2.decision_id


# --- pattern 翻成人話 + 撈店家數 --------------------------------------------------


def test_describe_pattern_is_human_readable() -> None:
    """★ pattern 要翻成人看得懂的形式,不是直接倒 JSON。"""
    out = describe_pattern([
        {"insight_type": "SKU_SCALE", "value_text": "Low SKU"},
        {"insight_type": "RATING", "value_text": "Warning"},
    ])
    assert out == "SKU_SCALE = Low SKU  且  RATING = Warning"
    assert "{" not in out and "insight_type" not in out


def test_describe_pattern_handles_empty() -> None:
    assert describe_pattern([]) == "(無條件)"
    assert describe_pattern(None) == "(無條件)"


async def test_load_views_counts_matching_stores(session: AsyncSession) -> None:
    """★ 顯示「這條假說會打到幾家店」—— 樣本夠不夠是審核的關鍵判斷依據。"""
    from mes.insight_registry import register_insight_type

    dim = f"TEST_REV_{uuid.uuid4().hex[:6]}"
    register_insight_type(dim, ("Hit", "Miss"))
    for value in ("Hit", "Hit", "Miss"):
        e = Entity(entity_type="store", canonical_key=f"rev-{uuid.uuid4().hex}.com")
        session.add(e)
        await session.flush()
        session.add(InsightStore(
            entity_id=e.entity_id, insight_type=dim, value_text=value, producer="rule_v1",
            confidence="certain", generated_at=datetime.now(UTC),
            source_knowledge_refs=[{"entity_id": str(e.entity_id), "feature": "product_count"}]))
    await session.commit()

    h = await _make_hyp(session, pattern=[{"insight_type": dim, "value_text": "Hit"}])
    views = await load_views(session, "pending")
    v = next(v for v in views if v.hypothesis.hypothesis_id == h.hypothesis_id)
    assert v.store_count == 2  # 只有兩家是 Hit


async def test_reviewed_hypotheses_still_viewable(session: AsyncSession) -> None:
    """★ 已審核的仍要能看到 —— 否則審完就消失,無法回顧自己的決定。"""
    h = await _make_hyp(session)
    await apply_decision(session, h.hypothesis_id, "approve", "ok")
    approved = await load_views(session, "approved")
    assert any(v.hypothesis.hypothesis_id == h.hypothesis_id for v in approved)
    all_views = await load_views(session, None)
    assert any(v.hypothesis.hypothesis_id == h.hypothesis_id for v in all_views)
    pending = await load_views(session, "pending")
    assert all(v.hypothesis.hypothesis_id != h.hypothesis_id for v in pending)


# --- 頁面渲染 -------------------------------------------------------------------


async def test_page_shows_all_required_fields(session: AsyncSession) -> None:
    h = await _make_hyp(session, rationale="這是完整的推論鏈說明。")
    views = [v for v in await load_views(session, "pending")
             if v.hypothesis.hypothesis_id == h.hypothesis_id]
    page = render_page(views, "pending")
    for expected in (
        f"{_SKU} = High SKU",          # pattern 翻成人話
        PREDICATE_SWAP_APP_INTENT,      # 預測
        "inferred",                     # confidence
        "這是完整的推論鏈說明。",         # rationale 完整顯示
        "claude-opus-5", "hypothesis_v1", "h1",  # 版本(P5 可追溯)
        "家店符合",                      # 店家數
        "Evidence",                     # Provenance
        "Approve", "Reject", "Comment",  # 三個動作
    ):
        assert expected in page, expected


async def test_page_shows_decision_history(session: AsyncSession) -> None:
    h = await _make_hyp(session)
    await apply_decision(session, h.hypothesis_id, "comment", "之前留過的想法")
    views = [v for v in await load_views(session, "pending")
             if v.hypothesis.hypothesis_id == h.hypothesis_id]
    page = render_page(views, "pending")
    assert "決策史" in page and "之前留過的想法" in page


async def test_page_escapes_html(session: AsyncSession) -> None:
    """rationale 是 LLM 產生的文字 —— 必須逃脫,不可直接當 HTML 插入。"""
    h = await _make_hyp(session, rationale="<script>alert('x')</script>")
    views = [v for v in await load_views(session, "pending")
             if v.hypothesis.hypothesis_id == h.hypothesis_id]
    page = render_page(views, "pending")
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_page_handles_empty_list() -> None:
    assert "沒有假說" in render_page([], "pending")


def test_server_binds_localhost_only() -> None:
    """★ 只綁 127.0.0.1 —— 這是「不需要登入認證」這個決定的前提。"""
    from mes.review import HOST

    assert HOST == "127.0.0.1"
