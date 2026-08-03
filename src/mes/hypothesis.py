"""Phase 3 第二批-A — Pattern 聚合 + 假說生成(AI 第一次進場)。

**流程(依設計文件第六節):**
  1. **先在 DB 聚合**出商家模式分佈(哪些 insight 組合各有幾家店)。
  2. 把**聚合後的 Pattern Summary + 每個 pattern 2–3 家匿名 sample** 丟給 LLM。
  3. LLM 針對該 pattern 產出假說 → 驗證 → 寫入 `hypothesis`(`status='pending'`)。

**★ 為什麼不把所有店的 raw insight 全塞給 LLM:** 浪費 token,且會陷入
「Lost in the Middle」—— 長 context 中段的資訊會被模型忽略。聚合後送,LLM 才能專心。

**★ 手動觸發,不排程(Jeff 定案):** 每次生成要花 API 費用,而且會**產出待審佇列** ——
排程會讓待審自己累積,但**審核是 Jeff 的人工時間**。手動觸發讓 Jeff 決定何時要一批新假說。

**這批不含審核介面**(網頁是 B 段);生成的假說一律 `status='pending'`。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import CONFIDENCE_LEVELS, Hypothesis, InsightStore, KnowledgeState
from mes.hypothesis_registry import PredicateError, registered_predicates, validate_predicate
from mes.jobs import heartbeat
from mes.llm import MAX_LLM_CALLS, LLMError, LLMProvider, LLMResponse, get_provider
from mes.patterns import PatternError, validate_pattern

logger = logging.getLogger(__name__)

JOB_HYPOTHESIS = "hypothesis_generation"
HYPOTHESIS_VERSION = "h1"  # 假說「結構」的版本(欄位形狀改了才升)
PROMPTS_DIR = Path("prompts")
DEFAULT_PROMPT = "hypothesis_v1"

SAMPLES_PER_PATTERN = 3  # 每個 pattern 送幾家匿名 sample(暫定,可調)
MAX_HYPOTHESES_PER_PATTERN = 3  # 每個 pattern 最多要幾條假說(暫定,可調)
# LLM 想用但未登記的 predicate,一律用這個佔位值回報(見 prompt 的 Hard rule 2)。
UNREGISTERED = "UNREGISTERED"
# sample 只送這些中性事實 —— **不送 canonical_key / entity_id 等身分資訊**(匿名)。
SAMPLE_FEATURES = (
    "product_count", "avg_price", "country", "currency", "language",
    "review_count", "avg_rating",
)


@dataclass(frozen=True)
class PatternGroup:
    """一種商家模式 + 有多少家店符合。"""

    conditions: list[dict[str, str]]  # [{"insight_type":..., "value_text":...}, ...]
    store_ids: list[uuid.UUID]

    @property
    def store_count(self) -> int:
        return len(self.store_ids)

    def describe(self) -> str:
        return " + ".join(f"{c['insight_type']}={c['value_text']}" for c in self.conditions)


@dataclass
class GenerationReport:
    """一次生成的結果 —— 產出多少、擋下多少、LLM 想用哪些未登記的 predicate。"""

    patterns: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    written: int = 0
    rejected: list[dict[str, str]] = field(default_factory=list)
    # ★ 這是 Jeff 決定「該登記哪些 predicate」的**真實素材**(比憑空窮舉好)。
    wanted_predicates: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def summary(self) -> str:
        lines = [
            f"[hypothesis] 聚合出 {self.patterns} 個 pattern · LLM 呼叫 {self.llm_calls} 次 "
            f"· token {self.input_tokens}+{self.output_tokens}"
            f"={self.input_tokens + self.output_tokens}",
            f"  寫入 {self.written} 條假說(status=pending) · 被驗證擋下 {len(self.rejected)} 條",
        ]
        for r in self.rejected:
            lines.append(f"    ✗ {r['reason']}")
        if self.wanted_predicates:
            lines.append("  ★ LLM 想用但未登記的 predicate(供 Jeff 決定要不要登記):")
            for p, n in sorted(self.wanted_predicates.items(), key=lambda kv: -kv[1]):
                lines.append(f"    - {p} ×{n}")
        return "\n".join(lines)


# --- 1. 聚合 -------------------------------------------------------------------


async def aggregate_patterns(
    session: AsyncSession, *, min_stores: int = 1
) -> list[PatternGroup]:
    """把 insight_store 聚合成「商家模式分佈」。

    一家店的模式 = 它所有 (insight_type, value_text) 的組合;相同組合的店歸為同一個 pattern。
    這是**誠實反映現況**的聚合:目前每家店只有 1 個維度(SKU_SCALE),
    所以只會聚出 3 種 pattern —— **這不是缺陷,是資料現況**,不為了好看而放寬或補造。
    """
    rows = (await session.execute(
        select(InsightStore.entity_id, InsightStore.insight_type, InsightStore.value_text)
    )).all()
    by_entity: dict[uuid.UUID, list[tuple[str, str]]] = defaultdict(list)
    for entity_id, itype, value in rows:
        by_entity[entity_id].append((itype, value))

    grouped: dict[tuple[tuple[str, str], ...], list[uuid.UUID]] = defaultdict(list)
    for entity_id, labels in by_entity.items():
        grouped[tuple(sorted(labels))].append(entity_id)

    patterns = [
        PatternGroup(
            conditions=[{"insight_type": t, "value_text": v} for t, v in key],
            store_ids=sorted(ids, key=str),
        )
        for key, ids in grouped.items()
        if len(ids) >= min_stores
    ]
    patterns.sort(key=lambda p: -p.store_count)
    return patterns


async def anonymised_samples(
    session: AsyncSession, group: PatternGroup, limit: int = SAMPLES_PER_PATTERN
) -> list[dict[str, Any]]:
    """取幾家代表性店的**中性事實**當 sample —— 不含任何身分資訊(匿名)。"""
    picked = group.store_ids[:limit]
    if not picked:
        return []
    rows = (await session.execute(
        select(KnowledgeState.entity_id, KnowledgeState.feature, KnowledgeState.value_raw)
        .where(
            KnowledgeState.entity_id.in_(picked),
            KnowledgeState.feature.in_(SAMPLE_FEATURES),
        )
    )).all()
    facts: dict[uuid.UUID, dict[str, Any]] = defaultdict(dict)
    for entity_id, feature, raw in rows:
        facts[entity_id][feature] = raw
    return [facts.get(eid, {}) for eid in picked]


# --- 2. Prompt ----------------------------------------------------------------


def load_prompt(version: str = DEFAULT_PROMPT) -> tuple[str, str]:
    """讀 prompt 檔,回傳 (system, user_template)。**版本號 = 檔名**(P5)。"""
    path = PROMPTS_DIR / f"{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"找不到 prompt 檔:{path}")
    text = path.read_text(encoding="utf-8")
    system = _section(text, "## SYSTEM")
    user = _section(text, "## USER TEMPLATE")
    if not system or not user:
        raise ValueError(f"{path} 缺少 '## SYSTEM' 或 '## USER TEMPLATE' 區塊")
    return system, user


def _section(text: str, header: str) -> str:
    m = re.search(rf"^{re.escape(header)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def build_user_prompt(
    template: str, group: PatternGroup, samples: list[dict[str, Any]]
) -> str:
    body = re.search(r"```(.*?)```", template, re.S)
    shape = body.group(1).strip() if body else template
    return (
        shape.replace("{allowed_predicates}", ", ".join(registered_predicates()) or "(none)")
        .replace("{pattern_description}", group.describe())
        .replace("{store_count}", str(group.store_count))
        .replace("{samples}", json.dumps(samples, ensure_ascii=False, indent=2))
        .replace("{max_hypotheses}", str(MAX_HYPOTHESES_PER_PATTERN))
    )


# --- 3. 解析 + 驗證 -------------------------------------------------------------


def parse_hypotheses(raw: str) -> list[dict[str, Any]]:
    """解析 LLM 回傳的 JSON。**解析失敗明確報錯,不硬塞、不猜。**"""
    text = raw.strip()
    # 容忍模型加了 markdown fence(常見),但不容忍其他散文。
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM 回傳不是合法 JSON(前 200 字:{raw[:200]!r}):{exc}") from exc
    if not isinstance(data, dict) or "hypotheses" not in data:
        raise LLMError(f"LLM 回傳缺少 'hypotheses' 欄位:{str(data)[:200]!r}")
    items = data["hypotheses"]
    if not isinstance(items, list):
        raise LLMError("'hypotheses' 必須是 list")
    return [i for i in items if isinstance(i, dict)]


# --- 4. 生成 -------------------------------------------------------------------


async def generate_for_pattern(
    session: AsyncSession,
    provider: LLMProvider,
    group: PatternGroup,
    system: str,
    user_template: str,
    prompt_version: str,
    report: GenerationReport,
) -> None:
    """對一個 pattern 產生假說並寫入(驗證不過的不寫,記錄原因)。"""
    samples = await anonymised_samples(session, group)
    resp: LLMResponse = provider.complete(
        system=system, user=build_user_prompt(user_template, group, samples)
    )
    report.llm_calls += 1
    report.input_tokens += resp.input_tokens
    report.output_tokens += resp.output_tokens

    for item in parse_hypotheses(resp.text):
        predicate = str(item.get("predicted_outcome", "")).strip()
        # ★ LLM 想用但未登記的 predicate —— 記下來當 Jeff 定清單的素材,不擅自擴充 registry。
        if predicate == UNREGISTERED or not predicate:
            wanted = str(item.get("wanted_predicate", "") or "(未指明)").strip()
            report.wanted_predicates[wanted] += 1
            report.rejected.append({
                "reason": f"predicate 未登記(LLM 想用 {wanted!r});pattern={group.describe()}"
            })
            continue

        draft = Hypothesis(
            pattern=list(group.conditions),
            predicted_outcome=predicate,
            rationale=str(item.get("rationale", "")).strip(),
            confidence=str(item.get("confidence", "")).strip(),
            # Provenance:這條假說基於哪些 insight —— 就是這個 pattern 的條件。
            source_insight_refs=list(group.conditions),
            model=resp.model,
            prompt_version=prompt_version,
            hypothesis_version=HYPOTHESIS_VERSION,
            status="pending",
        )
        try:
            _validate_draft(draft)
        except (PredicateError, PatternError, ValueError) as exc:
            # 驗證不過 -> **不寫入、記錄原因**(不靜默丟棄、也不放寬驗證讓它過)。
            if isinstance(exc, PredicateError):
                report.wanted_predicates[predicate] += 1
            report.rejected.append({"reason": f"{type(exc).__name__}: {exc}"})
            continue
        session.add(draft)
        report.written += 1
    await session.commit()


def _validate_draft(h: Hypothesis) -> None:
    """寫入前跑既有的驗證 —— 不為了讓 LLM 產出通過而放寬任何一條。"""
    validate_pattern(h.pattern)
    validate_predicate(h.predicted_outcome)
    if not h.source_insight_refs:
        raise ValueError("source_insight_refs 不可為空(Provenance 鐵律)")
    if not h.rationale:
        raise ValueError("rationale 不可為空(假說必須可審核)")
    if h.confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence {h.confidence!r} 不是合法的三級之一")


async def run_generation(
    *,
    provider_name: str | None = None,
    prompt_version: str = DEFAULT_PROMPT,
    max_patterns: int = MAX_LLM_CALLS,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    provider: LLMProvider | None = None,
) -> GenerationReport:
    """手動觸發一次假說生成。回傳結果摘要。"""
    engine = None
    if session_maker is None:
        engine = create_async_engine(get_settings().database_url)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    llm = provider or get_provider(provider_name)
    system, user_template = load_prompt(prompt_version)
    report = GenerationReport()
    try:
        async with heartbeat(JOB_HYPOTHESIS) as beat, session_maker() as session:
            groups = await aggregate_patterns(session)
            report.patterns = len(groups)
            # 呼叫上限:避免一次觸發打出大量請求(暫定值,可調)。
            for group in groups[:max_patterns]:
                await generate_for_pattern(
                    session, llm, group, system, user_template, prompt_version, report
                )
            beat.summary = {
                "patterns": report.patterns, "llm_calls": report.llm_calls,
                "written": report.written, "rejected": len(report.rejected),
                "input_tokens": report.input_tokens, "output_tokens": report.output_tokens,
                "wanted_predicates": dict(report.wanted_predicates),
                "prompt_version": prompt_version,
            }
    finally:
        if engine is not None:
            await engine.dispose()
    logger.info("%s", report.summary())
    return report


def main() -> None:
    """手動入口:`uv run python -m mes.hypothesis`(刻意不排程,見模組 docstring)。"""
    logging.basicConfig(level=get_settings().log_level)
    report = asyncio.run(run_generation())
    print(report.summary())


if __name__ == "__main__":
    main()
