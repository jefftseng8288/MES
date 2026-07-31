"""Phase 2.5 — insight 受控:應用層 registry + 寫入前驗證。

**為什麼受控放應用層,不下沉 DB CHECK(定案):**
每個 insight_type 有它自己的合法值域,必須受控 —— 否則不同實作者吐出不一致寫法
(High SKU / high_sku / HIGH),Phase 3 AI 會誤以為是不同東西。但**受控放哪一層,用
「穩不穩定 / 會不會頻繁改」決定:**

- 穩定既定事實(如 knowledge_state 的 currency、confidence 三級)→ 適合 DB CHECK。
- **insight 標籤是正在創造、會演化的東西** → 先放應用層。DB 硬鎖太早:每加一個標籤要改
  migration,且「受控清單只能往前加、難往後收」(Phase 1-C 踩過)。等 insight 類型穩定後,
  再考慮下沉 DB CHECK。

所以 `insight_store` 的 `insight_type` / `value_text` / `producer` **刻意沒有 DB CHECK**
—— 全擋在這裡。

**兩種 insight_type(第二批擴充):**
- **列舉型(enum)** —— value_text 必須在受控集合內(如 SKU_SCALE → {High/Medium/Low SKU})。
- **數值型(numeric)** —— value_text 存數值字串,驗證的是「格式可解析為數值」而非列舉。
  用於 GROWTH_VELOCITY 這種**刻意不設門檻、只記錄原始數值**的維度(見 producers 模組)。

**producer 也受控(第二批補上):** producer 是 Provider 競技場的核心欄位(未來要比較
rule_v1 / stat_v1 / LLM 的觀察力),寫法不一致(rule_v1 / rule_V1 / ruleV1)= 計分板壞掉。
各 Producer 類別自己聲明、由本 registry 統一收攏(見 `BaseInsightProducer.__init_subclass__`)。
同樣**不下沉 DB CHECK**(理由同 value_text:還在演化)。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

KIND_ENUM = "enum"
KIND_NUMERIC = "numeric"


class InsightValueError(ValueError):
    """(insight_type, value_text) 或 producer 不合法 —— 明確報錯,不靜默通過、不自動修正。"""


@dataclass(frozen=True)
class _TypeSpec:
    """一個 insight_type 的值域規格:列舉集合,或數值型。"""

    kind: str
    values: frozenset[str] | None = None  # 僅 enum 型有


# insight_type -> 值域規格。由各 Producer 登記進來。
_REGISTRY: dict[str, _TypeSpec] = {}
# 合法的 producer 識別集合(Provider 競技場的計分主體)。
_PRODUCERS: set[str] = set()


# --- 登記(第二批 Producer 的接口)---------------------------------------------


def register_insight_type(insight_type: str, allowed_values: Iterable[str]) -> None:
    """登記一個**列舉型** insight_type 及其合法 value 集合。

    重複登記同一 insight_type 但值集合不同 → 報錯(避免兩個 Producer 對同一維度各說各話)。
    """
    values = frozenset(allowed_values)
    if not values:
        raise InsightValueError(f"insight_type {insight_type!r} 的合法 value 集合不可為空")
    _register(insight_type, _TypeSpec(KIND_ENUM, values))


def register_numeric_insight_type(insight_type: str) -> None:
    """登記一個**數值型** insight_type(value_text 存數值字串,無列舉集合)。

    用於「刻意不設門檻、只記錄原始數值」的維度 —— 門檻是一種判斷,判斷該由下游行為決定。
    """
    _register(insight_type, _TypeSpec(KIND_NUMERIC))


def _register(insight_type: str, spec: _TypeSpec) -> None:
    existing = _REGISTRY.get(insight_type)
    if existing is not None and existing != spec:
        raise InsightValueError(
            f"insight_type {insight_type!r} 已登記為 {_describe(existing)},"
            f"不可改登記為 {_describe(spec)}(同一維度不可各說各話)"
        )
    _REGISTRY[insight_type] = spec


def _describe(spec: _TypeSpec) -> str:
    return f"{spec.kind}{sorted(spec.values) if spec.values else ''}"


def register_producer(producer: str) -> None:
    """登記一個合法的 producer 識別(由 Producer 類別自己聲明)。"""
    if not producer or producer != producer.strip():
        raise InsightValueError(f"producer {producer!r} 不合法(不可為空或含前後空白)")
    _PRODUCERS.add(producer)


# --- 查詢 ---------------------------------------------------------------------


def registered_types() -> tuple[str, ...]:
    """目前已登記的 insight_type(排序,便於檢視)。"""
    return tuple(sorted(_REGISTRY))


def registered_producers() -> tuple[str, ...]:
    """目前已登記的 producer(排序)。"""
    return tuple(sorted(_PRODUCERS))


def type_kind(insight_type: str) -> str:
    """某 insight_type 是列舉型還是數值型;未登記 → 報錯。"""
    return _spec(insight_type).kind


def allowed_values(insight_type: str) -> frozenset[str]:
    """某**列舉型** insight_type 的合法 value 集合;未登記或非列舉型 → 報錯。"""
    spec = _spec(insight_type)
    if spec.kind != KIND_ENUM or spec.values is None:
        raise InsightValueError(f"insight_type {insight_type!r} 是 {spec.kind} 型,無列舉值集合")
    return spec.values


def _spec(insight_type: str) -> _TypeSpec:
    spec = _REGISTRY.get(insight_type)
    if spec is None:
        raise InsightValueError(
            f"insight_type {insight_type!r} 未登記(已登記:{list(registered_types())})"
        )
    return spec


# --- 驗證(寫入 insight_store 前的守門)-----------------------------------------


def validate_insight_value(insight_type: str, value_text: str) -> None:
    """依 type 的種類走對應驗證:不合法就 raise,不靜默通過、不自動修正。

    未登記的 insight_type 一樣擋 —— 「沒登記」代表沒人定義過它的值域,不是放行理由。
    """
    spec = _spec(insight_type)
    if spec.kind == KIND_NUMERIC:
        try:
            float(value_text)
        except (TypeError, ValueError) as exc:
            raise InsightValueError(
                f"insight_type {insight_type!r} 是數值型,value_text {value_text!r} 無法解析為數值"
            ) from exc
        return
    assert spec.values is not None
    if value_text not in spec.values:
        raise InsightValueError(
            f"value_text {value_text!r} 不是 insight_type {insight_type!r} 的合法值"
            f"(合法:{sorted(spec.values)})"
        )


def validate_producer(producer: str) -> None:
    """producer 守門:未登記 → 明確報錯(防 rule_v1 / rule_V1 / ruleV1 混寫壞掉計分板)。"""
    if producer not in _PRODUCERS:
        raise InsightValueError(
            f"producer {producer!r} 未登記(已登記:{list(registered_producers())})"
        )
