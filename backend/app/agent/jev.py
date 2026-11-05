"""Jev 结构化决策（AGTCloud TypeSafe Decisions，POST /v1/decisions）。

Jev 只回答「是否 / 选哪个 / 打几分」，返回概率而不是文字：亚秒级、只按输入计费、
输出天然类型安全。适合放在每条消息都要过的轻量决策上；写回复、排查仍交给 Claude。
任何失败都返回 None，调用方回退到 Claude。
"""

import logging
import time
from typing import Any

import httpx

from app.config import settings
from app.services import trace

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def enabled() -> bool:
    return bool(settings.jev_model and (settings.jev_api_key or settings.anthropic_api_key))


def noul(instructions: str, true: str, false: str) -> dict:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": true, "false": false}}


def choice(instructions: str, options: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}


async def decide(state: Any, questions: dict[str, dict], timeout: float = 15) -> dict | None:
    """发起一次决策，返回 answers（键与 questions 相同）；失败返回 None。"""
    global _client
    if not enabled():
        return None
    base = (settings.jev_base_url or settings.anthropic_base_url or "https://api.agtcloud.ai").rstrip("/")
    key = settings.jev_api_key or settings.anthropic_api_key
    if _client is None:
        _client = httpx.AsyncClient(timeout=timeout)
    t0 = time.monotonic()
    body = {"model": settings.jev_model, "state": state, "questions": questions}
    call = trace.Call("jev", settings.jev_model, body)
    try:
        r = await _client.post(f"{base}/v1/decisions", headers={"Authorization": f"Bearer {key}"},
                               json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        answers = data["answers"]
    except Exception as e:  # noqa: BLE001
        call.done(error=f"{type(e).__name__}: {e}")
        log.warning("jev 决策失败，回退 Claude：%s", e)
        return None
    usage = data.get("usage") or {}
    if set(answers) != set(questions):
        call.done(answers, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                  error="返回的问题不完整")
        log.warning("jev 返回的问题不完整：%s", list(answers))
        return None
    call.done(answers, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    log.debug("jev %.0fms %s", (time.monotonic() - t0) * 1000, answers)
    return answers


# ---------- 业务问题集：与 llm.classify / llm.followup 的决策字段一一对应 ----------

TRIAGE = {
    "is_issue": noul(
        "这条客户群消息是否需要我方（模型 API 聚合平台技术支持）去处理？",
        "故障/报错/变慢等问题反馈，或用量、扩容、开通、额度等需求",
        "闲聊、玩笑、致谢、表情、打招呼、一般咨询、群里其他人之间的对话"),
    "category": choice("如果需要处理，属于哪一类？", {
        "error": "调用报错、失败、超时、变慢等故障",
        "rate_limit": "429 或限流",
        "usage_request": "用量、扩容、开通、额度、权限需求",
        "other": "其他",
    }),
    "reply_kind": choice("如果不需要处理，我们（技术支持）是否需要开口回应？", {
        "greeting": "客户在招呼或呼叫我们技术支持，如「在吗」「有人吗」「@技术支持」",
        "question": "客户向我们提一般性问题，如支持哪些模型、怎么开票、价格、怎么接入",
        "none": "闲聊、玩笑、表情、致谢，或者在叫群里某个具体同事（如「张三你…」）、安排他们自己的事，不需要我们开口",
    }),
}


def triage_state(text: str) -> str:
    return f"客户在群里发了这条消息：{text}"


FOLLOWUP = {
    "should_reply": noul(
        "客户在已有问题的对话里又发了一条消息，我们是否需要回复这条？",
        "继续追问、反馈问题还在或又出现、补充信息、询问进度或时间、表达不满、问题解决后致谢（简短收尾）",
        "对我们进展通报的「好的」「收到」「👍」「ok 辛苦了」之类确认，或与问题无关的闲聊玩笑"),
    "problem_persists": noul(
        "客户这条消息本身是否明确表示问题还在、又出现或没解决？",
        "明确说还是不行、又报错、没好、还在报",
        "确认、致谢、表情、询问进度或预计时间、抱怨频率但没说当前还坏着、说已经好了"),
}


def followup_state(conversation: str, new_text: str, context: str, quoted: str = "") -> dict:
    s = {"之前的客户消息": conversation[-3000:], "处理进展": context, "客户最新这条": new_text}
    if quoted:
        s["客户引用的消息"] = quoted
    return s
