"""审核类型常量，供队列消费和结果处理使用。"""

DEFAULT_CHECK_TYPES = ["latex", "difficulty", "answer", "synthesis"]
VALID_CHECK_TYPES = set(DEFAULT_CHECK_TYPES)
