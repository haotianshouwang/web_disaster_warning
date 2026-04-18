"""
灾害服务手动重连编排服务。
负责批量检查连接状态、补齐 connection_info 并触发 WebSocket 强制重连，
进一步减少 DisasterWarningService 中的运维编排职责。
"""

from __future__ import annotations

from astrbot.api import logger


class DisasterServiceReconnectService:
    """灾害服务手动重连编排服务。"""

    def __init__(self, service):
        self.service = service

    async def reconnect_all_sources(self) -> dict[str, str]:
        """强制重连所有已启用但离线的数据源。"""
        results: dict[str, str] = {}
        if not self.service.ws_manager:
            return {"error": "WebSocket管理器未初始化"}

        reconnect_count = 0
        for conn_name, conn_config in self.service.connections.items():
            # 已在线连接无需重复触发重连，避免无谓打断。
            if self._is_connected(conn_name):
                results[conn_name] = "已连接 (跳过)"
                continue

            try:
                self._ensure_connection_info(conn_name, conn_config)
                triggered = await self._force_reconnect(conn_name)
                if triggered:
                    results[conn_name] = "✅ 已触发重连"
                    reconnect_count += 1
                else:
                    results[conn_name] = "⚠️ 重连未触发"
            except Exception as e:
                results[conn_name] = f"❌ 失败: {e}"
                logger.error(f"[灾害预警] 手动重连 {conn_name} 失败: {e}")

        logger.info(f"[灾害预警] 手动重连操作完成，触发了 {reconnect_count} 个重连任务")
        return results

    def _is_connected(self, conn_name: str) -> bool:
        """检查指定连接当前是否已连接。"""
        if conn_name not in self.service.ws_manager.connections:
            return False
        ws = self.service.ws_manager.connections[conn_name]
        return not ws.closed

    def _ensure_connection_info(self, conn_name: str, conn_config: dict) -> None:
        """确保 WebSocketManager 中存在连接元信息。"""
        if conn_name in self.service.ws_manager.connection_info:
            return

        connection_info = {
            "connection_name": conn_name,
            "handler_type": conn_config["handler"],
            "data_source": self.service.get_data_source_from_connection(conn_name),
            "established_time": None,
            "backup_url": conn_config.get("backup_url"),
        }
        self.service.ws_manager.connection_info[conn_name] = {
            "uri": conn_config["url"],
            "headers": None,
            "connection_type": "websocket",
            "established_time": None,
            "retry_count": 0,
            **connection_info,
        }

    async def _force_reconnect(self, conn_name: str) -> bool:
        """调用 WebSocketManager 执行强制重连。"""
        if not hasattr(self.service.ws_manager, "force_reconnect"):
            return False
        return await self.service.ws_manager.force_reconnect(conn_name)
