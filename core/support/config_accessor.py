"""
统一配置访问器。
集中封装常见配置分组的读取逻辑，减少各处层层 config.get(...).get(...) 的重复写法。
"""

from __future__ import annotations

from typing import Any


class ConfigAccessor:
    """统一配置访问器。"""

    def __init__(self, config: dict[str, Any] | None = None):
        # 允许传入 None，便于在测试或局部工具场景下安全构造访问器。
        self.config = config or {}

    def web_admin_config(self) -> dict[str, Any]:
        """获取 Web 管理端配置。"""
        # 所有分组访问都做一次类型收敛，避免上游配置结构异常时把非 dict 继续向下传播。
        value = self.config.get("web_admin", {})
        return value if isinstance(value, dict) else {}

    def web_admin_password(self) -> str:
        """获取 Web 管理端密码。"""
        password = self.web_admin_config().get("password", "")
        return password if isinstance(password, str) else ""

    def message_format_config(self) -> dict[str, Any]:
        """获取消息格式配置。"""
        value = self.config.get("message_format", {})
        return value if isinstance(value, dict) else {}

    def event_deduplication_config(self) -> dict[str, Any]:
        """获取事件去重配置。"""
        value = self.config.get("event_deduplication", {})
        return value if isinstance(value, dict) else {}

    def weather_config(self) -> dict[str, Any]:
        """获取气象配置。"""
        value = self.config.get("weather_config", {})
        return value if isinstance(value, dict) else {}

    def debug_config(self) -> dict[str, Any]:
        """获取调试配置。"""
        value = self.config.get("debug_config", {})
        return value if isinstance(value, dict) else {}

    def local_monitoring_config(self) -> dict[str, Any]:
        """获取本地监测配置。"""
        value = self.config.get("local_monitoring", {})
        return value if isinstance(value, dict) else {}

    def data_sources_config(self) -> dict[str, Any]:
        """获取数据源配置。"""
        value = self.config.get("data_sources", {})
        return value if isinstance(value, dict) else {}

    def strategies_config(self) -> dict[str, Any]:
        """获取策略配置。"""
        value = self.config.get("strategies", {})
        return value if isinstance(value, dict) else {}

    def target_sessions(self) -> list[Any]:
        """获取目标会话列表。"""
        value = self.config.get("target_sessions", [])
        return value if isinstance(value, list) else []
