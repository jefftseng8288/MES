"""Phase 1-D store feature-harvest tests.

Parser three-value logic is tested purely (mock products/HTML). The write chain and
harvest-state flow are tested against the real DB.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, ObservationLog, StoreHarvestState
from mes.harvest import (
    PRODUCER_STORE_CRAWLER,
    FeatureResult,
    ProductsFetch,
    _select_stores_to_harvest,
    _upsert_state,
    _write_feature,
    parse_homepage_features,
    parse_products_features,
)

_BATCH = "2099-01-01-01"

_HTML = (
    'var x; Shopify.theme = {"name":"Dawn","id":123}; '
    'Shopify.country = "US"; Shopify.locale = "en"; '
    'Shopify.currency = {"active":"USD","rate":"1.0"}; '
    '<script src="https://cdn.loox.io/widget.js"></script>'
)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _by_feature(results: list[FeatureResult]) -> dict[str, FeatureResult]:
    return {r.feature: r for r in results}


# --- products.json parsing (three-value) -------------------------------------


def test_products_ok_parses_five() -> None:
    pf = ProductsFetch(
        "ok",
        products=[
            {"variants": [{"price": "10.00"}, {"price": "20.00"}]},
            {"variants": [{"price": "30.00"}]},
        ],
        complete=True,
    )
    f = _by_feature(parse_products_features(pf))
    assert f["product_count"].status == "observed" and f["product_count"].value_number == 2
    assert f["avg_price"].status == "observed" and f["avg_price"].value_number == 20.0
    assert f["price_range"].value_json == {"min": 10.0, "max": 30.0}
    assert f["is_active"].status == "observed" and f["is_active"].value_boolean is True
    assert f["product_count"].confidence == "certain"


def test_products_incomplete_is_estimated() -> None:
    pf = ProductsFetch("ok", products=[{"variants": [{"price": "5"}]}], complete=False)
    f = _by_feature(parse_products_features(pf))
    assert f["product_count"].confidence == "estimated"
    assert f["avg_price"].confidence == "estimated"


def test_products_empty_store() -> None:
    f = _by_feature(parse_products_features(ProductsFetch("ok", products=[], complete=True)))
    assert f["product_count"].status == "observed" and f["product_count"].value_number == 0
    assert f["avg_price"].status == "not_found"
    assert f["price_range"].status == "not_found"
    assert f["is_active"].status == "observed" and f["is_active"].value_boolean is False


def test_products_not_found_is_active_false() -> None:
    f = _by_feature(parse_products_features(ProductsFetch("not_found")))
    assert f["product_count"].status == "not_found"
    # confirmed no open store -> is_active observed false (a valid negative, not a failure)
    assert f["is_active"].status == "observed" and f["is_active"].value_boolean is False


def test_products_failed_all_fetch_failed() -> None:
    f = _by_feature(parse_products_features(ProductsFetch("failed")))
    assert all(f[k].status == "fetch_failed" for k in ("product_count", "avg_price", "is_active"))


# --- homepage parsing --------------------------------------------------------


def test_homepage_parses_vars_and_signature() -> None:
    review_apps = {"loox": uuid.uuid4()}
    f = _by_feature(parse_homepage_features(_HTML, review_apps))
    assert f["theme_name"].status == "observed" and f["theme_name"].value_text == "Dawn"
    assert f["country"].value_text == "US"
    assert f["language"].value_text == "en"
    assert f["currency"].value_text == "USD"
    assert f["theme_name"].confidence == "certain"
    # uses_review_app: signature hit -> observed entity_ref, inferred (推論,非直讀)
    ura = f["uses_review_app"]
    assert ura.status == "observed" and ura.value_type == "entity_ref"
    assert ura.value_entity_id == review_apps["loox"]
    assert ura.confidence == "inferred"


def test_homepage_no_signature_is_not_found_inferred() -> None:
    f = _by_feature(parse_homepage_features("<html>no apps here</html>", {"loox": uuid.uuid4()}))
    assert f["uses_review_app"].status == "not_found"
    assert f["uses_review_app"].confidence == "inferred"
    assert f["theme_name"].status == "not_found"  # var absent


def test_homepage_unreachable_all_fetch_failed() -> None:
    f = _by_feature(parse_homepage_features(None, {}))
    assert all(
        f[k].status == "fetch_failed"
        for k in ("theme_name", "country", "language", "currency", "uses_review_app")
    )


# --- write chain + CHECK (real DB) -------------------------------------------


async def _make_store(session: AsyncSession) -> Entity:
    store = Entity(entity_type="store", canonical_key=f"harvest-{uuid.uuid4().hex}.com")
    session.add(store)
    await session.commit()
    return store


async def test_write_nine_features_pass_check_and_provenance(session: AsyncSession) -> None:
    store = await _make_store(session)
    loox = await session.scalar(
        select(Entity).where(Entity.entity_type == "review_app", Entity.canonical_key == "loox")
    )
    assert loox is not None
    results = parse_products_features(
        ProductsFetch("ok", [{"variants": [{"price": "9.99"}]}], complete=True)
    ) + parse_homepage_features(_HTML, {"loox": loox.entity_id})
    assert len(results) == 9
    for r in results:
        await _write_feature(session, store.entity_id, _BATCH, r)
    await session.commit()

    rows = (
        await session.execute(
            select(ObservationLog).where(ObservationLog.entity_id == store.entity_id)
        )
    ).scalars().all()
    assert len(rows) == 9
    assert all(o.producer == PRODUCER_STORE_CRAWLER for o in rows)
    assert all(o.batch_id == _BATCH for o in rows)
    ura = next(o for o in rows if o.feature == "uses_review_app")
    assert ura.value_entity_id == loox.entity_id and ura.confidence == "inferred"


async def test_write_failed_and_not_found_pass_check(session: AsyncSession) -> None:
    store = await _make_store(session)
    for r in parse_products_features(ProductsFetch("failed")):  # all fetch_failed
        await _write_feature(session, store.entity_id, _BATCH, r)
    for r in parse_homepage_features("<html></html>", {}):  # not_found
        await _write_feature(session, store.entity_id, _BATCH, r)
    await session.commit()  # passes discriminated-union CHECK (all value cols NULL)
    rows = (
        await session.execute(
            select(ObservationLog.status).where(ObservationLog.entity_id == store.entity_id)
        )
    ).scalars().all()
    assert set(rows) <= {"fetch_failed", "not_found"}


# --- harvest state flow ------------------------------------------------------


async def test_harvest_state_flow_and_selection(session: AsyncSession) -> None:
    store = await _make_store(session)
    # no state row yet -> selectable
    picked = await _select_stores_to_harvest(session, 10_000_000)
    assert store.entity_id in [eid for eid, _ in picked]

    await _upsert_state(session, store.entity_id, "done")
    await session.commit()
    state = await session.get(StoreHarvestState, store.entity_id)
    assert state is not None and state.status == "done"
    # 剛嘗試過 -> 最小重抓間隔內,不再挑(不論 done 或 failed,現在是「時間」決定不是「狀態」)
    picked = await _select_stores_to_harvest(session, 10_000_000)
    assert store.entity_id not in [eid for eid, _ in picked]

    await _upsert_state(session, store.entity_id, "failed")
    await session.commit()
    picked = await _select_stores_to_harvest(session, 10_000_000)
    assert store.entity_id not in [eid for eid, _ in picked]  # 失敗也一樣要等間隔(天然退避)


async def _attempted_at(session: AsyncSession, entity_id: uuid.UUID, when: datetime) -> None:
    """把某店的最後嘗試時間直接設成 when(模擬「很久以前試過」)。"""
    await session.execute(
        update(StoreHarvestState).where(StoreHarvestState.entity_id == entity_id)
        .values(updated_at=when)
    )
    await session.commit()


async def test_done_store_is_reharvested_after_interval(session: AsyncSession) -> None:
    """★ 重抓機制成立的關鍵:done 的店在間隔過後會再次被挑到。

    舊行為是 done 之後永不再抓 → 每家店一輩子只有一筆觀測 → Growth 類 insight 不可能成立。
    """
    store = await _make_store(session)
    await _upsert_state(session, store.entity_id, "done")
    await session.commit()
    assert store.entity_id not in [
        eid for eid, _ in await _select_stores_to_harvest(session, 10_000_000)
    ]
    # 上次嘗試是 8 天前(> 7 天間隔)-> 重新成為候選
    await _attempted_at(session, store.entity_id, datetime.now(UTC) - timedelta(days=8))
    assert store.entity_id in [
        eid for eid, _ in await _select_stores_to_harvest(session, 10_000_000)
    ]


async def test_never_attempted_ranks_before_previously_attempted(session: AsyncSession) -> None:
    """沒試過的優先於試過的(即使那家試過的已超過間隔很久)。"""
    old = await _make_store(session)
    await _upsert_state(session, old.entity_id, "done")
    await session.commit()
    await _attempted_at(session, old.entity_id, datetime.now(UTC) - timedelta(days=99))
    fresh = await _make_store(session)  # 從未嘗試

    order = [eid for eid, _ in await _select_stores_to_harvest(session, 10_000_000)]
    assert order.index(fresh.entity_id) < order.index(old.entity_id)


async def test_oldest_attempt_first_among_attempted(session: AsyncSession) -> None:
    """試過的之中,最久沒試的優先(這就是天然退避:試過的排到隊尾)。"""
    older, newer = await _make_store(session), await _make_store(session)
    for s in (older, newer):
        await _upsert_state(session, s.entity_id, "done")
    await session.commit()
    await _attempted_at(session, older.entity_id, datetime.now(UTC) - timedelta(days=60))
    await _attempted_at(session, newer.entity_id, datetime.now(UTC) - timedelta(days=30))

    order = [eid for eid, _ in await _select_stores_to_harvest(session, 10_000_000)]
    assert order.index(older.entity_id) < order.index(newer.entity_id)


async def test_failed_store_does_not_block_others(session: AsyncSession) -> None:
    """★ 卡死修復:一直失敗的店不會霸佔名額,下一批換別家。

    舊行為:ORDER BY created_at + failed 永遠是候選 → 每批都挑同一批失敗的店
    (實測曾連續 16 天只抓同 3 家假網域)。
    """
    stuck = await _make_store(session)  # 較早建立、且一直失敗
    others = [await _make_store(session) for _ in range(3)]

    # 都沒試過時,stuck 建立較早 -> 排在其他家前面(舊邏輯也是如此)
    order = [eid for eid, _ in await _select_stores_to_harvest(session, 10_000_000)]
    assert all(order.index(stuck.entity_id) < order.index(o.entity_id) for o in others)

    await _upsert_state(session, stuck.entity_id, "failed")  # 抓失敗了
    await session.commit()

    # ★ 關鍵:失敗後它排到其他家「後面」(舊邏輯會讓它永遠排最前 -> 卡死)
    order = [eid for eid, _ in await _select_stores_to_harvest(session, 10_000_000)]
    assert stuck.entity_id not in order  # 間隔內先完全退出候選
    assert all(o.entity_id in order for o in others)  # 名額讓給別家
