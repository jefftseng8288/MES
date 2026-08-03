"""Phase 3 第二批-B — 假說審核網頁(本機)。

**做完這段,Phase 3 的循環就完整:生成 → 審核 → decision 進 Decision Graph。**

**服務形式(Jeff 定案):**
- `python -m mes.review` 起本機服務,**只綁 127.0.0.1**(不對外網開放)。
- **不做登入認證** —— 只綁 localhost、只有 Jeff 在自己機器上用,**沒有對外暴露就沒有
  未授權存取問題**。為此加一套帳密是過度設計。
- 用 **Python 標準庫**(`http.server`)+ 伺服器端渲染的 HTML,**零新增相依**、
  無前端框架、無建置流程。單一使用者的本機工具,這樣最相稱。

**★ 頁面的重點是「能讀懂那條假說」。** Phase 3 驗收條件是「可審核」,而可審核的實質是
**人真的讀得懂這條假說在說什麼** —— 所以 pattern 要翻成人話、rationale 要好排版、
還要顯示**這條假說會打到幾家店**(樣本夠不夠是審核時的關鍵判斷依據)。

**三個動作:**
| 動作 | hypothesis.status | decision |
|---|---|---|
| Approve | → `approved` | `approve` |
| Reject | → `rejected` + 寫 `rejection_reason` | `reject`(**理由必填**) |
| **Comment** | **不變(維持 pending)** | `comment` |

**★ Comment 是純註記(Jeff 定案):** 寫進 decision 表但**不改 status**,讓 Jeff 先留想法、
之後再決定。(「要求 AI 依 comment 修改」屬 Phase 5 演化循環,現在沒有 Outcome 當燃料,**不做**。)

**這批不做:** 重新生成、AI 依 reject 產生進化版、排程、對外部署。
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mes.config import get_settings
from mes.db.models import Decision, Hypothesis
from mes.patterns import stores_matching_pattern

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"  # ★ 只綁 localhost —— 不對外暴露
PORT = 8765
ACTOR = "jeff"
TARGET_TYPE = "hypothesis"
ACTION_APPROVE, ACTION_REJECT, ACTION_COMMENT = "approve", "reject", "comment"
# 動作 -> 對 status 的影響。**comment 刻意是 None = 不改 status。**
_STATUS_AFTER: dict[str, str | None] = {
    ACTION_APPROVE: "approved",
    ACTION_REJECT: "rejected",
    ACTION_COMMENT: None,
}


class ReviewError(ValueError):
    """審核動作不合法 —— 明確報錯,不靜默通過。"""


@dataclass
class HypothesisView:
    """一條假說在頁面上要呈現的一切(含決策史與符合的店家數)。"""

    hypothesis: Hypothesis
    store_count: int
    decisions: list[Decision] = field(default_factory=list)

    @property
    def pattern_human(self) -> str:
        """★ 把 pattern 翻成人看得懂的形式,不要直接倒 JSON。"""
        return describe_pattern(self.hypothesis.pattern)


def describe_pattern(pattern: list[dict[str, Any]] | None) -> str:
    """`[{"insight_type": "SKU_SCALE", "value_text": "Low SKU"}]` → `SKU_SCALE = Low SKU`。"""
    if not pattern:
        return "(無條件)"
    return "  且  ".join(
        f"{c.get('insight_type', '?')} = {c.get('value_text', '?')}" for c in pattern
    )


# --- 核心邏輯(與 HTTP 無關,可獨立測試)----------------------------------------


async def latest_decision(session: AsyncSession, hypothesis_id: uuid.UUID) -> Decision | None:
    """該假說最近一次的 decision —— 新 decision 的 parent(串起 Decision Graph)。"""
    return (
        await session.execute(
            select(Decision)
            .where(Decision.target_type == TARGET_TYPE, Decision.target_id == hypothesis_id)
            .order_by(Decision.created_at.desc(), Decision.decision_id.desc())
            .limit(1)
        )
    ).scalars().first()


async def decision_history(session: AsyncSession, hypothesis_id: uuid.UUID) -> list[Decision]:
    """該假說的完整決策史(舊→新)。讓 Jeff 看得到「這條之前被我 comment 過什麼」。"""
    return list(
        (
            await session.execute(
                select(Decision)
                .where(Decision.target_type == TARGET_TYPE, Decision.target_id == hypothesis_id)
                .order_by(Decision.created_at, Decision.decision_id)
            )
        ).scalars().all()
    )


async def apply_decision(
    session: AsyncSession,
    hypothesis_id: uuid.UUID,
    action: str,
    reason: str | None = None,
    actor: str = ACTOR,
) -> Decision:
    """套用一個審核動作:寫 decision(串 parent)+ 必要時改 status。

    **★ comment 不改 status** —— 它是純註記,讓人先留想法、之後再決定。
    **★ reject 必須填理由** —— 理由是未來演化的素材,空的等於沒記。
    """
    if action not in _STATUS_AFTER:
        raise ReviewError(f"未知的動作 {action!r}(可用:{sorted(_STATUS_AFTER)})")
    reason = (reason or "").strip()
    if action == ACTION_REJECT and not reason:
        raise ReviewError("Reject 必須填理由(理由是未來演化的素材)")

    h = await session.get(Hypothesis, hypothesis_id)
    if h is None:
        raise ReviewError(f"找不到假說 {hypothesis_id}")

    parent = await latest_decision(session, hypothesis_id)
    decision = Decision(
        parent_decision_id=parent.decision_id if parent else None,
        target_type=TARGET_TYPE,
        target_id=hypothesis_id,
        actor=actor,
        action=action,
        reason=reason or None,
    )
    session.add(decision)

    new_status = _STATUS_AFTER[action]
    if new_status is not None:  # comment -> None -> 維持原狀態
        h.status = new_status
        if action == ACTION_REJECT:
            h.rejection_reason = reason
    await session.commit()
    return decision


async def load_views(
    session: AsyncSession, status: str | None = "pending"
) -> list[HypothesisView]:
    """讀假說 + 算出每條符合的店家數 + 決策史。status=None 表示全部。"""
    stmt = select(Hypothesis).order_by(Hypothesis.created_at.desc())
    if status:
        stmt = stmt.where(Hypothesis.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    views = []
    for h in rows:
        try:
            matched = await stores_matching_pattern(session, h.pattern)
            count = len(matched)
        except Exception:  # noqa: BLE001 - pattern 壞掉不該讓整頁掛掉
            count = -1  # -1 = 算不出來(頁面會顯示為「無法計算」)
        views.append(HypothesisView(h, count, await decision_history(session, h.hypothesis_id)))
    return views


# --- HTML(伺服器端渲染,零前端相依)--------------------------------------------

_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.65 -apple-system, "Helvetica Neue", sans-serif; margin: 0 auto;
       max-width: 900px; padding: 24px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: #888; font-size: 13px; margin-bottom: 20px; }
.tabs a { margin-right: 12px; text-decoration: none; }
.tabs a.on { font-weight: 700; text-decoration: underline; }
.card { border: 1px solid #8883; border-radius: 10px; padding: 16px 18px; margin: 16px 0; }
.pattern { font-size: 17px; font-weight: 700; }
.meta { color: #888; font-size: 12.5px; margin: 6px 0 12px; }
.chip { display: inline-block; border: 1px solid #8885; border-radius: 999px;
        padding: 1px 9px; font-size: 12px; margin-right: 6px; }
.rationale { background: #8881; border-radius: 8px; padding: 12px 14px; margin: 10px 0;
             white-space: pre-wrap; }
.hist { font-size: 13px; color: #777; border-left: 3px solid #8884; padding-left: 10px;
        margin: 10px 0; }
form { display: flex; gap: 8px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
input[type=text] { flex: 1; min-width: 260px; padding: 7px 10px; border-radius: 7px;
                   border: 1px solid #8886; background: transparent; color: inherit; }
button { padding: 7px 14px; border-radius: 7px; border: 1px solid #8886; cursor: pointer;
         background: transparent; color: inherit; font-size: 14px; }
button.approve { border-color: #2a7; color: #2a7; }
button.reject  { border-color: #d55; color: #d55; }
.empty { color: #888; padding: 40px 0; text-align: center; }
.err { background: #d552; border: 1px solid #d55; padding: 10px 14px; border-radius: 8px; }
"""


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def render_card(v: HypothesisView) -> str:
    h = v.hypothesis
    refs = describe_pattern(h.source_insight_refs)
    count = "無法計算" if v.store_count < 0 else f"{v.store_count} 家店符合"
    history = ""
    if v.decisions:
        items = "".join(
            f"<div>· <b>{_esc(d.action)}</b> — {_esc(d.reason or '(無理由)')} "
            f"<span style='color:#999'>{_fmt(d.created_at)}</span></div>"
            for d in v.decisions
        )
        history = f"<div class='hist'><b>決策史</b>{items}</div>"

    actions = ""
    if h.status == "pending":
        actions = f"""
        <form method="post" action="/decide">
          <input type="hidden" name="id" value="{h.hypothesis_id}">
          <input type="text" name="reason" placeholder="理由 / 註記(Reject 必填)">
          <button class="approve" name="action" value="approve">Approve</button>
          <button class="reject" name="action" value="reject">Reject</button>
          <button name="action" value="comment">Comment</button>
        </form>"""

    return f"""<div class="card">
  <div class="pattern">{_esc(v.pattern_human)}</div>
  <div class="meta">{count} · 建立於 {_fmt(h.created_at)}</div>
  <div>
    <span class="chip">預測 {_esc(h.predicted_outcome)}</span>
    <span class="chip">信心 {_esc(h.confidence)}</span>
    <span class="chip">狀態 {_esc(h.status)}</span>
  </div>
  <div class="rationale">{_esc(h.rationale)}</div>
  <div class="meta">
    <b>Evidence(基於哪些 insight)</b>:{_esc(refs)}<br>
    <b>版本</b>:model {_esc(h.model)} · prompt {_esc(h.prompt_version)}
    · hypothesis {_esc(h.hypothesis_version)}
  </div>
  {f'<div class="meta"><b>Reject 理由</b>:{_esc(h.rejection_reason)}</div>'
    if h.rejection_reason else ''}
  {history}
  {actions}
</div>"""


