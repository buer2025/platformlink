import logging
import re

import anthropic
from pydantic import BaseModel, Field

from app.agent import jev
from app.config import settings
from app.services.trace import TracedClient

log = logging.getLogger(__name__)


class Classification(BaseModel):
    is_issue: bool = Field(description="是否需要我方处理：故障/报错反馈，或用量、扩容、开通等需求；闲聊和致谢为 false")
    category: str = Field(description="error（调用报错）| rate_limit（429/限流）| usage_request（用量/扩容/开通/额度需求）| other")
    model: str = Field(description="涉及的模型名，未知为空")
    request_id: str = Field(description="消息中的 request_id，没有为空")
    summary: str = Field(description="一句话概括问题")
    confidence: int = Field(description="0-100")
    reply_kind: str = Field(description=(
        "仅当 is_issue=false 时有意义：greeting（客户在招呼/呼叫我方，如「在吗」「有人吗」「@技术支持」）"
        "| question（向我方的一般咨询，如「你们支持 xx 模型吗」「怎么开发票」）"
        "| none（闲聊、玩笑、表情、致谢、群里其他人之间的对话，不需要我们开口）"))


class Finding(BaseModel):
    responsible: str = Field(description="customer | us | supplier | unknown")
    evidence: str = Field(description="判断依据")
    confidence: int = Field(description="0-100，证据越充分越高；没有日志证据时不应超过 60")
    customer_reply: str = Field(description="给客户的回复草稿")
    supplier_note: str = Field(description="若是供应商问题，发给供应商的描述，否则为空")
    # 不给默认值：结构化输出里带默认值的字段模型常常直接省略，反思就形同虚设
    self_check: str = Field(description="反思：这个结论最可能错在哪里，为什么仍这样判断")


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


_clients: dict[str, TracedClient] = {}


def _client() -> TracedClient | None:
    """所有 Claude 调用都经过这里；返回的客户端会把每次调用留存到 model_calls。"""
    key = settings.anthropic_api_key
    if not key:
        return None
    if key not in _clients:
        _clients[key] = TracedClient(anthropic.AsyncAnthropic(
            api_key=key, base_url=settings.anthropic_base_url or None))
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


GREETING = re.compile(r"^(?:@\S+\s*)*(?:在吗|在不在|有人吗|有人在吗|hello|hi|你好|您好)[\s?？。!！~～]*$",
                      re.IGNORECASE)


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
        kind = "greeting" if GREETING.match(text.strip()) else "none"
        return Classification(is_issue=False, category="other", model="", request_id=rid,
                              summary="", confidence=50, reply_kind=kind)
    model = (m.group(0) if (m := re.search(r"seedance[\w.-]*", low)) else "")
    return Classification(is_issue=True, category=cat, model=model, request_id=rid,
                          summary=text[:80], confidence=60, reply_kind="none")


JEV_SURE = 0.2  # Jev 给出的「成立」概率低于这个值才视为有把握的「否」，否则交给 Claude


async def classify(text: str) -> Classification:
    """分诊。启用 Jev 时先用它做亚秒级判断：有把握不是问题就直接返回（群里大部分消息），
    是问题或拿不准时才调 Claude 抽取模型 / request_id / 摘要。"""
    if jev.enabled():
        a = await jev.decide(jev.triage_state(text), jev.TRIAGE)
        if a and a["is_issue"]["noul"] <= JEV_SURE:
            rid = m.group(1) if (m := REQ_ID.search(text)) else ""
            return Classification(
                is_issue=False, category="other", model="", request_id=rid, summary="",
                confidence=round((1 - a["is_issue"]["noul"]) * 100),
                reply_kind=a["reply_kind"]["choice"])
    result = await _parse(
        "你是模型 API 聚合平台的技术支持分诊员。判断客户群里这条消息是否需要处理（故障反馈或用量需求）并抽取字段。"
        "is_issue=false 时必须认真填 reply_kind：客户叫我们（在吗/有人吗/@我方）→ greeting；"
        "向我方提一般性问题（支持哪些模型、怎么开票、价格、怎么接入等）→ question；"
        "闲聊、表情、致谢、群里其他人之间说话 → none。",
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
    "回复要像真人技术支持，简短自然，不要出现「渠道」二字、渠道编号和供应商名称，需要时说「上游服务」。"
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
                       supplier_note=f"{c.model or '模型'} 这边客户有加量需求，麻烦看下能否扩一下",
                       self_check="AI 不可用，规则兜底")
    if c.category == "rate_limit":
        return Finding(responsible="supplier", evidence="客户反馈 429", confidence=55,
                       customer_reply="收到，429 的情况我们正在协调上游扩容，有进展第一时间同步～",
                       supplier_note=f"{c.model or '模型'} 这边客户 429 比较多，麻烦看下能否扩一下量",
                       self_check="AI 不可用，规则兜底")
    return Finding(responsible="unknown", evidence=evidence, confidence=40,
                   customer_reply="收到，方便提供一下报错的 request_id 吗？我们马上排查",
                   supplier_note="", self_check="AI 不可用，规则兜底")


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


