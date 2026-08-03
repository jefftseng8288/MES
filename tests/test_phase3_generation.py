"""Phase 3 第二批-A:LLMProvider 抽象 + Pattern 聚合 + 假說生成。

全程用 **mock provider**,不打真實 API(測試要能離線、免費、可重複跑)。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Entity, Hypothesis, InsightStore
from mes.hypothesis import (
    UNREGISTERED,
    GenerationReport,
    aggregate_patterns,
    anonymised_samples,
    build_user_prompt,
    load_prompt,
    parse_hypotheses,
    run_generation,
)
from mes.hypothesis_registry import PREDICATE_SWAP_APP_INTENT, registered_predicates
from mes.insight_producers import SKURuleProducer
from mes.llm import LLMError, LLMProvider, LLMResponse, available_providers, get_provider

_SKU = SKURuleProducer.insight_type


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


class MockProvider(LLMProvider):
    """測試用 provider —— 證明呼叫端只依賴抽象,不依賴任何廠商 SDK。"""

    name = "mock"

    def __init__(self, payload: str, model: str = "mock-model-v1") -> None:
        self.payload = payload
        self._model = model
        self.calls = 0

    def complete(self, *, system: str, user: str, max_tokens: int = 4096) -> LLMResponse:
        self.calls += 1
        self.last_system, self.last_user = system, user
        return LLMResponse(self.payload, self._model, input_tokens=100, output_tokens=50)


def _payload(*items: dict[str, Any]) -> str:
    return json.dumps({"hypotheses": list(items)})


def _good(**over: Any) -> dict[str, Any]:
    base = {
        "predicted_outcome": PREDICATE_SWAP_APP_INTENT,
        "rationale": "這類店評論資產大,遷移成本高。",
        "confidence": "inferred",
    }
    base.update(over)
    return base


# --- LLMProvider 抽象 ----------------------------------------------------------


def test_factory_returns_anthropic_by_default() -> None:
    p = get_provider()
    assert p.name == "anthropic"


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(LLMError):
        get_provider("gemini")


def test_only_anthropic_registered_openai_not_implemented() -> None:
    """★ 誠實標記:第一版只有一個 provider —— 「換模型」驗收無法實際驗證。"""
    assert available_providers() == ("anthropic",)


def test_anthropic_without_key_raises_clear_error(monkeypatch: Any) -> None:
    """沒 key → 明確報錯,不靜默假裝成功。"""
    from types import SimpleNamespace

    import mes.llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_settings", lambda: SimpleNamespace(anthropic_api_key=""))
    with pytest.raises(LLMError, match="MES_ANTHROPIC_API_KEY"):
        get_provider("anthropic").complete(system="s", user="u")


# --- Pattern 聚合 --------------------------------------------------------------


async def _store(session: AsyncSession, labels: list[tuple[str, str]]) -> uuid.UUID:
    e = Entity(entity_type="store", canonical_key=f"gen-{uuid.uuid4().hex}.com")
    session.add(e)
    await session.flush()
    for itype, value in labels:
        session.add(InsightStore(
            entity_id=e.entity_id, insight_type=itype, value_text=value, producer="rule_v1",
            confidence="certain", generated_at=datetime.now(UTC),
            source_knowledge_refs=[{"entity_id": str(e.entity_id), "feature": "product_count"}]))
    await session.commit()
    return e.entity_id


async def test_aggregate_groups_same_combination(session: AsyncSession) -> None:
    """相同 insight 組合的店歸為同一個 pattern,並算出家數。"""
    from mes.insight_registry import register_insight_type

    dim = f"TEST_AGG_{uuid.uuid4().hex[:6]}"
    register_insight_type(dim, ("A", "B"))
    a1 = await _store(session, [(dim, "A")])
    a2 = await _store(session, [(dim, "A")])
    b1 = await _store(session, [(dim, "B")])

    groups = await aggregate_patterns(session)
    by_desc = {g.describe(): g for g in groups}
    assert set(by_desc[f"{dim}=A"].store_ids) >= {a1, a2}
    assert by_desc[f"{dim}=A"].store_count >= 2
    assert b1 in by_desc[f"{dim}=B"].store_ids
    assert a1 not in by_desc[f"{dim}=B"].store_ids


async def test_aggregate_multi_dimension_is_one_pattern(session: AsyncSession) -> None:
    """兩個維度的店 → 聚成「兩條件的組合」這一個 pattern(未來多維時的行為)。"""
    from mes.insight_registry import register_insight_type

    d1, d2 = f"TA_{uuid.uuid4().hex[:5]}", f"TB_{uuid.uuid4().hex[:5]}"
    register_insight_type(d1, ("X",))
    register_insight_type(d2, ("Y",))
    both = await _store(session, [(d1, "X"), (d2, "Y")])

    groups = await aggregate_patterns(session)
    g = next(g for g in groups if both in g.store_ids)
    assert len(g.conditions) == 2
    assert {c["insight_type"] for c in g.conditions} == {d1, d2}


async def test_samples_are_anonymised(session: AsyncSession) -> None:
    """★ sample 只含中性事實,**不含身分資訊**(canonical_key / entity_id)。"""
    from mes.db.models import KnowledgeState, ObservationLog
    from mes.insight_registry import register_insight_type

    dim = f"TS_{uuid.uuid4().hex[:5]}"
    register_insight_type(dim, ("Z",))
    eid = await _store(session, [(dim, "Z")])
    obs = ObservationLog(
        entity_id=eid, feature="product_count", value_type="number", value_raw="42",
        value_number=42, source="products_json", producer="mes_crawler_v1",
        observed_at=datetime.now(UTC), confidence="certain", status="observed",
        batch_id="2099-01-01-01")
    session.add(obs)
    await session.flush()
    session.add(KnowledgeState(
        entity_id=eid, feature="product_count", value_type="number", value_raw="42",
        value_number=42, producer="mes_crawler_v1", source_observation_id=obs.observation_id,
        observed_at=datetime.now(UTC), confidence="certain",
        selection_rule_version="default_v1", current_status="observed"))
    await session.commit()

    groups = await aggregate_patterns(session)
    g = next(g for g in groups if eid in g.store_ids)
    samples = await anonymised_samples(session, g)
    blob = json.dumps(samples)
    assert "product_count" in blob
    assert str(eid) not in blob and "gen-" not in blob  # 無身分資訊


# --- prompt ---------------------------------------------------------------------


def test_prompt_loads_and_has_both_sections() -> None:
    system, user = load_prompt()
    assert "falsifiable" in system.lower()
    assert "{pattern_description}" in user


def test_user_prompt_fills_placeholders() -> None:
    from mes.hypothesis import PatternGroup

    _, template = load_prompt()
    g = PatternGroup([{"insight_type": _SKU, "value_text": "High SKU"}], [uuid.uuid4()])
    out = build_user_prompt(template, g, [{"product_count": "42"}])
    assert "{pattern_description}" not in out and "{store_count}" not in out
    assert f"{_SKU}=High SKU" in out
    assert PREDICATE_SWAP_APP_INTENT in out  # 有告知 LLM 可用的 predicate


# --- 解析 ----------------------------------------------------------------------


def test_parse_valid_json() -> None:
    assert len(parse_hypotheses(_payload(_good(), _good()))) == 2


def test_parse_tolerates_markdown_fence() -> None:
    assert len(parse_hypotheses(f"```json\n{_payload(_good())}\n```")) == 1


@pytest.mark.parametrize("bad", ["not json at all", "{}", '{"hypotheses": "nope"}'])
def test_parse_failure_raises_clearly(bad: str) -> None:
    """★ 解析失敗明確報錯,不硬塞、不猜。"""
    with pytest.raises(LLMError):
        parse_hypotheses(bad)


# --- 生成 + 驗證擋下 -------------------------------------------------------------


async def _run(session: AsyncSession, payload: str) -> tuple[GenerationReport, MockProvider]:
    maker = async_sessionmaker(bind=session.bind, expire_on_commit=False)
    mock = MockProvider(payload)
    report = await run_generation(session_maker=maker, provider=mock, max_patterns=1)
    return report, mock


async def test_generation_writes_hypothesis(session: AsyncSession) -> None:
    before = len((await session.execute(select(Hypothesis))).scalars().all())
    report, mock = await _run(session, _payload(_good()))
    assert mock.calls == 1 and report.written >= 1
    rows = (await session.execute(select(Hypothesis))).scalars().all()
    assert len(rows) == before + report.written
    h = rows[-1]
    assert h.status == "pending"  # 生成不自動核准
    assert h.model == "mock-model-v1" and h.prompt_version == "hypothesis_v1"
    assert h.hypothesis_version == "h1"
    assert h.source_insight_refs and h.pattern  # Provenance 與 pattern 都有


async def test_token_usage_recorded(session: AsyncSession) -> None:
    report, _ = await _run(session, _payload(_good()))
    assert report.input_tokens == 100 and report.output_tokens == 50
    assert "token" in report.summary()


async def test_unregistered_predicate_is_recorded_not_written(session: AsyncSession) -> None:
    """★ LLM 想用未登記的 predicate → 不寫入,但**結構化記錄**供 Jeff 決定要不要登記。"""
    report, _ = await _run(session, _payload(
        _good(predicted_outcome=UNREGISTERED, wanted_predicate="EMAIL_REPLY")))
    assert report.written == 0
    assert report.wanted_predicates["EMAIL_REPLY"] == 1
    assert "EMAIL_REPLY" in report.summary()
    # 沒有因為 LLM 想用就擅自擴充 registry(擴充是 Jeff 的裁決,不是程式的權限)
    assert "EMAIL_REPLY" not in registered_predicates()


async def test_invented_predicate_also_blocked(session: AsyncSession) -> None:
    """LLM 若無視規則直接編一個值 → 一樣被 registry 擋下並記錄。"""
    report, _ = await _run(session, _payload(_good(predicted_outcome="MADE_UP_THING")))
    assert report.written == 0
    assert report.wanted_predicates["MADE_UP_THING"] == 1


async def test_bad_confidence_blocked(session: AsyncSession) -> None:
    report, _ = await _run(session, _payload(_good(confidence="very sure")))
    assert report.written == 0 and any("confidence" in r["reason"] for r in report.rejected)


async def test_empty_rationale_blocked(session: AsyncSession) -> None:
    """假說必須可審核 —— 沒有推論鏈就不可審核。"""
    report, _ = await _run(session, _payload(_good(rationale="")))
    assert report.written == 0 and any("rationale" in r["reason"] for r in report.rejected)


async def test_mixed_good_and_bad(session: AsyncSession) -> None:
    """一批裡有好有壞 → 好的寫入、壞的擋下並記錄,互不影響。"""
    report, _ = await _run(session, _payload(
        _good(), _good(predicted_outcome="NOPE"), _good()))
    assert report.written == 2 and len(report.rejected) == 1


async def test_parse_failure_surfaces(session: AsyncSession) -> None:
    with pytest.raises(LLMError):
        await _run(session, "the model rambled instead of returning JSON")


async def test_call_limit_respected(session: AsyncSession) -> None:
    """呼叫上限:max_patterns 限制單次觸發的 API 呼叫數。"""
    maker = async_sessionmaker(bind=session.bind, expire_on_commit=False)
    mock = MockProvider(_payload(_good()))
    await run_generation(session_maker=maker, provider=mock, max_patterns=2)
    assert mock.calls <= 2
