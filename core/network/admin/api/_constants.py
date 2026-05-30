"""管理端 API 共享常量。避免在多个路由文件中重复定义。"""

# 通知通道配置中的敏感字段（API返回时掩码为 ***）
SENSITIVE_KEYS = frozenset({
    "auth_code", "access_token",
    "http_server_token", "http_client_token",
    "ws_server_token", "ws_client_token",
})

# 事件类型中文 → 英文映射（filter_types 使用英文 key）
EVENT_TYPE_MAP = {"地震": "earthquake", "海啸": "tsunami", "气象": "weather"}
