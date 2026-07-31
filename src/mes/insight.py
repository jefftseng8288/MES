"""Phase 2.5 — InsightEngine:把 Knowledge Facts 壓縮成描述性 Insight。

**管線(Pipeline Plugins):**
  1. 取得要處理的 entity 清單(有 knowledge_state 的 **store**;seed / review_app 依定義
     無市場特徵,不處理 —— 見 `_load_facts`)。
  2. 對每個 entity:撈其所有 Facts;依 Producer 的**聲明**撈所需歷史;打包成記憶體 Context Dict。
  3. 丟給**可插拔的 Producer List**,各自吐出 InsightDraft 或 Skip(帶具體原因)。
  4. 寫入前跑 registry 驗證(value_text + producer)。
  5. 批次 **upsert** 進 insight_store,依 `(entity_id, insight_type)` 唯一鍵
     → **insight_id 穩定不重生成**(Phase 3 的 Hypothesis 才引用得住)。
     Skip 則進 `insight_run_log`(為什麼沒產出,不進 insight_store 污染語義)。

**第一版全量重算**(沿用 Phase 2 精神:資料量小、全量快)。

**★ `generated_at` 用 `now()`,不要改成純函數。** 語義是「這個**描述**何時被產生」= 執行時間,
與 knowledge_state 的 `observed_at`(「這個**事實**何時被觀測」,必由 observation_log 投影、
禁用系統時間)不同。已知且接受:insight_store 不是冪等重建的。

**★ 這是 Describe,不是 Predict。** Engine 只搬運 Producer 的描述,不做任何未來引申。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, InsightRunLog, InsightStore, KnowledgeState
from mes.insight_producers import (
    DEFAULT_PRODUCERS,
    BaseInsightProducer,
    Fact,
    HistoryPoint,
    InsightContext,
    InsightDraft,
    Skip,
)
from mes.insight_registry import validate_insight_value, validate_producer
from mes.knowledge import feature_history_bulk

logger = logging.getLogger(__name__)

# Insight 只描述市場實體(Reality entity);seed / review_app 依定義無市場特徵。
ENTITY_TYPE_STORE = "store"


@dataclass
class InsightRunReport:
    """一次全量重算的結果摘要(人讀 + 測試用)。"""

    run_at: datetime
    entities: int = 0
    produced: int = 0
    skipped: int = 0
    # insight_type -> 產出數 / skip 原因計數(便於一眼看出「為什麼大家都沒產出」)。
    produced_by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skipped_by_reason: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def summary(self) -> str:
        lines = [
            f"[insight] run_at={self.run_at.isoformat()} "
            f"entities={self.entities} produced={self.produced} skipped={self.skipped}",
        ]
        for t, n in sorted(self.produced_by_type.items()):
            lines.append(f"  產出 {t}: {n}")
        for r, n in sorted(self.skipped_by_reason.items(), key=lambda kv: -kv[1]):
            lines.append(f"  未產出 {n} 筆:{r}")
        return "\n".join(lines)


async def _load_facts(session: AsyncSession) -> dict[uuid.UUID, dict[str, Fact]]:
    """一次撈齊**store** entity 的當前 Facts(knowledge_state)。

    ★ 只處理 `store`:Insight 描述的是市場實體(Reality entity)的狀態。`store_name_seed`
    是「尚未推出 domain 的名字」,依定義沒有任何市場特徵;為它記「無 product_count」不是
    失敗訊號,是**類別錯誤** —— 且量體會把 run log 的真訊號(真正的 store 缺資料)埋掉
    (實測:一次全量 5918 筆 skip 中 2477 筆來自 seed)。review_app 同理。
    """
    rows = (
        await session.execute(
            select(KnowledgeState)
            .join(Entity, Entity.entity_id == KnowledgeState.entity_id)
            .where(Entity.entity_type == ENTITY_TYPE_STORE)
        )
    ).scalars().all()
    facts: dict[uuid.UUID, dict[str, Fact]] = defaultdict(dict)
    for r in rows:
        facts[r.entity_id][r.feature] = Fact(
            feature=r.feature,
            value_number=float(r.value_number) if r.value_number is not None else None,
            value_text=r.value_text,
            confidence=r.confidence,
        )
    return facts


async def _load_history(
    session: AsyncSession, features: set[str]
) -> dict[str, dict[uuid.UUID, list[HistoryPoint]]]:
    """依 Producer 的聲明撈所需歷史(Producer 自己不碰 DB)。"""
    history: dict[str, dict[uuid.UUID, list[HistoryPoint]]] = {}
    for feature in sorted(features):
        grouped = await feature_history_bulk(session, feature)
        history[feature] = {
            eid: [
                HistoryPoint(
                    observed_at=o.observed_at,
                    value_number=float(o.value_number) if o.value_number is not None else None,
                )
                for o in obs
            ]
            for eid, obs in grouped.items()
        }
    return history


def build_context(
    entity_id: uuid.UUID,
    facts: dict[str, Fact],
    history: dict[str, dict[uuid.UUID, list[HistoryPoint]]],
    needed_history: set[str],
) -> InsightContext:
    """把某 entity 的 Facts + 所需歷史打包成 Producer 的唯一輸入(純記憶體)。"""
    return InsightContext(
        entity_id=entity_id,
        facts=dict(facts),
        history={f: history.get(f, {}).get(entity_id, []) for f in sorted(needed_history)},
    )


async def run_insight_batch(
    session: AsyncSession,
    producers: tuple[BaseInsightProducer, ...] = DEFAULT_PRODUCERS,
    run_at: datetime | None = None,
) -> InsightRunReport:
    """全量重算:對每個 entity 跑所有 Producer,產出 upsert、未產出記 run log。"""
    # generated_at / run_at = 「這個描述何時被產生」= 執行時間(刻意用 now(),見模組 docstring)。
    run_at = run_at or datetime.now(UTC)
    report = InsightRunReport(run_at=run_at)

    facts_by_entity = await _load_facts(session)
    needed_history = {f for p in producers for f in p.required_history}
    history = await _load_history(session, needed_history)

    for entity_id, facts in facts_by_entity.items():
        report.entities += 1
        ctx = build_context(entity_id, facts, history, needed_history)
        for producer in producers:
            result = producer.produce(ctx)
            if isinstance(result, Skip):
                report.skipped += 1
                report.skipped_by_reason[result.reason] += 1
                session.add(InsightRunLog(
                    run_at=run_at, entity_id=entity_id, insight_type=producer.insight_type,
                    producer=producer.producer, reason=result.reason, detail=result.detail,
                ))
                continue
            await _upsert_insight(session, entity_id, producer, result, run_at)
            report.produced += 1
            report.produced_by_type[producer.insight_type] += 1

    await session.commit()
    logger.info("%s", report.summary())
    return report


async def _upsert_insight(
    session: AsyncSession,
    entity_id: uuid.UUID,
    producer: BaseInsightProducer,
    draft: InsightDraft,
    run_at: datetime,
) -> None:
    """寫入前驗證 → 依 (entity_id, insight_type) upsert(insight_id 因此穩定不重生成)。"""
    # 應用層受控守門:不合法明確報錯,不靜默通過、不自動修正。
    validate_insight_value(producer.insight_type, draft.value_text)
    validate_producer(producer.producer)

    stmt = pg_insert(InsightStore).values(
        insight_id=uuid.uuid4(),  # 只有「首次插入」時會用到;衝突時保留既有 insight_id
        entity_id=entity_id,
        insight_type=producer.insight_type,
        value_text=draft.value_text,
        producer=producer.producer,
        confidence=draft.confidence,
        generated_at=run_at,
        source_knowledge_refs=draft.source_knowledge_refs,
    )
    await session.execute(stmt.on_conflict_do_update(
        constraint="uq_insight_entity_type",
        set_={
            "value_text": stmt.excluded.value_text,
            "producer": stmt.excluded.producer,
            "confidence": stmt.excluded.confidence,
            "generated_at": stmt.excluded.generated_at,
            "source_knowledge_refs": stmt.excluded.source_knowledge_refs,
        },
    ))


async def run_insight(
    *, session_maker: async_sessionmaker[AsyncSession] | None = None
) -> InsightRunReport:
    """跑一次全量 Insight 重算(daemon 入口)。"""
    engine = None
    if session_maker is None:
        engine = create_async_engine(get_settings().database_url)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            report = await run_insight_batch(session)
            print(report.summary())
            return report
    finally:
        if engine is not None:
            await engine.dispose()


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_insight())


if __name__ == "__main__":
    main()
