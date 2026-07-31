"""Phase 2 投影引擎(Knowledge Engine)—— 全量重算 observation_log → knowledge_state。

**全量重算(Jeff 定案):** 每次投影都清空 knowledge_state、把整個 observation_log 重跑一遍。
資料規模小(<百萬),全量快、天然冪等、最不易錯;日常投影 = 重建,順便驗證重建能力。

**純函數重建(鐵律):** 投影過程不使用任何 now()/CURRENT_TIMESTAMP。所有寫入 knowledge_state 的
時間維度(observed_at / updated_at)100% 由 observation_log.observed_at 投影而來 —— 同樣的
observation_log,今天投影與明天投影結果完全一致(冪等)。

**守 P2 中立:** 只做取值 + normalize,不做任何評分 / 判斷 / 排序。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import KnowledgeState, ObservationLog
from mes.jobs import JOB_PROJECTION, heartbeat

logger = logging.getLogger(__name__)

COUNTRY_FEATURE = "country"
RULE_DEFAULT = "default_v1"
RULE_COUNTRY = "country_v1"
# confidence 高低(高 = 更可信)。用於 default 的同時間 tiebreaker,以及 country 特例的覆蓋門檻。
_CONF_RANK = {"certain": 3, "inferred": 2, "estimated": 1}


def _conf(o: ObservationLog) -> int:
    return _CONF_RANK.get(o.confidence, 0)


def _pick_default(observed: list[ObservationLog]) -> ObservationLog:
    """決定 1 — 時間優先:observed_at 最新 → confidence tiebreaker → observation_id(決定性)。"""
    return max(observed, key=lambda o: (o.observed_at, _conf(o), o.observation_id))


def _pick_country(observed: list[ObservationLog]) -> ObservationLog:
    """country 特例(Jeff 定案 B 版):時間序 fold,新值 confidence 需 ≥ 現行才覆蓋。

    低 confidence 的新值(如新 inferred)不覆蓋高 confidence 的舊值(如舊 certain)——
    剛性事實不被猜測污染;但同 / 更高 confidence 的新值照時間優先取代。
    """
    current: ObservationLog | None = None
    for o in sorted(observed, key=lambda o: (o.observed_at, _conf(o), o.observation_id)):
        if current is None or _conf(o) >= _conf(current):
            current = o
    assert current is not None  # observed 非空(呼叫端已保證)
    return current


def _latest_attempt(observations: list[ObservationLog]) -> ObservationLog:
    """current_status 來源:所有觀測(含 fetch_failed/not_found)中 observed_at 最新那筆。

    ★ 與「算 value 只看 observed」掃不同子集:value 只從 observed 挑,current_status 從全部挑最新。
    """
    return max(observations, key=lambda o: (o.observed_at, o.observation_id))


def project_row(
    entity_id: object, feature: str, observations: list[ObservationLog]
) -> KnowledgeState | None:
    """純函數:給定某 (entity, feature) 的所有觀測,回傳一列 KnowledgeState,或 None。

    None = 從無任何 observed(只有失敗)。v1 不為其投影列(保留 Provenance NOT NULL 鐵律;
    規則 1 的 CHECK 續當防禦守門)。有 ≥1 observed → 投影出當前值 + current_status。
    """
    observed = [o for o in observations if o.status == "observed"]
    if not observed:
        return None
    if feature == COUNTRY_FEATURE:
        winner, rule = _pick_country(observed), RULE_COUNTRY
    else:
        winner, rule = _pick_default(observed), RULE_DEFAULT
    latest = _latest_attempt(observations)
    return KnowledgeState(
        entity_id=entity_id,
        feature=feature,
        # value 容器與 Observation 同構,直接投影(不做型別轉換)。
        value_type=winner.value_type,
        value_raw=winner.value_raw,
        value_text=winner.value_text,
        value_number=winner.value_number,
        value_boolean=winner.value_boolean,
        value_json=winner.value_json,
        value_entity_id=winner.value_entity_id,
        producer=winner.producer,
        source_observation_id=winner.observation_id,
        observed_at=winner.observed_at,  # = 最後一次成功觀測時間(value 的新鮮度)
        confidence=winner.confidence,
        selection_rule_version=rule,
        # current_status = 最近一次「嘗試」的結果(可能比 value 的 observed_at 更晚的失敗)。
        current_status=latest.status,
        # 純函數:updated_at 亦由資料投影(最新一筆觀測時間),不用 now()。
        updated_at=latest.observed_at,
    )


async def rebuild_knowledge_state(session: AsyncSession) -> int:
    """全量重算:讀 observation_log 全部 → 清空 knowledge_state → 逐 (entity,feature) 投影 → 寫入。

    回傳寫入的 knowledge_state 列數。因是全量重建,清空重寫最單純(knowledge_state 是物化視圖、
    無 Append-Only 鎖,可清)。
    """
    rows = (await session.execute(select(ObservationLog))).scalars().all()
    groups: dict[tuple[object, str], list[ObservationLog]] = defaultdict(list)
    for o in rows:
        groups[(o.entity_id, o.feature)].append(o)

    await session.execute(delete(KnowledgeState))
    written = 0
    for (entity_id, feature), obs in groups.items():
        ks = project_row(entity_id, feature, obs)
        if ks is not None:
            session.add(ks)
            written += 1
    await session.commit()
    logger.info(
        "[projection] 投影完成:%d (entity,feature) 組 → %d 列(其中 %d 組從無 observed、不投影)",
        len(groups), written, len(groups) - written,
    )
    return written


def _observed_history_stmt(feature: str) -> Select[tuple[ObservationLog]]:
    """時間序列的**單一定義**:某 feature 的 observed 筆,依 observed_at 排序。

    單筆查詢(feature_history)與批次查詢(feature_history_bulk)共用同一條件,避免兩處
    各自硬寫而語義漂移(CLAUDE.md 規則 3:取值邏輯收斂單一函式)。
    """
    return (
        select(ObservationLog)
        .where(ObservationLog.feature == feature, ObservationLog.status == "observed")
        .order_by(ObservationLog.observed_at, ObservationLog.observation_id)
    )


async def feature_history(
    session: AsyncSession, entity_id: object, feature: str
) -> list[ObservationLog]:
    """時間序列:某 entity 的某 feature 歷次 observed 值,依 observed_at 排序(讀 Append-Only 全歷史)。

    knowledge_state = 當前值(查它);時間序列 = 歷史(查此,即 observation_log)。
    """
    stmt = _observed_history_stmt(feature).where(ObservationLog.entity_id == entity_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def feature_history_bulk(
    session: AsyncSession, feature: str
) -> dict[uuid.UUID, list[ObservationLog]]:
    """同 feature_history,但一次撈全部 entity(批次投影/Insight 用,避免 N+1 查詢)。

    回傳 entity_id -> 依 observed_at 排序的 observed 歷史。
    """
    rows = (await session.execute(_observed_history_stmt(feature))).scalars().all()
    grouped: dict[uuid.UUID, list[ObservationLog]] = defaultdict(list)
    for o in rows:
        grouped[o.entity_id].append(o)
    return grouped


async def run_projection(
    *, session_maker: async_sessionmaker[AsyncSession] | None = None
) -> int:
    """投影一次(daemon 入口)。回傳寫入列數。"""
    engine = None
    if session_maker is None:
        engine = create_async_engine(get_settings().database_url)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with heartbeat(JOB_PROJECTION) as beat, session_maker() as session:
            written = await rebuild_knowledge_state(session)
            beat.summary = {"rows_written": written}
            print(f"[projection] knowledge_state rebuilt: {written} rows")
            return written
    finally:
        if engine is not None:
            await engine.dispose()


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_projection())


if __name__ == "__main__":
    main()
