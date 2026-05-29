"""
OneBot 11 统一管理器。

根据配置自动启动已启用的协议适配器，提供统一的消息发送接口。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from .http_server import OneBotHttpServer
from .http_client import OneBotHttpClient
from .ws_server import OneBotWsServer
from .ws_client import OneBotWsClient
from .protocols import OneBotConfig, EventCallback, ProtocolState


class OneBotManager:
    """OneBot 11 协议统一入口。

    支持 HTTP/WS 四种模式的组合启用，按优先级选择发送通道。
    """

    def __init__(self, config: OneBotConfig):
        self._cfg = config
        self._adapters: dict[str, Any] = {}
        self._callbacks: list[EventCallback] = []

        if config.http_server_enabled:
            self._adapters["http_server"] = OneBotHttpServer(config)
        if config.http_client_enabled:
            self._adapters["http_client"] = OneBotHttpClient(config)
        if config.ws_server_enabled:
            self._adapters["ws_server"] = OneBotWsServer(config)
        if config.ws_client_enabled:
            self._adapters["ws_client"] = OneBotWsClient(config)

        for adapter in self._adapters.values():
            adapter.on_event(self._on_event)

    async def _on_event(self, event: dict[str, Any]) -> None:
        for cb in self._callbacks:
            try:
                asyncio.create_task(cb(event))
            except Exception:
                pass

    def set_event_callback(self, cb: EventCallback) -> None:
        """注册全局事件回调。"""
        self._callbacks.append(cb)

    async def start(self) -> None:
        """启动所有已启用的协议适配器。"""
        for name, adapter in self._adapters.items():
            try:
                await adapter.start()
            except Exception as e:
                logger.error(f"[OneBot] {name} 启动失败: {e}")

    async def stop(self) -> None:
        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
            except Exception as e:
                logger.warning(f"[OneBot] {name} 停止异常: {e}")

    @property
    def connected(self) -> bool:
        return any(
            a.state == ProtocolState.CONNECTED for a in self._adapters.values()
        )

    # ── 统一发送接口 ──

    async def send_group_msg(self, group_id: int, message: str) -> dict:
        """发送群消息（自动选择可用通道）。"""
        return await self._send("group", group_id, message)

    async def send_private_msg(self, user_id: int, message: str) -> dict:
        """发送私聊消息。"""
        return await self._send("private", user_id, message)

    async def _send(self, msg_type: str, target_id: int, message: str) -> dict:
        """按优先级选择发送通道: WS Client > WS Server > HTTP Client。"""
        # WS 客户端（插件主动连 NapCat）
        adapter = self._adapters.get("ws_client")
        if adapter and adapter.state == ProtocolState.CONNECTED:
            if hasattr(adapter, "send_msg"):
                return await adapter.send_msg(msg_type, target_id, message)

        # WS 服务器（NapCat 反连 → 复用已连接通道发送）
        adapter = self._adapters.get("ws_server")
        if adapter and adapter.state == ProtocolState.CONNECTED:
            if hasattr(adapter, "send_msg"):
                return await adapter.send_msg(msg_type, target_id, message)

        # HTTP 客户端
        adapter = self._adapters.get("http_client")
        if adapter and adapter.state == ProtocolState.CONNECTED:
            if hasattr(adapter, "send_msg"):
                return await adapter.send_msg(msg_type, target_id, message)

        return {"status": "failed", "retcode": -1, "msg": "no available channel"}
