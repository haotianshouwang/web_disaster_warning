"""
WebSocket Hub。
负责 Web 管理端 WebSocket 连接集合管理与消息广播，减少 WebAdminServer 中的实时推送职责。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger


class WebSocketHub:
    """WebSocket 连接与广播中心。"""

    def __init__(self):
        self.connections: list[Any] = []

    def add(self, websocket) -> None:
        """注册连接。"""
        # 这里不做去重假设，调用方保证同一 websocket 只在 accept 成功后注册一次。
        self.connections.append(websocket)

    def remove(self, websocket) -> None:
        """移除连接。"""
        if websocket in self.connections:
            self.connections.remove(websocket)

    def count(self) -> int:
        """返回当前连接数。"""
        return len(self.connections)

    async def send_full_update(
        self,
        websocket,
        data_factory: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        """向单个客户端发送完整更新。"""
        try:
            data = await data_factory()
            await websocket.send_json({"type": "full_update", "data": data})
        except Exception as e:
            logger.debug(f"[灾害预警] 发送数据失败: {e}")

    async def broadcast_update(
        self,
        data_factory: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        """向所有连接客户端广播常规更新。"""
        if not self.connections:
            return

        # 数据只构建一次，再复用到所有连接，避免同一轮广播内重复读取服务状态。
        data = await data_factory()
        message = {"type": "update", "data": data}
        disconnected = []
        for websocket in list(self.connections):
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.append(websocket)

        for websocket in disconnected:
            self.remove(websocket)

    async def broadcast_event(
        self,
        data_factory: Callable[[], Awaitable[dict[str, Any]]],
        event_data: dict[str, Any] | None = None,
    ) -> None:
        """向所有连接客户端广播事件更新。"""
        if not self.connections:
            return

        data = await data_factory()
        message: dict[str, Any] = {
            "type": "event",
            "data": data,
        }
        if event_data:
            message["new_event"] = event_data

        disconnected = []
        for websocket in list(self.connections):
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.append(websocket)

        for websocket in disconnected:
            self.remove(websocket)

        if event_data:
            logger.debug(f"[灾害预警] 已推送新事件到 {self.count()} 个客户端")

    async def close_all(self) -> None:
        """关闭并清空所有连接。"""
        for websocket in list(self.connections):
            try:
                await websocket.close()
            except Exception:
                pass
        self.connections.clear()
