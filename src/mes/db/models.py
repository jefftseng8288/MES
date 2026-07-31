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
# review_widget added 2026-07-31: 評論數來自 review app 自己的 widget 頁面(第三方),
# 不是店家的 html_page —— 沿用 web_search 先例,寧可補誠實新值,不借語義不符的舊值。
SOURCES = (
    "html_page", "products_json", "html_signature", "web_search", "review_widget",
    "manual", "monitor",
)
CONFIDENCE_LEVELS = ("certain", "inferred", "estimated")
STATUSES = ("observed", "fetch_failed", "not_found")

# producer = 這筆值由哪個「方法/模型」產生(語義版本 = 責任主體)。每筆 observation 必填。
# 與 P6 的 "provider"(外部資料源)刻意區分:producer 答「誰做出這筆裁決」。
#   mes_crawler_v1 — 直接抓取/讀取的責任主體(observed_on_app_store + 未來 9 個市場特徵)。
#   duckduckgo_v1  — DuckDuckGo 推論 inferred_domain 的責任主體。
#   manual_v1      — 人工校正/手動餵入的責任主體。
PRODUCERS = ("mes_crawler_v1", "duckduckgo_v1", "manual_v1", "mes_store_crawler_v1")

# Store feature-harvest processing state (Phase 1-D). NOT an observation — a mutable
# system-internal "to-do" marker. 'pending' 有 domain 待抓 / 'done' 已抓 / 'failed' 戳失敗可重試。
HARVEST_STATUSES = ("pending", "done", "failed")

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
# knowledge_state (Phase 2, 決定 2): value presence is gated by observed_at.
#   observed_at = 被取為當前值那筆 observed 的時間(= 值的新鮮度)。第二批把冗餘的 last_observed_at
#   併回 observed_at(投影驗證兩者恆等,2905 列 0 不符)。
#   observed_at IS NULL     -> 從無成功: value 全 NULL,current_status ∈ failed/not_found(防禦守門;
#                             observed_at 保持 NOT NULL,v1 不建無值列,保留 Provenance 鐵律)。
#   observed_at IS NOT NULL -> 曾成功: value 必非 NULL(discriminated union)。
# 同 observation_log 哲學:DB 物理拒絕不老實的混合狀態(投影代碼寫錯也擋得住)。
_KNOWLEDGE_VALUE_RAW_CHECK = (
    "(observed_at IS NULL AND value_raw IS NULL) "
    "OR (observed_at IS NOT NULL AND value_raw IS NOT NULL AND btrim(value_raw) <> '')"
)
_KNOWLEDGE_TYPED_CHECK = (
    f"(observed_at IS NULL AND {_all_typed_null()}) "
    f"OR (observed_at IS NOT NULL AND ({_exactly_one_typed()}))"
)
# 從無成功觀測(observed_at IS NULL)時 current_status 不能是 observed(observed 蘊含曾成功)。
_KNOWLEDGE_STATUS_CONSISTENCY_CHECK = "observed_at IS NOT NULL OR current_status <> 'observed'"


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
    # none_as_null: Python None -> SQL NULL (not JSON 'null'), else value_typed CHECK breaks.
    value_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
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
        CheckConstraint(_sql_in("current_status", STATUSES), name="ck_knowledge_current_status"),
        CheckConstraint(_KNOWLEDGE_VALUE_RAW_CHECK, name="ck_knowledge_value_raw"),
        CheckConstraint(_KNOWLEDGE_TYPED_CHECK, name="ck_knowledge_value_typed"),
        CheckConstraint(
            _KNOWLEDGE_STATUS_CONSISTENCY_CHECK, name="ck_knowledge_status_consistency"
        ),
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
    # none_as_null: Python None -> SQL NULL (not JSON 'null'), else value_typed CHECK breaks.
    value_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
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
    # --- Phase 2 (決定 2) --------------------------------------------------------
    # current_status = 最近一次「嘗試」觀測的結果(可能是比 observed_at 更晚的一次 fetch_failed)。
    # 與 value/observed_at 是不同時間點:值(及其 observed_at 新鮮度)可舊、狀態反映最新一次嘗試。
    # 例:product_count=196 於半年前 observed、今天 fetch_failed → value=196、observed_at=半年前、
    #     current_status=fetch_failed(保留舊值 + 誠實標明當前狀態)。NOT NULL。
    # 註:第二批已把第一批的 last_observed_at 併回 observed_at(投影驗證兩者恆等)。
    current_status: Mapped[str] = mapped_column(String(16), nullable=False)


