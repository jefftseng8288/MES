"""LLMProvider 抽象 —— P4(Model Agnostic)的地基。

**P4 的精神:模型是「受僱解讀中性資料的分析師」,不讓任何模型成為系統的唯一前提。**
所以呼叫端只認 `LLMProvider` 這個介面,不認任何一家廠商的 SDK。

**採 API 直接調用(SDK),不依賴 CLI** —— Claude Code 是終端機的開發 Agent,
MES 是 Python backend,兩者不同。

**第一版只實作 `AnthropicProvider`**(目前只有 Anthropic 的 key)。
`OpenAIProvider` 尚未實作,但**補上它時不需要動核心** —— 只要新增一個子類別 +
在 `_PROVIDERS` 註冊一行。呼叫端(`mes.hypothesis`)完全不必改。

⚠️ **驗收誠實標記:** Phase 3 驗收有一條「換模型(GPT ↔ Claude)讀同一份 Insight 各自產生
假說」。**只有一個 provider 時這條無法實際驗證** —— 目前只能證明「抽象層設計正確」
(換 provider 不需改核心),**不能宣稱該條驗收通過**。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from mes.config import get_settings

logger = logging.getLogger(__name__)

# 暫定值,可調:單次觸發最多打幾次 API(避免一次觸發打出大量請求 / 燒掉預算)。
MAX_LLM_CALLS = 10
DEFAULT_MAX_TOKENS = 4096
# 用當前最強的 Anthropic 模型產生假說(推理品質優先於成本)。
# ★ 模型 id **實測自 `client.models.list()`**,不憑記憶寫 —— 憑印象寫的
# `claude-opus-4-20250514` 實測 404(該 id 不存在於此帳戶)。同「碰外部世界先探再寫」。
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"


class LLMError(RuntimeError):
    """LLM 呼叫失敗 —— 明確報錯並記錄,**不重試到底、不假裝成功**。"""


@dataclass(frozen=True)
class LLMResponse:
    """一次 LLM 呼叫的結果 + 用量(用量要讓 Jeff 知道花了多少)。"""

    text: str
    model: str  # 實際回應的模型名 -> 寫進 hypothesis.model(P5)
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMProvider(ABC):
    """所有 provider 的介面。呼叫端只認這個,不認任何廠商 SDK。"""

    name: str

    @abstractmethod
    def complete(
        self, *, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> LLMResponse:
        """送出一次請求並回傳結果。失敗一律拋 `LLMError`(不吞、不假裝成功)。"""


class AnthropicProvider(LLMProvider):
    """Anthropic 實作。API key 走既有的 Settings(`MES_ANTHROPIC_API_KEY`),不硬編。"""

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL) -> None:
        self.model = model

    def complete(
        self, *, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> LLMResponse:
        api_key = get_settings().anthropic_api_key
        if not api_key:
            raise LLMError(
                "未設定 MES_ANTHROPIC_API_KEY —— 請在 .env 加上(勿硬編、勿進版控)"
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - 相依已在 pyproject
            raise LLMError(f"anthropic SDK 未安裝:{exc}") from exc

        client = anthropic.Anthropic(api_key=api_key)
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001 - 各種 API 錯誤統一成 LLMError
            raise LLMError(f"Anthropic API 呼叫失敗:{type(exc).__name__}: {exc}") from exc

        # content 可能混有 thinking / tool_use 等 block,只取 text(用 getattr 而非
        # isinstance,避免綁死 SDK 的型別名稱)。
        text = "".join(
            str(getattr(block, "text", "")) for block in resp.content
            if getattr(block, "type", "") == "text"
        )
        if not text.strip():
            raise LLMError("Anthropic 回應為空(無 text block)")
        return LLMResponse(
            text=text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )


# 註冊表:**補第二個 provider = 加一個子類別 + 這裡加一行**,核心不必動。
_PROVIDERS: dict[str, type[LLMProvider]] = {
    AnthropicProvider.name: AnthropicProvider,
    # "openai": OpenAIProvider,   # 尚未實作(需另辦 key)
}


def get_provider(name: str | None = None) -> LLMProvider:
    """Factory:依名稱取得 provider。預設 anthropic(目前唯一有 key 的)。"""
    key = (name or AnthropicProvider.name).lower()
    cls = _PROVIDERS.get(key)
    if cls is None:
        raise LLMError(
            f"未知的 LLM provider {key!r}(已註冊:{sorted(_PROVIDERS)})"
        )
    return cls()


def available_providers() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDERS))
