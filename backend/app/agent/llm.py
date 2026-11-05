import logging
import re

import anthropic
from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger(__name__)


class Classification(BaseModel):
    is_issue: bool = Field(description="是否需要我方处理：故障/报错反馈，或用量、扩容、开通等需求；闲聊和致谢为 false")
    category: str = Field(description="error（调用报错）| rate_limit（429/限流）| usage_request（用量/扩容/开通/额度需求）| other")
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
    self_check: str = Field(default="", description="反思：这个结论最可能错在哪里，为什么仍这样判断")


class KnowledgeDraft(BaseModel):
    title: str = Field(description="一句话标题，如『seedance 上游 429 需找供应商扩量』")
    symptoms: str = Field(description="现象：报错原文、错误码、客户描述")
    keywords: list[str] = Field(description="3-6 个用于匹配的关键词/错误码")
    responsible: str = Field(description="customer | us | supplier | unknown")
    root_cause: str
    solution: str = Field(description="处理步骤，下次遇到照着做")
    reply_template: str = Field(description="给客户的回复参考，口语化")


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
    if any(k in text for k in ("扩容", "加量", "用量", "额度", "开通", "提额", "并发")):
        cat = "usage_request"
    elif "429" in low or "限流" in text or "rate limit" in low:
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
        "你是模型 API 聚合平台的技术支持分诊员。判断客户群里这条消息是否需要处理（故障反馈或用量需求）并抽取字段。",
        text,
        Classification,
        effort="low",
    )
    return result or _rule_classify(text)


BASE_INVESTIGATE = (
    "你是模型 API 聚合平台的排查 Agent。根据客户反馈和日志库（new-api 结构）查询结果判断责任方："
    "customer（参数错误、余额不足、key 失效、内容审核等）、us（我方网关/配置问题）、"
    "supplier（上游渠道报错、限流、超时）。日志里的 channel_id 是上游渠道。"
    "证据不足时 responsible=unknown，并在给客户的回复里礼貌索要 request_id。"
    "回复要像真人技术支持，简短自然，不要暴露内部渠道编号和供应商名称。"
)


async def investigate(c: Classification, evidence: str, instructions: str = "",
                      found: bool = False, knowledge: str = "") -> Finding:
    system = BASE_INVESTIGATE + (f"\n\n本链路的处理说明：\n{instructions}" if instructions else "")
    if knowledge:
        system += ("\n\n知识库中相似的历史经验（现象吻合时优先参考，并在 evidence 中写明引用了哪条 #id）：\n"
                   + knowledge)
    result = await _parse(
        system,
        f"客户原话: {c.summary}\n类别: {c.category}\n模型: {c.model}\n"
        f"request_id: {c.request_id or '客户未提供'}\n\n日志库查询结果:\n{evidence}",
        Finding,
        effort="medium",
    )
    if result:
        if not found:  # 没有任何日志证据时封顶，避免自动发出没把握的结论
            result.confidence = min(result.confidence, 60)
        return result
    if c.category == "usage_request":
        return Finding(responsible="supplier", evidence=evidence, confidence=50,
                       customer_reply="收到，用量需求我们这边登记一下，和上游确认好额度后同步您～",
                       supplier_note=f"{c.model or '模型'} 这边客户有加量需求，麻烦看下能否扩一下")
    if c.category == "rate_limit":
        return Finding(responsible="supplier", evidence="客户反馈 429", confidence=55,
                       customer_reply="收到，429 的情况我们正在协调上游扩容，有进展第一时间同步～",
                       supplier_note=f"{c.model or '模型'} 这边客户 429 比较多，麻烦看下能否扩一下量")
    return Finding(responsible="unknown", evidence=evidence, confidence=40,
                   customer_reply="收到，方便提供一下报错的 request_id 吗？我们马上排查",
                   supplier_note="")


async def distill(timeline: str) -> KnowledgeDraft | None:
    """把一次处理完的事件总结成可复用的知识条目。"""
    return await _parse(
        "你是技术支持团队的知识管理员。根据一次问题处理的完整时间线，提炼出下次能直接复用的经验。"
        "只写时间线里确实出现过的事实，不要编造。",
        timeline, KnowledgeDraft, effort="medium")


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
