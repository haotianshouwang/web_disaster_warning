"""
OneBot 11 协议抽象基类与公共常量。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

# 事件回调签名: async def callback(event: OneBotEvent) -> None
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ProtocolState(Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class OneBotConfig:
    """OneBot 11 连接配置。"""
    # === 服务器模式（NapCat → 插件）===
    http_server_enabled: bool = False
    http_server_host: str = "0.0.0.0"
    http_server_port: int = 5700
    http_server_path: str = "/onebot"
    http_server_token: str = ""

    # === HTTP 客户端（插件 → NapCat）===
    http_client_enabled: bool = False
    http_client_url: str = "http://127.0.0.1:3000"
    http_client_token: str = ""

    # === WS 服务器模式（NapCat → 插件）===
    ws_server_enabled: bool = False
    ws_server_host: str = "0.0.0.0"
    ws_server_port: int = 5701
    ws_server_token: str = ""

    # === WS 客户端模式（插件 → NapCat）===
    ws_client_enabled: bool = False
    ws_client_url: str = "ws://127.0.0.1:3001"
    ws_client_token: str = ""

    # === 通用（向后兼容） ===
    access_token: str = ""
    reconnect_interval: float = 5.0
    reconnect_max_attempts: int = 0  # 0 = 无限重连


class BaseProtocol(ABC):
    """协议适配器基类。"""

    def __init__(self, config: OneBotConfig):
        self.config = config
        self._state = ProtocolState.STOPPED
        self._callbacks: list[EventCallback] = []

    @property
    def state(self) -> ProtocolState:
        return self._state

    def set_state(self, s: ProtocolState) -> None:
        self._state = s

    def on_event(self, cb: EventCallback) -> None:
        """注册事件回调。"""
        self._callbacks.append(cb)

    async def emit_event(self, event: dict[str, Any]) -> None:
        """触发所有已注册的事件回调。"""
        for cb in self._callbacks:
            try:
                await cb(event)
            except Exception:
                pass

    @abstractmethod
    async def start(self) -> None:
        """启动协议适配器。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止协议适配器。"""