class StoreHarvestState(Base):
    """系統處理狀態(非觀測):某 store 的 9 個市場 feature 抓取進度。

    刻意與 entity 純淨、Append-Only 分離——這是「待處理清單」的系統標記,可自由 UPDATE,
    不是市場觀測資料。放獨立表(非 entity 加欄)以保持 entity 只作觀測掛載點。
    """

    __tablename__ = "store_harvest_state"
    __table_args__ = (
        CheckConstraint(_sql_in("status", HARVEST_STATUSES), name="ck_harvest_status"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("entity.entity_id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class AlertLog(Base):
    """警鈴觸發的結構化記錄:異常 + 診斷出的原因 + 當天數據。

    這是「未來自動生成調整策略」要學的燃料——每次叫痛都留下「異常+原因+證據」。
    `alert_type` 刻意**不** CHECK 鎖(彈性擴充新異常類型,如同 feature);`detail` 用 JSONB
    存當天三批數據 + 門檻,結構開放。**這只記錄+推播,不做任何自動調整。**
    """

    __tablename__ = "alert_log"

    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    taiwan_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD (巡檢當天)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)  # 不 CHECK 鎖,利擴充
    diagnosis: Mapped[str] = mapped_column(Text, nullable=False)  # 判讀出的最可能原因(人讀)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # Telegram 是否送達(留 credential 空時為 False)。
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class InsightStore(Base):
    """Phase 2.5 — 對 Knowledge Facts 做「標籤化 / 語義壓縮」的結果。

    **完全獨立於 knowledge_state,絕不混:** knowledge_state = 中立事實(product_count=520);
    insight_store = 對事實的描述性標籤(→ High SKU)。一個 entity 可多列(每個 insight_type
    一列,如同時 SKU_SCALE + GROWTH_VELOCITY)。

    **這是 Describe,不是 Predict。** 出現「下個月 / 即將 / 應該會」等未來時間軸 + 賭注
    → 那是 Hypothesis(Phase 3),不屬本層。
    """

    __tablename__ = "insight_store"
    __table_args__ = (
        # 與 knowledge_state 的 (entity_id, feature) 主鍵同構:一個 entity 的每個 insight
        # 維度只有一個當前值。第二批全量重算走此鍵 upsert(不清空重寫)→ insight_id 穩定,
        # 未來 Phase 3 的 Hypothesis 才引用得住。
        UniqueConstraint("entity_id", "insight_type", name="uq_insight_entity_type"),
        CheckConstraint(_sql_in("confidence", CONFIDENCE_LEVELS), name="ck_insight_confidence"),
    )

    insight_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("entity.entity_id"), nullable=False
    )
    # insight_type / value_text / producer 刻意**不** CHECK 鎖 —— 見 mes.insight_registry:
    # insight 標籤還在快速演化,受控放應用層(registry + 寫入前驗證),等穩定再考慮下沉 DB。
    # 對比 confidence(Phase 0 既定三級、穩定)→ 適合 DB CHECK,故上面有鎖。
    insight_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 如 SKU_SCALE
    value_text: Mapped[str] = mapped_column(String(255), nullable=False)  # 如 High SKU(應用層受控)
    producer: Mapped[str] = mapped_column(String(50), nullable=False)  # 如 rule_v1 / stat_v1
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    # ★ generated_at = 「這個**描述**何時被產生」= 實際執行時間(now()),與 knowledge_state 的
    # observed_at(「這個**事實**何時被觀測」,必由 observation_log 投影、禁用系統時間)語義不同。
    # 已知且接受的代價:insight_store **不是**冪等重建的(今天重算與明天重算 generated_at 不同)。
    # 可接受 —— insight 是「每天對當前 Knowledge 重新描述一次」的快照,不是歷史真相的投影;
    # 真相在 observation_log,insight 只是描述層。**這不違反 Phase 2 的純函數原則。**
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    # Provenance:此 insight 基於哪幾條 knowledge 事實 —— 一組 (entity_id, feature)。
    # 第一版不追求「完全重現當時的確切值」(有 generated_at 且每天重算,精確重現非必要需求)。
    source_knowledge_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), nullable=False
    )


