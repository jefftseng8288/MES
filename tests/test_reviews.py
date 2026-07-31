"""review_count / avg_rating / rating_distribution 採集測試(純函數,不碰網路)。"""

from __future__ import annotations

import uuid

from mes.harvest import parse_review_features
from mes.reviews import (
    FEATURE_AVG_RATING,
    FEATURE_RATING_DISTRIBUTION,
    FEATURE_REVIEW_COUNT,
    HANDLERS,
    ReviewStats,
    extract_loox_widget_id,
    fetch_review_stats,
    parse_loox_widget,
)

# 真實版面樣本(取自 loox 評論牆頁的實際結構,2026-07-31 實測)。
_WIDGET = (
    '<div aria-label="Average rating: 4.8 / 5 star review"></div>'
    '<div data-testid="rating-summary-count" class="summary-text"><span>402 Reviews</span></div>'
    '<button aria-label="Filter by 5 stars, 365 reviews"></button>'
    '<button aria-label="4 stars, 19 reviews"></button>'
    '<button aria-label="3 stars, 7 reviews"></button>'
    '<button aria-label="2 stars, 2 reviews"></button>'
    '<button aria-label="1 stars, 9 reviews"></button>'
)


def _by_feature(results: list) -> dict:
    return {r.feature: r for r in results}


# --- widget id 兩種 markup ----------------------------------------------------


def test_widget_id_from_script_url() -> None:
    html = '<script src="https://loox.io/widget/7aidAfEWdm/loox.1730769892539.js?shop=x"></script>'
    assert extract_loox_widget_id(html) == "7aidAfEWdm"


def test_widget_id_from_lxc_comment() -> None:
    html = "<!-- __LXV: 1781897427079;__LXR: https://loox.io;__LXC: Xa9tTuyR8_; -->"
    assert extract_loox_widget_id(html) == "Xa9tTuyR8_"


def test_widget_id_absent_returns_none() -> None:
    assert extract_loox_widget_id("<html>no loox here</html>") is None


# --- 評論牆解析 + 自我驗算 ----------------------------------------------------


def test_parse_widget_extracts_count_avg_distribution() -> None:
    s = parse_loox_widget(_WIDGET)
    assert s.status == "observed"
    assert s.review_count == 402
    assert s.avg_rating == 4.8 and s.avg_is_computed is False  # 頁面直讀
    assert s.distribution == {"5": 365, "4": 19, "3": 7, "2": 2, "1": 9}
    assert sum(s.distribution.values()) == s.review_count  # ★ 加總驗算


def test_distribution_rejected_when_sum_mismatches() -> None:
    """★ 驗算不符 → 不採用分佈(不靜默採用壞資料),但總數仍記。"""
    bad = _WIDGET.replace("5 stars, 365 reviews", "5 stars, 999 reviews")
    s = parse_loox_widget(bad)
    assert s.status == "observed" and s.review_count == 402
    assert s.distribution is None


def test_avg_computed_from_distribution_is_marked() -> None:
    """頁面沒給平均 → 由分佈計算,並誠實標記(confidence 會降級)。"""
    no_avg = _WIDGET.replace('aria-label="Average rating: 4.8 / 5 star review"', "")
    s = parse_loox_widget(no_avg)
    assert s.avg_rating is not None and s.avg_is_computed is True
    expected = (5 * 365 + 4 * 19 + 3 * 7 + 2 * 2 + 1 * 9) / 402
    assert abs(s.avg_rating - expected) < 1e-9


def test_parse_widget_unparseable_is_fetch_failed() -> None:
    s = parse_loox_widget("<html>版面改版了</html>")
    assert s.status == "fetch_failed" and s.reason is not None and "解析不到" in s.reason


# --- ★ 三值語義:沒裝(not_found)vs 裝了抓不到(fetch_failed)-----------------


def test_no_review_app_is_not_found() -> None:
    """沒偵測到 app → 確認不具備 → not_found(有效負向觀測,不是失敗)。"""
    s = fetch_review_stats("x.com", "<html></html>", None, client=None)  # type: ignore[arg-type]
    assert s.status == "not_found"


def test_app_without_handler_is_fetch_failed() -> None:
    """★ 裝了 yotpo 但我們沒 handler → 店家有評論、只是我們讀不到 → fetch_failed。

    記 not_found 會謊稱「確認沒有評論」—— 兩者不可混淆。
    """
    s = fetch_review_stats("x.com", "<html></html>", "yotpo", client=None)  # type: ignore[arg-type]
    assert s.status == "fetch_failed"
    assert s.reason is not None and "尚無 yotpo handler" in s.reason


def test_homepage_unreachable_is_fetch_failed() -> None:
    s = fetch_review_stats("x.com", None, "loox", client=None)  # type: ignore[arg-type]
    assert s.status == "fetch_failed"


def test_loox_installed_but_no_widget_id_is_fetch_failed() -> None:
    """裝了 loox 卻取不到 id(可能是第三種 markup)→ 不知道結果 → fetch_failed + 原因。"""
    s = HANDLERS["loox"].fetch("x.com", "<html>loox but no id</html>", client=None)  # type: ignore[arg-type]
    assert s.status == "fetch_failed"
    assert s.reason is not None and "widget id" in s.reason