def render_page(views: list[HypothesisView], status: str | None, error: str = "") -> str:
    tabs = "".join(
        f'<a class="{"on" if status == s else ""}" href="/?status={s or "all"}">{label}</a>'
        for s, label in [
            ("pending", "待審"), ("approved", "已核准"), ("rejected", "已否決"), (None, "全部"),
        ]
    )
    body = "".join(render_card(v) for v in views) or (
        "<div class='empty'>這個檢視沒有假說。</div>"
    )
    err = f"<div class='err'>{_esc(error)}</div>" if error else ""
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MES 假說審核</title><style>{_CSS}</style></head><body>
<h1>MES 假說審核</h1>
<div class="sub">Phase 3 · 本機服務(僅 127.0.0.1)· 共 {len(views)} 條</div>
<div class="tabs">{tabs}</div>{err}{body}</body></html>"""


# --- HTTP ----------------------------------------------------------------------


def _run(coro: Any) -> Any:
    """單一使用者的本機工具 —— 每個請求開一次 event loop 即可,不必常駐。"""
    return asyncio.run(coro)


async def _with_session(fn: Any) -> Any:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
            return await fn(session)
    finally:
        await engine.dispose()


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "MESReview/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # 靜音預設的逐請求 log
        logger.debug("[review] " + fmt, *args)

    def _send(self, body: str, code: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的介面
        q = parse_qs(urlparse(self.path).query)
        raw = (q.get("status") or ["pending"])[0]
        status = None if raw == "all" else raw
        error = (q.get("error") or [""])[0]
        views = _run(_with_session(lambda s: load_views(s, status)))
        self._send(render_page(views, status, error))

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        hid = (form.get("id") or [""])[0]
        action = (form.get("action") or [""])[0]
        reason = (form.get("reason") or [""])[0]
        error = ""
        try:
            _run(_with_session(
                lambda s: apply_decision(s, uuid.UUID(hid), action, reason)
            ))
        except (ReviewError, ValueError) as exc:
            error = str(exc)
        # PRG:重新導向回列表,避免重新整理時重送表單。
        self.send_response(303)
        self.send_header("Location", f"/?status=pending&error={_esc(error)}" if error else "/")
        self.end_headers()


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    server = ThreadingHTTPServer((HOST, PORT), ReviewHandler)
    print(f"[mes.review] 假說審核頁:http://{HOST}:{PORT}/  (Ctrl-C 結束)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mes.review] 已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
