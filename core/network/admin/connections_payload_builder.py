"""
Web 管理端连接状态载荷构建器。
统一组装 /api/connections 与实时数据中的连接状态视图，避免重复拼装逻辑。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...support.config_accessor import ConfigAccessor


class ConnectionsPayloadBuilder:
    """连接状态载荷构建器。"""

    SOURCE_CONFIG_KEY: dict[str, str] = {
        "fan_studio_all": "fan_studio",
        "p2p_main": "p2p_earthquake",
        "wolfx_all": "wolfx",
        "global_quake": "global_quake",
    }

    def __init__(
        self,
        disaster_service,
        config: dict[str, Any],
        latency_cache: dict[str, float | None] | None = None,
    ):
        self.disaster_service = disaster_service
        self.config = config
        self.config_accessor = ConfigAccessor(config)
        self.latency_cache = latency_cache if latency_cache is not None else {}

    def build(self, expected_sources: dict[str, str]) -> dict[str, dict[str, Any]]:
        """构建连接状态视图。"""
        if not self.disaster_service or not self.disaster_service.ws_manager:
            return {}

        actual_connections = (
            self.disaster_service.ws_manager.get_all_connections_status()
        )
        status_data = self.disaster_service.get_service_status()
        sub_source_status = status_data.get("sub_source_status", {})
        data_sources_config = self.config_accessor.data_sources_config()

        merged_connections: dict[str, dict[str, Any]] = {}
        for source_name, display_name in expected_sources.items():
            # expected_sources 决定“理论上应该展示哪些源”，actual_connections 则提供实际连接态；
            # 两者合并后前端才能同时看到未连接但受支持的数据源。
            if source_name in actual_connections:
                conn_info = actual_connections[source_name].copy()
            else:
                conn_info = {
                    "connected": False,
                    "retry_count": 0,
                    "has_handler": False,
                    "status": "未连接",
                }

            cfg_key = self.SOURCE_CONFIG_KEY.get(source_name, source_name)
            conn_info["enabled"] = bool(
                data_sources_config.get(cfg_key, {}).get("enabled", False)
            )
            conn_info["latency"] = self.latency_cache.get(source_name)

            if source_name == "fan_studio_all":
                conn_info["sub_sources"] = sub_source_status.get("fan_studio", {})
            elif source_name == "p2p_main":
                conn_info["sub_sources"] = sub_source_status.get("p2p_earthquake", {})
            elif source_name == "wolfx_all":
                conn_info["sub_sources"] = sub_source_status.get("wolfx", {})
            elif source_name == "global_quake":
                conn_info["sub_sources"] = sub_source_status.get("global_quake", {})

            merged_connections[display_name] = conn_info

        return merged_connections

    def build_api_payload(self, expected_sources: dict[str, str]) -> dict[str, Any]:
        """构建 /api/connections 响应载荷。"""
        return {
            "connections": self.build(expected_sources),
            "timestamp": datetime.now().isoformat(),
        }
