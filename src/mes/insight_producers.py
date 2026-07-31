"""Phase 2.5 — Insight 實作者(Producer)。

**Producer = 純函數類別,不自己撈 DB。** 設計文件定 Producer 是純函數,但 GrowthStatProducer
需要歷史資料 —— 解法:**Producer 只「聲明」它需要什麼**(`required_features` / `required_history`),
由 InsightEngine 統一撈齊、打包成記憶體 `InsightContext` 交給它。Producer 拿到的永遠是記憶體
物件,**不碰 DB** → 純函數、可測試、可重現。

**加新 Producer = 加一個類別丟進 Engine 的 List**,不動 Engine。類別在定義時透過
`__init_subclass__` **自己向 registry 聲明** insight_type 與 producer 識別。

**★ 紅線:只做描述,不做預測。** 出現「下個月 / 即將 / 應該會」等未來時間軸 + 賭注 → 那是
Hypothesis(Phase 3),立即停修。成長率是「對歷史的統計」(既定軌跡)→ 是描述,合格。
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from mes.insight_registry import (
    KIND_ENUM,
    KIND_NUMERIC,
    register_insight_type,
    register_numeric_insight_type,
    register_producer,
)

# --- Context:Engine 打包給 Producer 的記憶體快照(Producer 只看這個)-------------


@dataclass(frozen=True)
class Fact:
    """knowledge_state 的一列當前事實(Producer 需要的部分)。"""

    feature: str
    value_number: float | None
    value_text: str | None
    confidence: str


@dataclass(frozen=True)
class HistoryPoint:
    """observation_log 的一筆 observed 歷史點(依 observed_at 排序)。"""

    observed_at: datetime
    value_number: float | None


@dataclass(frozen=True)
class InsightContext:
    """某 entity 的 Facts + 所需歷史 —— Producer 的**唯一**輸入(不碰 DB)。"""

    entity_id: uuid.UUID
    facts: dict[str, Fact] = field(default_factory=dict)
    history: dict[str, list[HistoryPoint]] = field(default_factory=dict)


# --- Producer 的輸出:產出一筆,或帶具體原因的「沒產出」-------------------------


@dataclass(frozen=True)
class InsightDraft:
    """Producer 吐出的 insight 結構(尚未驗證/寫入)。"""

    value_text: str
    confidence: str
    source_knowledge_refs: list[dict[str, str]]


@dataclass(frozen=True)
class Skip:
    """沒產出 + **具體載明缺什麼**(進 insight_run_log,不進 insight_store)。"""

    reason: str
    detail: dict[str, Any] | None = None


# --- 基底類別 -----------------------------------------------------------------


class BaseInsightProducer(ABC):
    """所有 Producer 的基底:聲明自己要什麼、產出什麼,並自動向 registry 登記。

    子類別必須設:`insight_type` / `producer` / `value_kind`(enum 時另設 `values`)。
    """

    insight_type: str
    producer: str
    value_kind: str = KIND_ENUM
    values: tuple[str, ...] = ()
    # 聲明需要的當前 Facts / 歷史 feature —— Engine 據此撈齊打包(Producer 不碰 DB)。
    required_features: tuple[str, ...] = ()
    required_history: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """類別定義時自己向 registry 聲明 —— 加新 Producer 不必記得另外去登記。"""
        super().__init_subclass__(**kwargs)
        if getattr(cls, "abstract", False):
            return
        register_producer(cls.producer)
        if cls.value_kind == KIND_NUMERIC:
            register_numeric_insight_type(cls.insight_type)
        else:
            register_insight_type(cls.insight_type, cls.values)

    @abstractmethod
    def produce(self, ctx: InsightContext) -> InsightDraft | Skip:
        """純函數:給定 Context → 吐出 insight 結構,或帶具體原因的 Skip。"""

    def _refs(self, entity_id: uuid.UUID, *features: str) -> list[dict[str, str]]:
        """Provenance:此 insight 基於哪幾條 (entity_id, feature) 事實。"""
        return [{"entity_id": str(entity_id), "feature": f} for f in features]


# --- SKURuleProducer(列舉型:Rule 實作者)--------------------------------------

FEATURE_PRODUCT_COUNT = "product_count"
SKU_LOW_MAX = 100  # ≤100 → Low
SKU_MEDIUM_MAX = 500  # 101–500 → Medium;>500 → High


class SKURuleProducer(BaseInsightProducer):
    """product_count → SKU 規模標籤(純描述:520 > 500 是當下狀態定義,無賭注成分)。

    門檻(Jeff 定案,連續無縫,不留空隙):
      ≤100 Low SKU / 101–500 Medium SKU / >500 High SKU
    """

    insight_type = "SKU_SCALE"
    producer = "rule_v1"
    value_kind = KIND_ENUM
    values = ("High SKU", "Medium SKU", "Low SKU")
    required_features = (FEATURE_PRODUCT_COUNT,)

    def produce(self, ctx: InsightContext) -> InsightDraft | Skip:
        fact = ctx.facts.get(FEATURE_PRODUCT_COUNT)
        if fact is None:
            return Skip(
                "knowledge 無 product_count 值(該 entity 未曾成功觀測到商品數)",
                {"missing_feature": FEATURE_PRODUCT_COUNT},
            )
        if fact.value_number is None:
            return Skip(
                "knowledge 的 product_count 非數值(value_number 為空)",
                {"missing_feature": FEATURE_PRODUCT_COUNT, "reason_kind": "not_numeric"},
            )
        count = float(fact.value_number)
        if count <= SKU_LOW_MAX:
            label = "Low SKU"
        elif count <= SKU_MEDIUM_MAX:
            label = "Medium SKU"
        else:
            label = "High SKU"
        # confidence 誠實沿用來源事實的信心度:事實若是 estimated,這個標籤也不該自稱 certain。
        return InsightDraft(
            value_text=label,
            confidence=fact.confidence,
            source_knowledge_refs=self._refs(ctx.entity_id, FEATURE_PRODUCT_COUNT),
        )


# --- GrowthStatProducer(數值型:Statistics 實作者)------------------------------

# ★ 目標 feature。MES 目前**尚未採集** review_count(不在 Feature Taxonomy v1 的 9 個
# 市場特徵內、Phase 1-D 也沒抓),故本 Producer 對真實資料目前必然產不出、只會記錄
# 「無 observed 歷史」—— 這是資料採集範圍的問題,不是計算邏輯的問題。待 review_count
# 納入採集後,改這一個常數即可(或直接沿用)。
FEATURE_REVIEW_COUNT = "review_count"

GROWTH_TARGET_DAYS = 30
GROWTH_WINDOW_MIN_DAYS = 25  # 容忍窗下界
GROWTH_WINDOW_MAX_DAYS = 35  # 容忍窗上界
# value_text 的數值格式(統一精度,便於日後聚合/比較):比率,小數 6 位。
# 例:+25% → "0.250000";−50% → "-0.500000"。
GROWTH_VALUE_FORMAT = "{:.6f}"


class GrowthStatProducer(BaseInsightProducer):
    """review_count 的 30 天成長率 —— **刻意不設門檻,只記錄實際數值**(Jeff 定案)。

    為什麼不設門檻:**門檻是一種判斷,判斷該由「後面要做什麼行為」決定。** 現階段沒有任何
    下游行為,設門檻等於憑空造判斷,還會丟失資訊(+19% 與 −50% 被壓成同一類「Growth」,
    差異永遠不見)。Phase 4 要怎麼切,由那時的實際行為決定 —— 現在的職責是誠實記錄原始數值。

    「30 天前的值」取法:取**最接近 30 天前**的那筆 observed,容忍窗 25–35 天;窗內找不到
    → 資料不足,不產出並記錄缺什麼。因窗有容忍(未必剛好 30 天),confidence 記 `estimated`。
    """

    insight_type = "GROWTH_VELOCITY"
    producer = "stat_v1"
    value_kind = KIND_NUMERIC
    required_history = (FEATURE_REVIEW_COUNT,)

    def produce(self, ctx: InsightContext) -> InsightDraft | Skip:
        hist = [p for p in ctx.history.get(FEATURE_REVIEW_COUNT, []) if p.value_number is not None]
        if not hist:
            return Skip(
                f"{FEATURE_REVIEW_COUNT} 無 observed 歷史(需當前 + 約 30 天前兩點)",
                {"history_points": 0, "needed_feature": FEATURE_REVIEW_COUNT},
            )
        if len(hist) == 1:
            return Skip(
                f"{FEATURE_REVIEW_COUNT} 僅 1 筆 observed(需當前 + 約 30 天前兩點)",
                {"history_points": 1, "needed_feature": FEATURE_REVIEW_COUNT},
            )

        current = hist[-1]  # 已依 observed_at 排序,最後一筆 = 當前
        span_days = (current.observed_at - hist[0].observed_at).days
        in_window = [
            p for p in hist[:-1]
            if GROWTH_WINDOW_MIN_DAYS
            <= (current.observed_at - p.observed_at).days
            <= GROWTH_WINDOW_MAX_DAYS
        ]
        if not in_window:
            return Skip(
                f"觀測跨度僅 {span_days} 天,"
                f"{GROWTH_WINDOW_MIN_DAYS}–{GROWTH_WINDOW_MAX_DAYS} 天窗內無 observed",
                {"history_points": len(hist), "span_days": span_days,
                 "window": [GROWTH_WINDOW_MIN_DAYS, GROWTH_WINDOW_MAX_DAYS]},
            )

        # 窗內取「最接近 30 天前」的那筆(距離相同時取較早的,決定性)。
        past = min(
            in_window,
            key=lambda p: (
                abs((current.observed_at - p.observed_at).days - GROWTH_TARGET_DAYS),
                p.observed_at,
            ),
        )
        base = float(past.value_number or 0.0)
        if base <= 0:
            return Skip(
                f"約 30 天前的 {FEATURE_REVIEW_COUNT} 基準值為 {base:g}(≤0),成長率無法計算",
                {"history_points": len(hist), "base_value": base,
                 "gap_days": (current.observed_at - past.observed_at).days},
            )

        rate = (float(current.value_number or 0.0) - base) / base
        return InsightDraft(
            value_text=GROWTH_VALUE_FORMAT.format(rate),
            # 容忍窗未必剛好 30 天 → 這是近似的 30 天成長率,誠實記 estimated。
            confidence="estimated",
            source_knowledge_refs=self._refs(ctx.entity_id, FEATURE_REVIEW_COUNT),
        )


# 預設的可插拔 Producer List —— 加新 Producer 只需在此加一個實例,不動 Engine。
DEFAULT_PRODUCERS: tuple[BaseInsightProducer, ...] = (SKURuleProducer(), GrowthStatProducer())
