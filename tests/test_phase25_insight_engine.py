"""Phase 2.5 第二批:Producer(純函數)+ InsightEngine + registry 擴充。

Producer 取值/門檻/成長率用純函數測(記憶體 InsightContext,Producer 不碰 DB);
Engine 的 upsert / run log / 可插拔對真實 DB 測。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, InsightRunLog, InsightStore, KnowledgeState, ObservationLog
from mes.insight import build_context, run_insight_batch
from mes.insight_producers import (
    FEATURE_PRODUCT_COUNT,
    FEATURE_REVIEW_COUNT,
    BaseInsightProducer,
    Fact,
    GrowthStatProducer,
    HistoryPoint,
    InsightContext,
    InsightDraft,
    Skip,
    SKURuleProducer,
)
from mes.insight_registry import (
    InsightValueError,
    registered_producers,
    type_kind,
    validate_insight_value,
    validate_producer,
)

_BATCH = "2099-01-01-01"
_T0 = datetime(2026, 3, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _ctx(*, product_count: float | None = None, confidence: str = "certain",
         review_history: list[tuple[datetime, float]] | None = None,
         entity_id: uuid.UUID | None = None) -> InsightContext:
    facts = {}
    if product_count is not None:
        facts[FEATURE_PRODUCT_COUNT] = Fact(
            FEATURE_PRODUCT_COUNT, value_number=product_count, value_text=None,
            confidence=confidence)
    history = {}
    if review_history is not None:
        history[FEATURE_REVIEW_COUNT] = [HistoryPoint(at, v) for at, v in review_history]
    return InsightContext(entity_id=entity_id or uuid.uuid4(), facts=facts, history=history)


# --- SKURuleProducer:三級門檻(邊界值逐一驗)-----------------------------------


@pytest.mark.parametrize(
    ("count", "label"),
    [(0, "Low SKU"), (100, "Low SKU"), (101, "Medium SKU"), (500, "Medium SKU"),
     (501, "High SKU"), (5000, "High SKU")],
)
def test_sku_thresholds_boundaries(count: float, label: str) -> None:
    result = SKURuleProducer().produce(_ctx(product_count=count))
    assert isinstance(result, InsightDraft) and result.value_text == label


def test_sku_carries_source_confidence_and_refs() -> None:
    eid = uuid.uuid4()
    result = SKURuleProducer().produce(_ctx(product_count=800, confidence="estimated",
                                            entity_id=eid))
    assert isinstance(result, InsightDraft)
    # 事實若是 estimated,標籤不該自稱 certain
    assert result.confidence == "estimated"
    assert result.source_knowledge_refs == [
        {"entity_id": str(eid), "feature": FEATURE_PRODUCT_COUNT}
    ]


def test_sku_no_product_count_skips_with_reason() -> None:
    result = SKURuleProducer().produce(_ctx())
    assert isinstance(result, Skip)
    assert "無 product_count" in result.reason  # 具體載明缺什麼


# --- GrowthStatProducer:成長率 + 容忍窗 ----------------------------------------


def test_growth_rate_computed_and_numeric() -> None:
    # 30 天前 100 → 現在 125 = +25%
    result = GrowthStatProducer().produce(_ctx(review_history=[
        (_T0, 100.0), (_T0 + timedelta(days=30), 125.0)]))
    assert isinstance(result, InsightDraft)
    assert float(result.value_text) == pytest.approx(0.25)
    assert result.value_text == "0.250000"  # 統一精度格式
    assert result.confidence == "estimated"  # 容忍窗未必剛好 30 天


def test_growth_rate_negative_is_recorded_not_flattened() -> None:
    # ★ 刻意不設門檻:−50% 要如實記錄,不被壓成標籤而丟失資訊
    result = GrowthStatProducer().produce(_ctx(review_history=[
        (_T0, 200.0), (_T0 + timedelta(days=30), 100.0)]))
    assert isinstance(result, InsightDraft)
    assert float(result.value_text) == pytest.approx(-0.5)


@pytest.mark.parametrize("gap_days", [25, 30, 35])
def test_growth_window_inclusive_bounds(gap_days: int) -> None:
    result = GrowthStatProducer().produce(_ctx(review_history=[
        (_T0, 100.0), (_T0 + timedelta(days=gap_days), 110.0)]))
    assert isinstance(result, InsightDraft)


@pytest.mark.parametrize("gap_days", [24, 36])
def test_growth_window_outside_skips_with_span(gap_days: int) -> None:
    result = GrowthStatProducer().produce(_ctx(review_history=[
        (_T0, 100.0), (_T0 + timedelta(days=gap_days), 110.0)]))
    assert isinstance(result, Skip)
    assert f"跨度僅 {gap_days} 天" in result.reason  # 載明缺什麼
    assert result.detail is not None and result.detail["span_days"] == gap_days


def test_growth_picks_closest_to_30_days() -> None:
    # 窗內有 26 天前(120)與 30 天前(100)兩點 → 取最接近 30 天的那筆(100)
    now = _T0 + timedelta(days=30)
    result = GrowthStatProducer().produce(_ctx(review_history=[
        (_T0, 100.0), (now - timedelta(days=26), 120.0), (now, 150.0)]))
    assert isinstance(result, InsightDraft)
    assert float(result.value_text) == pytest.approx(0.5)  # (150-100)/100


def test_growth_single_point_skips() -> None:
    result = GrowthStatProducer().produce(_ctx(review_history=[(_T0, 100.0)]))
    assert isinstance(result, Skip)
    assert "僅 1 筆 observed" in result.reason
    assert result.detail is not None and result.detail["history_points"] == 1


def test_growth_no_history_skips() -> None:
    result = GrowthStatProducer().produce(_ctx(review_history=[]))
    assert isinstance(result, Skip)
    assert "無 observed 歷史" in result.reason


def test_growth_zero_base_skips() -> None:
    result = GrowthStatProducer().produce(_ctx(review_history=[
        (_T0, 0.0), (_T0 + timedelta(days=30), 50.0)]))
    assert isinstance(result, Skip)
    assert "無法計算" in result.reason
    assert result.detail is not None and result.detail["base_value"] == 0.0


# --- Producer 純函數性 ---------------------------------------------------------


def test_producers_are_pure_same_context_same_output() -> None:
    ctx = _ctx(product_count=250, review_history=[(_T0, 10.0), (_T0 + timedelta(days=30), 20.0)])
    for producer in (SKURuleProducer(), GrowthStatProducer()):
        first, second = producer.produce(ctx), producer.produce(ctx)
        assert first == second  # 同一 Context → 輸出恆定


def test_producers_take_no_session_argument() -> None:
    # 純函數:produce 的唯一輸入是 Context(不碰 DB)
    import inspect
    for producer in (SKURuleProducer(), GrowthStatProducer()):
        params = list(inspect.signature(producer.produce).parameters)
        assert params == ["ctx"]


# --- registry:數值型 / 列舉型 / producer 受控 ----------------------------------


def test_registry_kinds() -> None:
    assert type_kind(SKURuleProducer.insight_type) == "enum"
    assert type_kind(GrowthStatProducer.insight_type) == "numeric"


def test_numeric_type_accepts_numbers_rejects_labels() -> None:
    for good in ("0.250000", "-0.5", "0", "1e-3"):
        validate_insight_value(GrowthStatProducer.insight_type, good)
    for bad in ("Growth", "", "25%"):
        with pytest.raises(InsightValueError):
            validate_insight_value(GrowthStatProducer.insight_type, bad)


def test_enum_type_still_enforced() -> None:
    validate_insight_value(SKURuleProducer.insight_type, "High SKU")
    with pytest.raises(InsightValueError):
        validate_insight_value(SKURuleProducer.insight_type, "high_sku")


def test_producer_controlled() -> None:
    for p in ("rule_v1", "stat_v1"):
        validate_producer(p)
        assert p in registered_producers()
    for bad in ("rule_V1", "ruleV1", "unknown_v9"):
        with pytest.raises(InsightValueError):
            validate_producer(bad)


# --- Engine(真實 DB):upsert / run log / 可插拔 -------------------------------


async def _store_with(session: AsyncSession, *, product_count: float | None = None,
                      review_points: list[tuple[datetime, float]] | None = None) -> uuid.UUID:
    """一家店 + 指定的 knowledge fact / review 歷史(直接建資料,不跑 crawler)。"""
    store = Entity(entity_type="store", canonical_key=f"ins2-{uuid.uuid4().hex}.com")
    session.add(store)
    await session.flush()
    obs = ObservationLog(
        entity_id=store.entity_id, feature=FEATURE_PRODUCT_COUNT, value_type="number",
        value_raw="1", value_number=1, source="products_json", producer="mes_crawler_v1",
        observed_at=_T0, confidence="certain", status="observed", batch_id=_BATCH)
    session.add(obs)
    await session.flush()
    if product_count is not None:
        session.add(KnowledgeState(
            entity_id=store.entity_id, feature=FEATURE_PRODUCT_COUNT, value_type="number",
            value_raw=str(product_count), value_number=product_count, producer="mes_crawler_v1",
            source_observation_id=obs.observation_id, observed_at=_T0, confidence="certain",
            selection_rule_version="default_v1", current_status="observed"))
    for at, v in review_points or []:
        session.add(ObservationLog(
            entity_id=store.entity_id, feature=FEATURE_REVIEW_COUNT, value_type="number",
            value_raw=str(v), value_number=v, source="html_page", producer="mes_crawler_v1",
            observed_at=at, confidence="certain", status="observed", batch_id=_BATCH))
    await session.commit()
    return store.entity_id


async def test_engine_produces_and_upserts_stably(session: AsyncSession) -> None:
    eid = await _store_with(session, product_count=800)
    await run_insight_batch(session, producers=(SKURuleProducer(),))
    row = await session.scalar(select(InsightStore).where(InsightStore.entity_id == eid))
    assert row is not None and row.value_text == "High SKU"
    first_id, first_gen = row.insight_id, row.generated_at

    # 連跑第二次:不重複建列、insight_id 穩定(Phase 3 引用得住)
    session.expunge_all()
    await run_insight_batch(session, producers=(SKURuleProducer(),))
    rows = (await session.execute(
        select(InsightStore).where(InsightStore.entity_id == eid))).scalars().all()
    assert len(rows) == 1
    assert rows[0].insight_id == first_id
    assert rows[0].generated_at >= first_gen  # generated_at 是執行時間(刻意非冪等)


async def test_engine_updates_value_on_rerun(session: AsyncSession) -> None:
    eid = await _store_with(session, product_count=50)
    await run_insight_batch(session, producers=(SKURuleProducer(),))
    row = await session.scalar(select(InsightStore).where(InsightStore.entity_id == eid))
    assert row is not None and row.value_text == "Low SKU"
    kept_id = row.insight_id

    # 事實變了 → 同一列被 upsert 更新(不是新增一列)
    ks = await session.get(KnowledgeState, (eid, FEATURE_PRODUCT_COUNT))
    assert ks is not None
    ks.value_number = 900
    ks.value_raw = "900"
    await session.commit()
    session.expunge_all()
    await run_insight_batch(session, producers=(SKURuleProducer(),))
    rows = (await session.execute(
        select(InsightStore).where(InsightStore.entity_id == eid))).scalars().all()
    assert len(rows) == 1 and rows[0].value_text == "High SKU"
    assert rows[0].insight_id == kept_id


async def test_engine_records_skip_reason_in_run_log(session: AsyncSession) -> None:
    # 有 knowledge 但只有 1 筆 review 歷史 → GrowthStat 不產出,原因要載明缺什麼
    eid = await _store_with(session, product_count=10, review_points=[(_T0, 5.0)])
    run = await run_insight_batch(session, producers=(GrowthStatProducer(),))
    # 全量重算會掃全 DB(dev DB 會累積他測資料),故斷言只針對**這家店**,不對全域計數
    assert run.skipped >= 1
    assert await session.scalar(
        select(InsightStore).where(InsightStore.entity_id == eid)) is None  # 這家沒產出
    logs = (await session.execute(
        select(InsightRunLog).where(InsightRunLog.entity_id == eid))).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.insight_type == "GROWTH_VELOCITY" and log.producer == "stat_v1"
    assert "僅 1 筆 observed" in log.reason  # 具體:缺什麼
    assert log.detail is not None and log.detail["history_points"] == 1


async def test_engine_growth_end_to_end_with_history(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    eid = await _store_with(session, product_count=10, review_points=[
        (now - timedelta(days=30), 100.0), (now, 130.0)])
    await run_insight_batch(session, producers=(GrowthStatProducer(),))
    row = await session.scalar(
        select(InsightStore).where(InsightStore.entity_id == eid,
                                   InsightStore.insight_type == "GROWTH_VELOCITY"))
    assert row is not None
    assert float(row.value_text) == pytest.approx(0.3)
    assert row.source_knowledge_refs == [{"entity_id": str(eid), "feature": FEATURE_REVIEW_COUNT}]


async def test_engine_pluggable_new_producer(session: AsyncSession) -> None:
    """加一個假 Producer 丟進 List 即生效,不動 Engine。"""

    class FakeProducer(BaseInsightProducer):
        insight_type = "TEST_PLUGGABLE"
        producer = "fake_v1"
        values = ("Yes", "No")
        required_features = (FEATURE_PRODUCT_COUNT,)

        def produce(self, ctx: InsightContext) -> InsightDraft | Skip:
            fact = ctx.facts.get(FEATURE_PRODUCT_COUNT)
            if fact is None:
                return Skip("no product_count")
            return InsightDraft("Yes", "certain", self._refs(ctx.entity_id, FEATURE_PRODUCT_COUNT))

    eid = await _store_with(session, product_count=7)
    await run_insight_batch(session, producers=(FakeProducer(),))
    row = await session.scalar(
        select(InsightStore).where(InsightStore.entity_id == eid,
                                   InsightStore.insight_type == "TEST_PLUGGABLE"))
    assert row is not None and row.value_text == "Yes" and row.producer == "fake_v1"


def test_build_context_gives_producer_only_memory_objects() -> None:
    eid = uuid.uuid4()
    facts = {FEATURE_PRODUCT_COUNT: Fact(FEATURE_PRODUCT_COUNT, 5.0, None, "certain")}
    history = {FEATURE_REVIEW_COUNT: {eid: [HistoryPoint(_T0, 1.0)]}}
    ctx = build_context(eid, facts, history, {FEATURE_REVIEW_COUNT})
    assert ctx.entity_id == eid
    assert ctx.facts[FEATURE_PRODUCT_COUNT].value_number == 5.0
    assert ctx.history[FEATURE_REVIEW_COUNT] == [HistoryPoint(_T0, 1.0)]
    # 沒有該 entity 的歷史時給空 list(不是 None,Producer 不必處理兩種空)
    assert build_context(uuid.uuid4(), {}, history, {FEATURE_REVIEW_COUNT}).history[
        FEATURE_REVIEW_COUNT] == []


# --- 守 P2.5 紅線:無預測性內容 ------------------------------------------------


def test_no_prediction_columns_in_insight_store() -> None:
    cols = set(InsightStore.__table__.columns.keys())
    banned = {"prediction", "forecast", "predicted_at", "will_", "expected_value", "bet"}
    assert cols.isdisjoint(banned)