class FollowupDecision(BaseModel):
    should_reply: bool = Field(description="这条消息是否需要我们回复")
    problem_persists: bool = Field(description="仅当客户这条消息本身明确说问题还在 / 又出现 / 没解决时为 true；"
                                               "「好的」「收到」、表情、致谢、询问进度或预计时间（如「多久能好」）一律为 false")
    reason: str = Field(description="一句话说明判断理由")
    text: str = Field(description="回复内容；不需要回复时为空")


PERSISTS = re.compile(r"(还是|依然|仍然|又|再次|没好|没解决|不行|还在报|还报)")


async def followup(conversation: str, new_text: str, context: str, quoted: str = "",
                   resolved: bool = False) -> FollowupDecision:
    """客户在已有事件里又说话（可能引用了某条消息）：判断要不要回、问题是否还在，并起草回复。"""
    if jev.enabled():  # 「好的」「👍」这类有把握不用回的，Jev 直接定，不再调 Claude
        a = await jev.decide(jev.followup_state(conversation, new_text, context, quoted), jev.FOLLOWUP)
        if a and a["should_reply"]["noul"] <= JEV_SURE:
            return FollowupDecision(
                should_reply=False, problem_persists=a["problem_persists"]["noul"] >= 0.5,
                reason=f"Jev 判断无需回复（回复倾向 {a['should_reply']['noul']:.2f}）", text="")
    q = f"\n客户引用的消息: 「{quoted}」" if quoted else ""
    r = await _parse(
        FOLLOWUP + "\n先判断这条消息的意图，再决定要不要回：\n"
        "- 继续追问、反馈问题还在、补充信息 → 回复。\n"
        "- 对我们进展通报的「好的」「收到」「👍」→ 不回，避免刷屏。\n"
        "- 问题已解决后客户致谢（如「好了谢谢」）→ 简短暖心地回一句收个尾（例如不客气、有事随时找我），"
        "不要再复述问题。\n"
        "- 闲聊、玩笑 → 一般不回；客户明显在跟我们互动时可以轻松回一句。\n"
        "如果客户引用了我们的某条回复，要结合被引用的内容理解他在问什么。",
        f"客户消息（按时间）:\n{conversation}\n\n客户最新这条: {new_text}{q}\n\n处理进展:\n{context}",
        FollowupDecision, effort="low")
    if r is not None:
        if not r.should_reply:
            r.text = ""
        return r
    # 以下仅在 AI 不可用时兜底
    persists = bool(PERSISTS.search(new_text))
    if _NO_REPLY.match(new_text.strip()) or (resolved and not persists):  # 兜底时宁可不回
        return FollowupDecision(should_reply=False, problem_persists=False, reason="AI 不可用，规则兜底：无需回复", text="")
    return FollowupDecision(should_reply=True, problem_persists=persists,
                            reason="AI 不可用，规则兜底", text="收到，这边还在跟进，有进展第一时间同步您～")


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
    if fixes := style.get("corrections"):
        lines.append("- 同事修改过 AI 原稿的例子（最重要：照着同事的改法调整措辞、长度和语气）：")
        lines += [f"  · AI：{x['ai'][:160]}\n    同事改成：{x['human'][:160]}" for x in fixes]
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


