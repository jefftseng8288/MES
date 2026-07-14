"""MES core ORM models — Phase 1-B.

Three tables that form the data foundation, mapped strictly to the Phase 0
schema docs:

- ``entity``           — Entity Model v1, §3
- ``observation_log``  — Observation Schema v1, §2 (唯一真相, Append-Only)
- ``knowledge_state``  — Knowledge Schema v1, §2 (物化視圖, 可 UPDATE)

Controlled string lists are enforced with VARCHAR + CHECK (not native ENUM),
so升版 only touches a CHECK constraint. All primary-key IDs are UUIDs generated
on the Python (write) side, not by the DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mes.db.base import Base

# --- Controlled vocabularies (source of truth for the CHECK constraints) ------
# These mirror the Phase 0 schema docs. Adding a value = bump the doc version and
# alter the CHECK — deliberately easier than ALTER TYPE on a native enum.

# store_name_seed (Phase 1-C): a Store Name scraped from the App Store review page,
# before its domain is inferred. reserved (not yet in CHECK): contact/experiment/campaign.
ENTITY_TYPES = ("store", "review_app", "store_name_seed")
VALUE_TYPES = ("string", "number", "boolean", "entity_ref", "json")
# source = 透過什麼「管道」觀測到。web_search added Phase 1-C (inferred_domain via
# DuckDuckGo web search — do NOT record that as html_page; that lies about provenance).
SOURCES = ("html_page", "products_json", "html_signature", "web_search", "manual", "monitor")
CONFIDENCE_LEVELS = ("certain", "inferred", "estimated")
STATUSES = ("observed", "fetch_failed", "not_found")

# producer = 這筆值由哪個「方法/模型」產生(語義版本 = 責任主體)。每筆 observation 必填。
# 與 P6 的 "provider"(外部資料源)刻意區分:producer 答「誰做出這筆裁決」。
#   mes_crawler_v1 — 直接抓取/讀取的責任主體(observed_on_app_store + 未來 9 個市場特徵)。
#   duckduckgo_v1  — DuckDuckGo 推論 inferred_domain 的責任主體。
#   manual_v1      — 人工校正/手動餵入的責任主體。
PRODUCERS = ("mes_crawler_v1", "duckduckgo_v1", "manual_v1")

# Feature Taxonomy v1 — Market Features: describe the state of a Reality entity.
# Not CHECK-locked: feature is an intentionally extensible vocabulary (new features
# bump the taxonomy doc, not the schema). Listed for reference only.
FEATURES_V1 = (
    "uses_review_app",
    "theme_name",
    "product_count",
    "avg_price",
    "price_range",
    "country",
    "language",
    "currency",
    "is_active",
)

# Meta-Features (Phase 1-C) — describe MES's own cognition/interfacing events
# (scraping, inference), not a Reality entity's state:
#   observed_on_app_store — a Store Name was seen on the App Store review page.
#   inferred_domain       — a domain was (or was not) inferred from a Store Name.
FEATURES_META = (
    "observed_on_app_store",
    "inferred_domain",
)


def _sql_in(column: str, values: tuple[str, ...]) -> str:
    """Render ``column IN ('a', 'b', ...)`` for a CHECK constraint."""
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _now() -> datetime:
    return datetime.now(UTC)


# --- Discriminated-union value container --------------------------------------
# observation_log and knowledge_state share the SAME value columns: Knowledge_State
# is a projection of Observation_Log, so the value container shape must be identical
# — otherwise the projection would need type conversion (a corruption point).
#
# value_raw holds ONLY the feature's original value 原貌 (e.g. country "Taiwan",
# avg_price "$48.00 USD"). It must NOT hold fetch/inference evidence — HTTP status,
# raw URLs, parser errors, matched HTML selectors, signature evidence, search
# candidates. Those are execution metadata of the *act of observing*, not the
# feature value, and will get their own evidence/execution columns later. Mixing
# them in corrupts value_raw's semantics.
#
# value_type → typed column mapping:
#   'string'     (theme_name / country / language / currency) → value_text
#   'number'     (product_count / avg_price)                  → value_number
#   'json'       (price_range)                                → value_json
#   'boolean'    (is_active)                                  → value_boolean
#   'entity_ref' (uses_review_app)                            → value_entity_id

_TYPED_VALUE_COLUMNS = (
    "value_text",
    "value_number",
    "value_boolean",
    "value_json",
    "value_entity_id",
)
_VALUE_TYPE_TO_COLUMN = {
    "string": "value_text",
    "number": "value_number",
    "boolean": "value_boolean",
    "json": "value_json",
    "entity_ref": "value_entity_id",
}


def _all_typed_null() -> str:
    """All typed value columns are NULL (used for fetch_failed / not_found)."""
    return " AND ".join(f"{c} IS NULL" for c in _TYPED_VALUE_COLUMNS)


def _exactly_one_typed() -> str:
    """OR over value_type branches: the matching typed column is non-null, the rest NULL."""
    branches = []
    for vtype, col in _VALUE_TYPE_TO_COLUMN.items():
        parts = [f"value_type = '{vtype}'", f"{col} IS NOT NULL"]
        parts += [f"{other} IS NULL" for other in _TYPED_VALUE_COLUMNS if other != col]
        branches.append("(" + " AND ".join(parts) + ")")
    return " OR ".join(branches)


# observation_log: two-layer contract keyed on status.
# Layer 1 — status ↔ value_raw (observed must be non-null AND non-blank; failed/not_found null).
_OBS_VALUE_RAW_CHECK = (
    "(status = 'observed' AND value_raw IS NOT NULL AND btrim(value_raw) <> '') "
    "OR (status IN ('fetch_failed', 'not_found') AND value_raw IS NULL)"
)
# Layer 2 — status ↔ value_type ↔ typed columns.
# value_type is retained on failed/not_found (it describes the feature's expected type).
_OBS_TYPED_CHECK = (
    f"(status = 'observed' AND ({_exactly_one_typed()})) "
    f"OR (status IN ('fetch_failed', 'not_found') AND {_all_typed_null()})"
)
# knowledge_state: only projects status='observed' rows; no status column, no failed branch.
_KNOWLEDGE_VALUE_RAW_CHECK = "value_raw IS NOT NULL AND btrim(value_raw) <> ''"
_KNOWLEDGE_TYPED_CHECK = _exactly_one_typed()


class Entity(Base):
    """觀測掛載點 + 去重自然鍵。Entity 幾乎不帶屬性(會變的事實都是 Observation)。"""

    __tablename__ = "entity"
    __table_args__ = (
        UniqueConstraint("entity_type", "canonical_key", name="uq_entity_type_canonical_key"),
        CheckConstraint(_sql_in("entity_type", ENTITY_TYPES), name="ck_entity_entity_type"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class ObservationLog(Base):
    """Append-Only 事件日誌 = 唯一真相。DB 層以 trigger 物理拒絕 UPDATE/DELETE。"""

    __tablename__ = "observation_log"
    __table_args__ = (
        CheckConstraint(_sql_in("value_type", VALUE_TYPES), name="ck_observation_value_type"),
        CheckConstraint(_sql_in("source", SOURCES), name="ck_observation_source"),
        CheckConstraint(_sql_in("producer", PRODUCERS), name="ck_observation_producer"),
        CheckConstraint(_sql_in("confidence", CONFIDENCE_LEVELS), name="ck_observation_confidence"),
        CheckConstraint(_sql_in("status", STATUSES), name="ck_observation_status"),
        CheckConstraint(_OBS_VALUE_RAW_CHECK, name="ck_observation_value_raw_status"),
        CheckConstraint(_OBS_TYPED_CHECK, name="ck_observation_value_typed"),
        # batch_id format: YYYY-MM-DD-NN (Taiwan date + 當天批序,≥2 位).
        CheckConstraint(
            "batch_id ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2,}$'",
            name="ck_observation_batch_id",
        ),
    )

    observation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Provenance 硬約束(雙層):ORM nullable=False + DB NOT NULL。
    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("entity.entity_id"), nullable=False
    )
    feature: Mapped[str] = mapped_column(String(64), nullable=False)  # 受控詞彙,見 FEATURES_V1
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # value_raw = feature 原始值原貌 only (see discriminated-union note above).
    value_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Discriminated-union typed columns: exactly one matches value_type when observed.
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_number: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    value_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("entity.entity_id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # 管道
    # producer = 產生此值的方法/模型(NOT NULL,雙層:ORM + DB)。
    producer: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # crawler_version = 執行當時的實體程式碼版本(Git commit SHA-1)。只存 hash,不塞別的。
    crawler_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # batch_id = 這筆觀測由哪一次 run 產生(Provenance 延伸;格式 YYYY-MM-DD-NN)。NOT NULL。
    # 只在 observation_log(觀測事件屬性);knowledge_state 不加(當前值可能混不同批)。
    batch_id: Mapped[str] = mapped_column(String(16), nullable=False)


class KnowledgeState(Base):
    """物化視圖:每個 (entity, feature) 只有一列當前值。允許 UPDATE(取值規則覆寫)。"""

    __tablename__ = "knowledge_state"
    __table_args__ = (
        CheckConstraint(_sql_in("value_type", VALUE_TYPES), name="ck_knowledge_value_type"),
        CheckConstraint(_sql_in("producer", PRODUCERS), name="ck_knowledge_producer"),
        CheckConstraint(_sql_in("confidence", CONFIDENCE_LEVELS), name="ck_knowledge_confidence"),
        CheckConstraint(_KNOWLEDGE_VALUE_RAW_CHECK, name="ck_knowledge_value_raw"),
        CheckConstraint(_KNOWLEDGE_TYPED_CHECK, name="ck_knowledge_value_typed"),
    )

    # 複合主鍵 (entity_id, feature)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("entity.entity_id"), primary_key=True
    )
    feature: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Same discriminated-union value container as observation_log (identical shape:
    # knowledge_state is the projection, so no type conversion on the way in).
    value_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_number: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    value_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("entity.entity_id"), nullable=True
    )
    # producer carried from the source observation (NOT NULL, 雙層).
    producer: Mapped[str] = mapped_column(String(32), nullable=False)
    # Provenance 硬約束(雙層):此當前值必須追得回來源觀測。
    source_observation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("observation_log.observation_id"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    selection_rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
