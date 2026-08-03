"""Phase 3 — Pattern:**可執行的條件**,以及「這個 Pattern 對應哪些店」的查詢。

**★ 為什麼 Pattern 必須可執行,不能只是文字:**
Phase 4 執行時要問「這條假說要打哪些店」。若 pattern 只是散文(「高流量的美妝店」),
**Phase 4 拿到假說卻不知道要打誰** —— 假說再漂亮也沒辦法被執行、被證偽。

**形狀(第一版,Jeff 定案):** 一組條件的 **AND** 組合,存 JSONB:

    [{"insight_type": "SKU_SCALE", "value_text": "High SKU"},
     {"insight_type": "RATING_STATUS", "value_text": "Rating Warning"}]

**只支援 AND**(不做 OR / NOT)—— 夠用,且能直接翻成 SQL。未來要更複雜的邏輯再擴充。

本模組屬**資料層**(結構驗證 + 查詢翻譯),不含任何假說生成邏輯(那是第二批)。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mes.db.models import InsightStore
from mes.insight_registry import InsightValueError, validate_insight_value

KEY_INSIGHT_TYPE = "insight_type"
KEY_VALUE_TEXT = "value_text"


class PatternError(ValueError):
    """pattern 結構不合法 —— 明確報錯,不靜默通過、不自動修正。"""


def validate_pattern(pattern: Any) -> list[dict[str, str]]:
    """驗證 pattern 結構並回傳正規化後的條件列表。

    檢查三層:
      1. 是**非空的 list**(空 pattern = 「打所有店」,不是有意義的假說)。
      2. 每項是 dict 且**同時有** `insight_type` 與 `value_text`。
      3. `(insight_type, value_text)` **在 insight registry 裡登記過** —— 否則這條 pattern
         永遠撈不到任何店(拼錯的標籤不會有人符合),而且會靜默地撈到 0 家而非報錯。
    """
    if not isinstance(pattern, list) or not pattern:
        raise PatternError(f"pattern 必須是非空的 list,得到 {type(pattern).__name__}")
    conditions: list[dict[str, str]] = []
    for i, cond in enumerate(pattern):
        if not isinstance(cond, dict):
            raise PatternError(f"pattern[{i}] 必須是 dict,得到 {type(cond).__name__}")
        missing = [k for k in (KEY_INSIGHT_TYPE, KEY_VALUE_TEXT) if k not in cond]
        if missing:
            raise PatternError(f"pattern[{i}] 缺少欄位:{missing}")
        itype, value = str(cond[KEY_INSIGHT_TYPE]), str(cond[KEY_VALUE_TEXT])
        try:
            # 借用 insight 的受控驗證 —— pattern 引用的必須是真實存在的 insight 標籤。
            validate_insight_value(itype, value)
        except InsightValueError as exc:
            raise PatternError(f"pattern[{i}] 引用了未登記的 insight:{exc}") from exc
        conditions.append({KEY_INSIGHT_TYPE: itype, KEY_VALUE_TEXT: value})
    return conditions


async def stores_matching_pattern(
    session: AsyncSession, pattern: list[dict[str, Any]]
) -> list[uuid.UUID]:
    """撈出**同時符合所有條件**(AND)的 entity。

    作法:把每個條件當成 `(insight_type, value_text)` 的比對,取符合任一條件的列,
    依 entity 分組,**只留下「命中條件數 = 條件總數」的** —— 這就是 AND。
    (`insight_store` 有 `(entity_id, insight_type)` UNIQUE,故同一 entity 的同一
    insight_type 只會有一列,不會重複計數把 OR 誤算成 AND。)
    """
    conditions = validate_pattern(pattern)
    pairs = [
        (InsightStore.insight_type == c[KEY_INSIGHT_TYPE])
        & (InsightStore.value_text == c[KEY_VALUE_TEXT])
        for c in conditions
    ]
    any_match = pairs[0]
    for p in pairs[1:]:
        any_match = any_match | p

    rows = await session.execute(
        select(InsightStore.entity_id)
        .where(any_match)
        .group_by(InsightStore.entity_id)
        .having(func.count() == len(conditions))
    )
    return [r for (r,) in rows.all()]