class JobRunLog(Base):
    """每條鏈路「跑完就記一筆」的心跳(per-run),讓系統能分辨三種狀態。

    **為什麼需要主動上報,而不是去查產出:** 兩種失效在「產出」上長得一樣,但修法天差地遠 ——
      - `store_harvest` 曾**有跑但原地打轉** 16 天(每批都挑同 3 家假網域)。
      - `projection` / `insight` 曾**根本沒被 load**、從未執行。
    只查產出時兩者都只表現為「沒有新資料」,分不出來。有心跳才能分辨
    **「沒跑」/「跑了但沒產出」/「跑了有產出」**。

    與既有兩個記錄層**不重疊**:`observation_log` 是 per-observation 的事實、
    `insight_run_log` 是 per-entity 的未產出原因、本表是 **per-run 的執行心跳**。
    baseline 既有的 HealthReport 數字**餵進本表的 summary**,不另記一份。

    `summary` 各 job 內容不同,但都必須足以讓警鈴判斷「產出 0 是正常閒置還是異常」
    (例:harvest 記候選池總數 / 被間隔跳過幾家 / 實際挑幾家)。
    """

    __tablename__ = "job_run_log"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # 不 CHECK 鎖(同 alert_log):未來加新鏈路不必改 schema。
    job: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # success / failed
    # 產出摘要(即使產出為 0 也要寫 —— 沒產出 ≠ 沒跑)。
    summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class InsightRunLog(Base):
    """Phase 2.5 — 每次全量重算時,記錄「某 entity 的某 insight_type **為什麼沒產出**」。

    **為什麼要有:** insight_store「查無此列」會靜默掉三種完全不同的情況 ——
    (a) 資料不足算不出、(b) 算得出但無此描述、(c) 根本沒處理到這家店。
    分不出來 = 失敗訊號被偽裝成沒事,違反「失敗不偽裝」鐵律。

    **為什麼不塞進 insight_store:** value_text NOT NULL,塞「資料不足」會把「系統的計算狀態」
    混進「市場描述」,污染語義(同 observation_log 不把 fetch_failed 記成 0)。故獨立記錄,
    比照 `alert_log` 的精神:結構化、可查詢、可聚合。

    `reason` 要**足夠具體、載明缺什麼**(例:「review_count 僅 1 筆 observed」、
    「觀測跨度僅 12 天,25–35 天窗內無 observed」),才能支撐未來「要不要繼續觀察這家店」
    的決策。**本表只做記錄;停止觀察的決策機制不在 Phase 2.5 做。**
    """

    __tablename__ = "insight_run_log"

    run_log_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # 同一次全量重算的所有列共用同一個 run_at(可據此聚合「某次執行」)。
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("entity.entity_id"), nullable=False
    )
    insight_type: Mapped[str] = mapped_column(String(50), nullable=False)
    producer: Mapped[str] = mapped_column(String(50), nullable=False)
    # 人讀的具體原因(載明缺什麼),不是「資料不足」這種無資訊的字串。
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # 機器讀的結構化細節(如 {"history_points": 1, "span_days": 12}),供聚合分析。
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
