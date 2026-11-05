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


INTEGRATE = (
    "你是技术支持，客户在群里连续发了几条消息。把下面这条还没发出的回复草稿改写成一条完整的回复，"
    "要覆盖客户所有消息里的问题和信息（例如追问、补充的现象、催促），不要逐条复述，像真人一次性回完。"
    "草稿里的事实和结论不能改变，不要新增没有依据的承诺。2~4 句，口语化。"
)
FOLLOWUP = (
    "你是技术支持。我们之前已经回复过客户，现在客户又发了新消息（可能是追问、催促或补充）。"
    "根据当前处理进展，写一条针对这条新消息的简短回复：先回应他这句话，再说明当前进展和下一步；"
    "进展里没有的信息不要编造，不要重复之前已经说过的大段解释。1~3 句，口语化。"
    "如果这条消息不需要回复（例如只是「好的」「谢谢」「收到」），text 返回空字符串。"
)


async def integrate(draft: str, conversation: str, context: str) -> str:
    """把待发回复改写成覆盖客户全部消息的一条回复。"""
    r = await _parse(INTEGRATE, f"客户消息（按时间）:\n{conversation}\n\n处理进展:\n{context}\n\n回复草稿:\n{draft}",
                     Rewrite, effort="low")
    return r.text if r else draft


async def followup(conversation: str, new_text: str, context: str) -> str:
    """回复已发出后，客户追问 / 催促时的跟进回复。"""
    r = await _parse(FOLLOWUP, f"客户消息（按时间）:\n{conversation}\n\n客户最新这条: {new_text}\n\n处理进展:\n{context}",
                     Rewrite, effort="low")
    if r is None:
        return "" if _NO_REPLY.match(new_text.strip()) else "收到，这边还在跟进，有进展第一时间同步您～"
    return r.text.strip()


_NO_REPLY = re.compile(
    r"^(?:(?:好的?|好滴|ok|okay|收到|谢谢|多谢|感谢|辛苦了?|thx|thanks|👌|👍|🙏|嗯+|行|了解)[\s,，!！。.~～啦呀哈]*)+$",
    re.IGNORECASE)


class StyleProfile(BaseModel):
    summary: str = Field(description="一句话描述我方在这个群的说话风格")
    address: str = Field(description="对客户的称呼，如 王总 / 哥 / 老师；没有固定称呼为空")
    tone: str = Field(description="语气：正式 / 专业亲和 / 轻松 / 熟络 / 英文 等")
    particles: list[str] = Field(description="常用语气词，如 哈、呀、哦、~、啦")
    emoji: list[str] = Field(description="常用表情，没有为空")
    phrases: list[str] = Field(description="常用口头禅或固定说法")
    taboos: list[str] = Field(description="应避免的说法（从客户反感的反应中推断），没有为空")
    samples: list[str] = Field(description="挑 3~5 条最能代表我方风格的原话（原样摘录）")


def describe_style(style: dict) -> str:
    """把风格标记整理成给模型看的说明。"""
    label = {"address": "称呼", "tone": "语气", "particles": "常用语气词", "emoji": "常用表情",
             "phrases": "口头禅", "taboos": "禁忌（绝对不要出现）", "summary": "整体风格"}
    lines = []
    for k, name in label.items():
        v = style.get(k)
        if v:
            lines.append(f"- {name}：{'、'.join(v) if isinstance(v, list) else v}")
    if samples := style.get("samples"):
        lines.append("- 我方在这个群的真实发言（模仿它们的句式、长度和口吻）：")
        lines += [f"  · {x}" for x in samples[:6]]
    return "\n".join(lines)


STYLIZE = """你负责把一条要发到 IM 群的消息改写成这个群里我方同事平时说话的样子，让对方感觉是熟悉的人在回，而不是机器人。

要求：
- 事实、数字、request_id、结论一个字都不能改，不要加入原文没有的承诺或信息。
- 有称呼就自然地用上（开头或句中，不要每句都叫）。
- 克制：整条消息里语气词（含「～」）合计不超过 2 个，表情最多 1 个；内容严肃（报错原因、余额、故障）时更少。
- 不要照搬样本里的寒暄（早呀、晚安等），问候语必须和「当前时间」相符，没把握就不问候。
- 不要补充原文没有的句子（例如「有进展第一时间同步」），只调整说法。
- 句子长度、断句方式模仿真实发言样本；正式的群就保持正式，不要强行卖萌。
- 禁忌里的说法绝对不能出现。
- 只输出改写后的消息本身。"""


def _now_cn() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M（%A）")


async def stylize(text: str, style: dict, purpose: str) -> str:
    """按群的风格标记改写消息；无 Key 时做最基本的称呼处理。"""
    if not style or not any(style.values()):
        return text
    result = await _parse(
        STYLIZE,
        f"消息用途: {purpose}\n当前时间: {_now_cn()}\n\n这个群的风格:\n{describe_style(style)}\n\n原文:\n{text}",
        Rewrite,
        effort="low",
    )
    if result and result.text.strip():
        return result.text.strip()
    address = style.get("address")
    if address and address not in text:
        return f"{address}，{text}"
    return text


async def learn_style(ours: list[str], theirs: list[str]) -> StyleProfile | None:
    """从群聊历史里学习我方同事的说话风格。"""
    if not ours:
        return None
    convo = "\n".join(f"[我方] {x}" for x in ours[-80:])
    if theirs:
        convo += "\n\n（客户的发言，用于判断关系远近和客户反感什么）\n" + "\n".join(
            f"[客户] {x}" for x in theirs[-40:])
    return await _parse(
        "你在分析一个工作群里「我方」技术支持同事是怎么和客户说话的，提炼成风格档案，"
        "让 AI 之后回复时能模仿。只根据聊天记录里真实出现的用法总结，不要编造；样本必须原样摘录。",
        convo, StyleProfile, effort="medium")
