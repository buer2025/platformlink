"""评估用例：改提示词 / 换模型后跑一遍，看准确率有没有退步。

每类用例只断言「做对了没有」，不要求措辞一致。日志数据是 new-api 结构的模拟数据。
"""

NOW_OFFSET = 0  # 日志时间相对现在的秒数偏移（运行时填充）

# (request_id, type, content, channel_id) —— type 5 = 错误
LOGS = [
    ("req-e-429", 5, "upstream error: status_code=429, Too Many Requests: rate limit exceeded for seedance-1.0-pro", 7),
    ("req-e-500", 5, "upstream error: status_code=500, internal server error from provider", 8),
    ("req-e-401", 5, "invalid token: 无效的令牌", 0),
    ("req-e-quota", 5, "user quota is not enough: 用户额度不足，剩余额度 0", 0),
    ("req-e-sensitive", 5, "sensitive words detected: 输入内容包含敏感词，请求已被拦截", 0),
    ("req-e-ok", 2, "", 7),
]

TRIAGE = [
    # 客户消息, 期望类别, 期望责任方（None 表示不检查）, 是否问题
    ("seedance 一直报 429 request_id: req-e-429", "rate_limit", "supplier", True),
    ("seedance 500 了，request_id: req-e-500", "error", "supplier", True),
    ("调用报错 401，request_id: req-e-401", "error", "customer", True),
    ("怎么突然调不通了 request_id: req-e-quota", "error", "customer", True),
    ("请求被拦截了？request_id: req-e-sensitive", "error", "customer", True),
    ("下周活动想把 seedance 扩到 300 RPM，能安排吗", "usage_request", None, True),
    ("今天天气真不错", None, None, False),
    # 以下来自真实群聊里容易判错的说法
    ("gpt-image 生成特别慢，一张要 40 多秒，正常吗", None, None, True),
    ("麻烦帮我们开通一下 claude 的权限", "usage_request", None, True),
    ("哈哈哈 你们这个 bot 还挺像真人", None, None, False),
    ("@技术支持 在吗", None, None, False),
]

# 没有证据时不能给高分
LOW_CONFIDENCE = ["seedance 超时了 request_id: req-e-404"]

FOLLOWUP = [
    # 客户新消息, 事件是否已解决, 期望要不要回, 期望问题是否仍在
    ("好的", False, False, False),
    ("👍", False, False, False),
    ("好了，谢谢！", True, True, False),
    ("还是不行啊", True, True, True),
    ("大概多久能好？", False, True, False),
    ("收到，我们这边再观察下", False, False, False),
    ("又出现了，request_id: req-x-77", True, True, True),
    ("你们这个已经是这周第三次了，能不能靠谱点", False, True, None),
    ("ok 辛苦了", False, False, False),
]

# 非问题消息：要不要开口（greeting 直接回 / question 查知识库回 / none 不插话）
REPLY_KIND = [
    ("@技术支持 在吗", "greeting"),
    ("有人吗？", "greeting"),
    ("你们支持 gpt-image 吗", "question"),
    ("请问发票怎么开", "question"),
    ("哈哈哈 你们这个 bot 还挺像真人", "none"),
    ("张三你下午把报表发我一下", "none"),
    ("👍", "none"),
]

QUOTE = [
    # 被引用的消息, 客户新消息, 期望要不要回, 期望问题是否仍在
    ("上游限流，已协调扩容，稍后同步", "这个现在好了吗", True, False),
    ("上游限流，已协调扩容，稍后同步", "这个还是不行", True, True),
    ("上游限流，已协调扩容，稍后同步", "👌", False, False),
]

# 长对话压缩：摘要必须保留所有硬信息
COMPRESS = """[09:01] 王总: 早上好，seedance 又报 429 了，request_id req-7f3a91
[09:02] 王总: 我们 10 点有直播，挺急的
[09:05] 王总: 在吗
[09:20] 王总: 还是不行，新的 request_id req-8812bb，报 500
[09:21] 王总: 另外 gpt-image 出图特别慢，平均 40 秒
[09:30] 王总: 哦对了，账号是 acme-prod
[09:40] 王总: 429 好像好了，500 还有
[09:41] 王总: 谢谢哈"""
COMPRESS_MUST_KEEP = ["req-7f3a91", "req-8812bb", "seedance", "gpt-image", "acme-prod", "500"]

STYLE = {"address": "李姐", "tone": "轻松亲和", "particles": ["哈", "~"],
         "taboos": ["哈哈", "亲"], "samples": ["李姐放心，这个我盯着呢"]}
STYLE_TEXTS = ["您的余额不足，请充值后再试。", "这个问题是上游限流导致的，我们已经在协调扩容了。"]
