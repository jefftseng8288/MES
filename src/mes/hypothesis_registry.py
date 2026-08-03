"""Phase 3 — `predicted_outcome`(predicate)的應用層受控。

**為什麼受控放應用層,不下沉 DB CHECK(定案):**
`predicted_outcome` 的合法值**取決於 Phase 4 實際用哪些武器,而那還沒定** —— Roadmap 的
武器庫優先序是「listing 優化 / Build in Public」第 1、cold email 第 3。現在若定死
`EMAIL_OPEN` / `HIGHER_CLICK_THROUGH_RATE` 這類值,**很可能定出一組根本用不到的**;
而受控清單**加值容易、收回難**(Phase 1-C 踩過:窄化 CHECK 的 downgrade 會被既有資料擋住)。

**判準與 Phase 2.5 的 `value_text` 相同:用「穩不穩定 / 會不會頻繁改」決定受控放 DB 還是應用層。**
對比同一張表的 `status` / `confidence`(已定義完整、穩定)→ 有 DB CHECK。**同表刻意兩種待遇。**

**★ 只登記「已經確定會用」的 predicate,不預先窮舉。**

**2026-08-03 更新(Jeff 裁決,依真實素材):** 第一次真實生成時,LLM 一再想表達
`SWAP_APP_INTENT` 涵蓋不了的意圖 —— 於是補登記成**商家對 app 的行為三態**
(互斥且窮盡):換掉現有的 / 新裝 / 不裝。**這是被真實產出逼出來的,不是憑空窮舉。**

**`LOCALIZATION_APP_INTENT` 刻意不登記(Jeff 裁決):** 「想裝**哪一類** app」是**產品範疇**,
與「**行為意圖**」是兩個正交維度;混進 predicate 會讓它隨 app 類別數量爆炸
(每多一類 app 就多一個 predicate)。**這類資訊第一版記在 `rationale` 裡,不另開欄位** ——
等真實需求證明需要再結構化。
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


# --- 已登記:商家對 app 的行為三態(互斥且窮盡)---------------------------------
PREDICATE_SWAP_APP_INTENT = "SWAP_APP_INTENT"  # 換掉現有的 app
PREDICATE_ADOPT_APP_INTENT = "ADOPT_APP_INTENT"  # 新裝(原本沒有)
PREDICATE_NO_APP_ADOPTION_INTENT = "NO_APP_ADOPTION_INTENT"  # 無意願、不裝
for _p in (
    PREDICATE_SWAP_APP_INTENT,
    PREDICATE_ADOPT_APP_INTENT,
    PREDICATE_NO_APP_ADOPTION_INTENT,
):
    register_predicate(_p)