class Memory(BaseModel):
    text: str = Field(description="一句话记忆，60 字以内")


async def summarize_memory(transcript: str) -> str:
    """把一次处理过程压缩成一句客户记忆：下次和这个客户打交道时值得知道的事。"""
    r = await _parse(
        "把下面这次问题处理压缩成一句话的「客户记忆」，供下次和这个客户沟通时参考。"
        "格式：日期省略，写「问题 + 原因/结果 + 值得记住的客户特点或偏好」，例如："
        "「seedance 上游 429，已协调扩容；客户做直播，很在意恢复时间」。只写记录里出现过的事实，60 字以内。",
        transcript, Memory, effort="low")
    return r.text.strip() if r else ""


async def summarize_conversation(previous: str, transcript: str) -> str:
    """把一段较早的客户对话压缩成摘要（增量：在已有摘要基础上合并新的一段）。"""
    r = await _parse(
        "你在帮技术支持压缩一段很长的客户群聊，后面的排查和回复只会看到这份摘要加最近几条原话。"
        "把「已有摘要」和「新的一段对话」合并成一份新摘要：\n"
        "- 必须原样保留所有 request_id、模型名、报错码、时间点、数量等硬信息\n"
        "- 写清客户提过哪些问题、各自的进展，客户的情绪和明确提出的要求\n"
        "- 删掉寒暄和重复内容，不要编造，不要给建议\n"
        "- 分条列出，400 字以内",
        f"已有摘要:\n{previous or '（无）'}\n\n新的一段对话:\n{transcript}",
        Rewrite, effort="low")
    return r.text.strip() if r and r.text.strip() else ""


COMPOSE = """你是我方技术支持，在 IM 群里亲自打字回复。根据「要表达的意思」和「上下文」写一条消息。

要求：
- 像真人当下打出来的话：回应对方刚说的具体内容，不要套话、不要每次都一样的句式。
- 只表达给定的意思，不编造事实、不做原文没有的承诺。
- 简短：1~2 句。不要套用「有结果第一时间跟您说」这类固定结尾，换着说或干脆不说。
- 按群风格说话（称呼、语气词、表情克制使用），问候语要和当前时间相符。
- 只输出消息本身。"""


async def compose(intent: str, context: str, style: dict, fallback: str) -> str:
    """让 AI 按上下文和群风格直接写一条短消息；AI 不可用时用 fallback（再做基本称呼处理）。"""
    r = await _parse(
        COMPOSE,
        f"要表达的意思: {intent}\n当前时间: {_now_cn()}\n\n上下文:\n{context}\n\n"
        f"群风格:\n{describe_style(style or {}) or '（未设置，用自然、专业亲和的语气）'}",
        Rewrite, effort="low")
    if r and r.text.strip():
        return r.text.strip()
    address = (style or {}).get("address")
    return f"{address}，{fallback}" if address and address not in fallback else fallback


class ImageReading(BaseModel):
    is_error: bool = Field(description="截图里是否是报错 / 异常信息")
    text: str = Field(description="截图里与问题相关的文字，尽量原样摘录：报错信息、状态码、request_id、模型名、时间")


async def read_image(data: bytes, mime: str) -> str:
    """识别客户发来的截图（报错弹窗、控制台、日志），返回摘录的文字；失败返回空。"""
    import base64
    client = _client()
    if client is None:
        return ""
    try:
        resp = await client.messages.parse(
            model=settings.anthropic_model, max_tokens=1500,
            system="你在帮技术支持看客户发来的截图。原样摘录与问题相关的文字，特别是报错信息、状态码、"
                   "request_id、模型名和时间；不相关的界面文字忽略，不要推测。",
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime,
                                             "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": "请摘录这张截图里与问题相关的文字。"}]}],
            output_format=ImageReading, output_config={"effort": "low"})
    except (anthropic.APIError, ValueError):
        log.exception("截图识别失败")
        return ""
    r = resp.parsed_output
    return r.text.strip() if r else ""


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