# --- 轉成 FeatureResult -------------------------------------------------------


def test_observed_produces_three_features() -> None:
    f = _by_feature(parse_review_features(parse_loox_widget(_WIDGET)))
    assert len(f) == 3
    assert f[FEATURE_REVIEW_COUNT].status == "observed"
    assert f[FEATURE_REVIEW_COUNT].value_number == 402.0
    assert f[FEATURE_AVG_RATING].value_number == 4.8
    assert f[FEATURE_AVG_RATING].confidence == "certain"  # 直讀
    assert f[FEATURE_RATING_DISTRIBUTION].value_json == {"5": 365, "4": 19, "3": 7, "2": 2, "1": 9}
    assert all(r.source == "review_widget" for r in f.values())


def test_computed_avg_confidence_downgraded() -> None:
    no_avg = _WIDGET.replace('aria-label="Average rating: 4.8 / 5 star review"', "")
    f = _by_feature(parse_review_features(parse_loox_widget(no_avg)))
    assert f[FEATURE_AVG_RATING].confidence == "estimated"  # 計算而來,誠實降級


def test_not_found_produces_three_absent_features() -> None:
    f = _by_feature(parse_review_features(ReviewStats("not_found", reason="沒裝")))
    assert len(f) == 3
    assert all(r.status == "not_found" for r in f.values())
    assert all(r.value_raw is None and r.value_number is None for r in f.values())


def test_fetch_failed_produces_three_absent_features() -> None:
    f = _by_feature(parse_review_features(ReviewStats("fetch_failed", reason="讀不到")))
    assert all(r.status == "fetch_failed" for r in f.values())
    assert all(r.value_raw is None for r in f.values())


def test_handlers_are_pluggable() -> None:
    """加新 app = 加一個 handler 丟進 HANDLERS,核心不動。"""
    assert set(HANDLERS) == {"loox"}  # 目前只有 loox 有實測樣本
    assert all(hasattr(h, "fetch") and h.app_key for h in HANDLERS.values())


def test_distribution_absent_records_not_found_not_silent() -> None:
    """驗算不符時,分佈記 not_found(可見),而不是靜默省略該 feature。"""
    bad = _WIDGET.replace("5 stars, 365 reviews", "5 stars, 999 reviews")
    f = _by_feature(parse_review_features(parse_loox_widget(bad)))
    assert f[FEATURE_RATING_DISTRIBUTION].status == "not_found"
    assert f[FEATURE_REVIEW_COUNT].status == "observed"  # 總數仍可信


def test_review_features_are_uuid_free() -> None:
    """評論 feature 不掛 entity_ref(與 uses_review_app 不同)。"""
    for r in parse_review_features(parse_loox_widget(_WIDGET)):
        assert r.value_entity_id is None
        assert isinstance(uuid.uuid4(), uuid.UUID)  # sanity


# --- 實測發現的三個真實變體(都由真實店觸發,非預防性猜測)---------------------


def test_widget_id_from_json_escaped_url() -> None:
    """★ 第三種 markup:JSON 字串內的跳脫斜線(sciencefactory.es 實測)。"""
    html = r'"https:\/\/loox.io\/widget\/EkZ2BqM2Qj\/loox.174003.js"'
    assert extract_loox_widget_id(html) == "EkZ2BqM2Qj"


def test_count_is_language_agnostic() -> None:
    """★ widget 頁可見文字會在地化(西班牙文店顯示 Reseñas)—— 錨在結構才抓得到。"""
    es = ('<div data-testid="rating-summary-count" class="summary-text">'
          '<span>1,019 Rese\u00f1as</span></div>')
    s = parse_loox_widget(es)
    assert s.status == "observed" and s.review_count == 1019


def test_distribution_is_structural_not_positional() -> None:
    """★ 小店的數字序列會誤導位置式解析;結構式(星等自我標示)才正確。"""
    small = (
        '<div aria-label="Average rating: 5.0 / 5 star review"></div>'
        '<div data-testid="rating-summary-count"><span>7 Reviews</span></div>'
        '<button aria-label="Filter by 5 stars, 7 reviews"></button>'
        '<button aria-label="4 stars, 0 reviews"></button>'
        '<button aria-label="3 stars, 0 reviews"></button>'
        '<button aria-label="2 stars, 0 reviews"></button>'
        '<button aria-label="1 stars, 0 reviews"></button>'
    )
    s = parse_loox_widget(small)
    assert s.review_count == 7
    assert s.distribution == {"5": 7, "4": 0, "3": 0, "2": 0, "1": 0}
    assert sum(s.distribution.values()) == 7


def test_no_distribution_markup_still_records_count() -> None:
    """分佈區塊不存在(某些版面)→ 總數仍記,分佈記 not_found,不整批失敗。"""
    only_count = (
        '<div aria-label="Average rating: 4.9 / 5 star review"></div>'
        '<div data-testid="rating-summary-count"><span>1,019 Rese\u00f1as</span></div>'
    )
    s = parse_loox_widget(only_count)
    assert s.status == "observed" and s.review_count == 1019 and s.avg_rating == 4.9
    assert s.distribution is None
