import logging
import re

import anthropic
from pydantic import BaseModel, Field

from app.agent.logs import LogRecord
from app.config import settings

log = logging.getLogger(__name__)


class Classification(BaseModel):
    is_issue: bool = Field(description="是否是需要处理的问题/故障反馈")
    category: str = Field(description="error | rate_limit | other")
    model: str = Field(description="涉及的模型名，未知为空")
    request_id: str = Field(description="消息中的 request_id，没有为空")
    summary: str = Field(description="一句话概括问题")
    confidence: int = Field(description="0-100")


class Finding(BaseModel):
    responsible: str = Field(description="customer | us | supplier | unknown")
    evidence: str = Field(description="判断依据")
    confidence: int = Field(description="0-100，证据越充分越高；没有日志证据时不应超过 60")
    customer_reply: str = Field(description="给客户的回复草稿")
    supplier_note: str = Field(description="若是供应商问题，发给供应商的描述，否则为空")


class Rewrite(BaseModel):
    text: str


_clients: dict[str, anthropic.AsyncAnthropic] = {}


def _client() -> anthropic.AsyncAnthropic | None:
    key = settings.anthropic_api_key
    if not key:
        return None
    if key not in _clients:
        _clients[key] = anthropic.AsyncAnthropic(
            api_key=key, base_url=settings.anthropic_base_url or None)
    return _clients[key]


async def _parse[T: BaseModel](system: str, user: str, schema: type[T], effort: str) -> T | None:
    client = _client()
    if client is None:
        return None
    try:
        resp = await client.messages.parse(
            model=settings.anthropic_model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": effort},
        )
    except (anthropic.APIError, ValueError):
        log.exception("Claude 调用失败，使用规则兜底")
        return None
    if resp.stop_reason == "refusal":
        return None
    return resp.parsed_output


REQ_ID = re.compile(r"(?:request[_ ]?id|req[_-]?id)\s*[:：=]?\s*([\w-]{6,})", re.IGNORECASE)


def _rule_classify(text: str) -> Classification:
    low = text.lower()
    rid = (m.group(1) if (m := REQ_ID.search(text)) else "")
    if "429" in low or "限流" in text or "rate limit" in low:
        cat = "rate_limit"
    elif any(k in low for k in ("报错", "error", "失败", "500", "502", "503", "超时", "timeout")):
        cat = "error"
    else:
        return Classification(is_issue=False, category="other", model="", request_id=rid,
                              summary="", confidence=50)
    model = (m.group(0) if (m := re.search(r"seedance[\w.-]*", low)) else "")
    return Classification(is_issue=True, category=cat, model=model, request_id=rid,
                          summary=text[:80], confidence=60)


async def classify(text: str) -> Classification:
    result = await _parse(
        "你是模型 API 聚合平台的技术支持分诊员。判断客户群里这条消息是否是问题反馈并抽取字段。",
        text,
        Classification,
        effort="low",
    )
    return result or _rule_classify(text)


async def investigate(c: Classification, log: LogRecord | None) -> Finding:
    log_text = "无 request_id，未查日志" if log is None else (
        f"日志: {log}" if log.found else f"request_id {log.request_id} 在日志库中未找到"
    )
    result = await _parse(
        "你是模型 API 聚合平台的排查 Agent。根据客户反馈和调用日志判断责任方："
        "customer（参数错误、余额不足、key 失效等）、us（我方网关问题）、supplier（上游供应商报错/限流）。"
        "证据不足时 responsible=unknown，并在给客户的回复里礼貌索要 request_id。",
        f"客户反馈: {c.summary}\n类别: {c.category}\n模型: {c.model}\n"
        f"request_id: {c.request_id or '客户未提供'}\n{log_text}",
        Finding,
        effort="medium",
    )
    if result:
        return result
    if c.category == "rate_limit":
        return Finding(responsible="supplier", evidence="客户反馈 429", confidence=55,
                       customer_reply="收到，429 的情况我们正在协调上游扩容，有进展第一时间同步～",
                       supplier_note=f"{c.model or '模型'} 这边客户 429 比较多，麻烦看下能否扩一下量")
    return Finding(responsible="unknown", evidence=log_text, confidence=40,
                   customer_reply="收到，方便提供一下报错的 request_id 吗？我们马上排查",
                   supplier_note="")


async def stylize(text: str, style: dict, purpose: str) -> str:
    """按群的风格标记改写消息；无风格或无 Key 时原样返回。"""
    if not style:
        return text
    result = await _parse(
        "你负责把一条要发到 IM 群的消息改写成符合该群聊天风格的说法，事实和数字不能改变，"
        "不要显得机械，不要加入原文没有的承诺。",
        f"用途: {purpose}\n群风格: {style}\n原文: {text}",
        Rewrite,
        effort="low",
    )
    return result.text if result else text
