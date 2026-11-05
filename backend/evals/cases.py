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
]

STYLE = {"address": "李姐", "tone": "轻松亲和", "particles": ["哈", "~"],
         "taboos": ["哈哈", "亲"], "samples": ["李姐放心，这个我盯着呢"]}
STYLE_TEXTS = ["您的余额不足，请充值后再试。", "这个问题是上游限流导致的，我们已经在协调扩容了。"]
