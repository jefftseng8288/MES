"""Phase 3 — `predicted_outcome`(predicate)的應用層受控。

**為什麼受控放應用層,不下沉 DB CHECK(定案):**
`predicted_outcome` 的合法值**取決於 Phase 4 實際用哪些武器,而那還沒定** —— Roadmap 的
武器庫優先序是「listing 優化 / Build in Public」第 1、cold email 第 3。現在若定死
`EMAIL_OPEN` / `HIGHER_CLICK_THROUGH_RATE` 這類值,**很可能定出一組根本用不到的**;
而受控清單**加值容易、收回難**(Phase 1-C 踩過:窄化 CHECK 的 downgrade 會被既有資料擋住)。

**判準與 Phase 2.5 的 `value_text` 相同:用「穩不穩定 / 會不會頻繁改」決定受控放 DB 還是應用層。**
對比同一張表的 `status` / `confidence`(已定義完整、穩定)→ 有 DB CHECK。**同表刻意兩種待遇。**

**★ 第一版只登記「已經確定會用」的少數 predicate,不預先窮舉。**
目前只登記 `SWAP_APP_INTENT` —— 它是設計文件第一節唯一舉出的具體 predicate,非臆造。
其餘等 Phase 4 的武器定了再登記。
"""

from __future__ import annotations


class PredicateError(ValueError):
    """predicate 不合法 —— 明確報錯,不靜默通過、不自動修正。"""


# 合法的 predicted_outcome 集合。刻意起始很小,由實際需要驅動增加。
_PREDICATES: set[str] = set()


def register_predicate(predicate: str) -> None:
    """登記一個合法的 `predicted_outcome`。

    Phase 4 定了武器之後,對應的 predicate 在這裡登記;不預先窮舉。
    """
    if not predicate or predicate != predicate.strip():
        raise PredicateError(f"predicate {predicate!r} 不合法(不可為空或含前後空白)")
    _PREDICATES.add(predicate)


def registered_predicates() -> tuple[str, ...]:
    """目前已登記的 predicate(排序)。"""
    return tuple(sorted(_PREDICATES))


def validate_predicate(predicate: str) -> None:
    """寫入 hypothesis 前的守門:未登記 → 明確報錯。

    未登記的一律擋 —— 「沒登記」代表沒人定義過這個預測結果要怎麼判定成敗,
    而 Phase 4 的判官需要能用純函數比對 `ActualOutcome == PredictedOutcome`。
    """
    if predicate not in _PREDICATES:
        raise PredicateError(
            f"predicted_outcome {predicate!r} 未登記(已登記:{list(registered_predicates())})"
        )


# --- 第一版的登記(唯一一個,取自設計文件第一節的具體例子)------------------------
PREDICATE_SWAP_APP_INTENT = "SWAP_APP_INTENT"
register_predicate(PREDICATE_SWAP_APP_INTENT)
