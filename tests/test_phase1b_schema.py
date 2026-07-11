"""Phase 1-B integration tests — real PostgreSQL, no mocks.

Requires the migration to be applied (`uv run alembic upgrade head`) against the
database at MES_DATABASE_URL. These tests prove the DB-layer guarantees the
Phase 0 schema docs demand, plus the discriminated-union value contract:

- Append-Only physical lock on observation_log (UPDATE/DELETE rejected).
- Provenance hard constraint (NOT NULL on entity_id / source_observation_id).
- knowledge_state remains freely UPDATE-able (物化視圖 semantics intact).
- Controlled-string CHECK constraints reject illegal values.
- Discriminated-union value contract: value_raw non-blank when observed; exactly
  one typed column matching value_type; failed/not_found carry no value.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, KnowledgeState, ObservationLog

# A seeded review_app entity (fixed UUID from the migration) for entity_ref values.
LOOX_ENTITY_ID = uuid.UUID("11111111-1111-4111-8111-000000000001")


def _now() -> datetime:
    return datetime.now(UTC)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _make_store(session: AsyncSession, key: str | None = None) -> Entity:
    store = Entity(entity_type="store", canonical_key=key or f"test-{uuid.uuid4().hex}.com")
    session.add(store)
    await session.commit()
    return store


def _obs(entity_id: uuid.UUID, **overrides: Any) -> ObservationLog:
    """A valid observed number observation; override fields to build other cases."""
    base: dict[str, Any] = {
        "entity_id": entity_id,
        "feature": "product_count",
        "value_type": "number",
        "value_raw": "42",
        "value_number": 42,
        "source": "products_json",
        "producer": "mes_crawler_v1",
        "observed_at": _now(),
        "confidence": "certain",
        "status": "observed",
        "crawler_version": "testhash",
    }
    base.update(overrides)
    return ObservationLog(**base)


def _ks(entity_id: uuid.UUID, source_observation_id: uuid.UUID, **overrides: Any) -> KnowledgeState:
    base: dict[str, Any] = {
        "entity_id": entity_id,
        "feature": "product_count",
        "value_type": "number",
        "value_raw": "42",
        "value_number": 42,
        "producer": "mes_crawler_v1",
        "source_observation_id": source_observation_id,
        "observed_at": _now(),
        "confidence": "certain",
        "selection_rule_version": "default_v1",
    }
    base.update(overrides)
    return KnowledgeState(**base)


async def _make_observation(session: AsyncSession, entity_id: uuid.UUID) -> ObservationLog:
    obs = _obs(entity_id)
    session.add(obs)
    await session.commit()
    return obs


async def _expect_rejected(session: AsyncSession, obj: Any) -> None:
    session.add(obj)
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.commit()
    await session.rollback()


# --- Structure & seed --------------------------------------------------------


async def test_three_core_tables_exist(session: AsyncSession) -> None:
    rows = await session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            "AND table_name IN ('entity', 'observation_log', 'knowledge_state')"
        )
    )
    names = {r[0] for r in rows}
    assert names == {"entity", "observation_log", "knowledge_state"}


async def test_review_app_signature_library_seeded(session: AsyncSession) -> None:
    rows = await session.execute(
        text("SELECT canonical_key FROM entity WHERE entity_type = 'review_app'")
    )
    keys = {r[0] for r in rows}
    assert {"loox", "judgeme", "yotpo", "okendo", "stamped"} <= keys


# --- Append-Only physical lock (preserved) -----------------------------------


async def test_observation_log_append_only_rejects_update(session: AsyncSession) -> None:
    store = await _make_store(session)
    obs = await _make_observation(session, store.entity_id)

    with pytest.raises(DBAPIError) as exc:
        await session.execute(
            text("UPDATE observation_log SET status = 'not_found' WHERE observation_id = :oid"),
            {"oid": obs.observation_id},
        )
        await session.commit()
    assert "append-only" in str(exc.value).lower()
    await session.rollback()


async def test_observation_log_append_only_rejects_delete(session: AsyncSession) -> None:
    store = await _make_store(session)
    obs = await _make_observation(session, store.entity_id)

    with pytest.raises(DBAPIError) as exc:
        await session.execute(
            text("DELETE FROM observation_log WHERE observation_id = :oid"),
            {"oid": obs.observation_id},
        )
        await session.commit()
    assert "append-only" in str(exc.value).lower()
    await session.rollback()


# --- Provenance hard constraint (preserved) ----------------------------------


async def test_observation_entity_id_not_null_rejected(session: AsyncSession) -> None:
    # status=fetch_failed keeps the value contract satisfied so entity_id NULL is
    # the sole violation.
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.execute(
            text(
                "INSERT INTO observation_log "
                "(observation_id, entity_id, feature, value_type, source, producer, "
                " observed_at, confidence, status) "
                "VALUES (:oid, NULL, 'product_count', 'number', 'products_json', "
                " 'mes_crawler_v1', now(), 'certain', 'fetch_failed')"
            ),
            {"oid": uuid.uuid4()},
        )
        await session.commit()
    await session.rollback()


async def test_knowledge_source_observation_id_not_null_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.execute(
            text(
                "INSERT INTO knowledge_state "
                "(entity_id, feature, value_type, value_raw, value_number, producer, "
                " source_observation_id, observed_at, confidence, selection_rule_version, "
                " updated_at) "
                "VALUES (:eid, 'product_count', 'number', '42', 42, 'mes_crawler_v1', NULL, "
                " now(), 'certain', 'v1', now())"
            ),
            {"eid": store.entity_id},
        )
        await session.commit()
    await session.rollback()


async def test_knowledge_state_allows_update(session: AsyncSession) -> None:
    store = await _make_store(session)
    obs = await _make_observation(session, store.entity_id)

    ks = _ks(store.entity_id, obs.observation_id)
    session.add(ks)
    await session.commit()

    # Materialised-view semantics: overwriting the current value must succeed.
    ks.value_raw = "99"
    ks.value_number = 99
    await session.commit()

    row = await session.execute(
        text(
            "SELECT value_number FROM knowledge_state "
            "WHERE entity_id = :eid AND feature = 'product_count'"
        ),
        {"eid": store.entity_id},
    )
    assert row.scalar_one() == 99


# --- Controlled-string CHECK (preserved) -------------------------------------


@pytest.mark.parametrize(
    ("column", "bad_value"),
    [
        ("status", "broken"),
        ("confidence", "very_sure"),
        ("value_type", "float128"),
        ("source", "smoke_signals"),
    ],
)
async def test_controlled_string_checks_reject_illegal(
    session: AsyncSession, column: str, bad_value: str
) -> None:
    store = await _make_store(session)
    await _expect_rejected(session, _obs(store.entity_id, **{column: bad_value}))


# --- Discriminated union: observed correct combos writable -------------------


@pytest.mark.parametrize(
    ("value_type", "overrides"),
    [
        ("string", {"value_raw": "Aurora", "value_text": "Aurora", "value_number": None}),
        ("number", {"value_raw": "42", "value_number": 42}),
        ("boolean", {"value_raw": "true", "value_boolean": True, "value_number": None}),
        ("json", {"value_raw": "{}", "value_json": {"min": 10, "max": 90}, "value_number": None}),
        (
            "entity_ref",
            {"value_raw": "loox", "value_entity_id": LOOX_ENTITY_ID, "value_number": None},
        ),
    ],
)
async def test_observed_correct_value_type_combo_writable(
    session: AsyncSession, value_type: str, overrides: dict[str, Any]
) -> None:
    store = await _make_store(session)
    obs = _obs(store.entity_id, feature="f", value_type=value_type, **overrides)
    session.add(obs)
    await session.commit()
    assert obs.observation_id is not None


# --- Discriminated union: wrong combos rejected ------------------------------


async def test_value_type_string_but_number_filled_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    # value_type='string' but the number column is populated, text is empty.
    await _expect_rejected(
        session,
        _obs(store.entity_id, value_type="string", value_raw="x", value_text=None, value_number=42),
    )


async def test_value_type_number_but_two_columns_filled_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    # value_type='number' but BOTH value_text and value_number are non-null.
    await _expect_rejected(
        session,
        _obs(
            store.entity_id,
            value_type="number",
            value_raw="42",
            value_text="42",
            value_number=42,
        ),
    )


# --- Discriminated union: value_raw null / blank when observed ---------------


async def test_observed_value_raw_null_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    await _expect_rejected(session, _obs(store.entity_id, value_raw=None))


async def test_observed_value_raw_blank_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    await _expect_rejected(session, _obs(store.entity_id, value_raw="   "))


# --- Discriminated union: failed / not_found carry no value ------------------


@pytest.mark.parametrize("status", ["fetch_failed", "not_found"])
async def test_failed_with_value_raw_rejected(session: AsyncSession, status: str) -> None:
    store = await _make_store(session)
    await _expect_rejected(
        session,
        _obs(store.entity_id, status=status, value_raw="oops", value_number=None),
    )


@pytest.mark.parametrize("status", ["fetch_failed", "not_found"])
async def test_failed_with_typed_value_rejected(session: AsyncSession, status: str) -> None:
    store = await _make_store(session)
    await _expect_rejected(
        session,
        _obs(store.entity_id, status=status, value_raw=None, value_number=7),
    )


@pytest.mark.parametrize("status", ["fetch_failed", "not_found"])
async def test_failed_all_null_with_retained_value_type_writable(
    session: AsyncSession, status: str
) -> None:
    store = await _make_store(session)
    # value_type retained (describes expected type); all value columns NULL.
    obs = _obs(
        store.entity_id,
        feature="avg_price",
        value_type="number",
        status=status,
        value_raw=None,
        value_number=None,
    )
    session.add(obs)
    await session.commit()
    assert obs.observation_id is not None


# --- Discriminated union: knowledge_state contract ---------------------------


async def test_knowledge_correct_combo_writable(session: AsyncSession) -> None:
    store = await _make_store(session)
    obs = await _make_observation(session, store.entity_id)
    ks = _ks(store.entity_id, obs.observation_id, feature="theme_name",
             value_type="string", value_raw="Aurora", value_text="Aurora", value_number=None)
    session.add(ks)
    await session.commit()
    assert ks.entity_id == store.entity_id


async def test_knowledge_value_raw_blank_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    obs = await _make_observation(session, store.entity_id)
    await _expect_rejected(session, _ks(store.entity_id, obs.observation_id, value_raw=""))


async def test_knowledge_typed_mismatch_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    obs = await _make_observation(session, store.entity_id)
    # value_type='string' but value_number populated instead of value_text.
    await _expect_rejected(
        session,
        _ks(
            store.entity_id,
            obs.observation_id,
            value_type="string",
            value_raw="x",
            value_text=None,
            value_number=42,
        ),
    )


# --- source web_search + producer contract (Phase 1-C schema细化) -------------


async def test_source_web_search_writable(session: AsyncSession) -> None:
    store = await _make_store(session)
    obs = _obs(store.entity_id, feature="inferred_domain", source="web_search")
    session.add(obs)
    await session.commit()
    assert obs.source == "web_search"


@pytest.mark.parametrize("producer", ["mes_crawler_v1", "duckduckgo_v1", "manual_v1"])
async def test_producer_legal_values_writable(session: AsyncSession, producer: str) -> None:
    store = await _make_store(session)
    obs = _obs(store.entity_id, producer=producer)
    session.add(obs)
    await session.commit()
    assert obs.producer == producer


async def test_producer_illegal_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    await _expect_rejected(session, _obs(store.entity_id, producer="rogue_model"))


async def test_producer_null_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    await _expect_rejected(session, _obs(store.entity_id, producer=None))


async def test_knowledge_producer_null_rejected(session: AsyncSession) -> None:
    store = await _make_store(session)
    obs = await _make_observation(session, store.entity_id)
    await _expect_rejected(
        session, _ks(store.entity_id, obs.observation_id, producer=None)
    )
