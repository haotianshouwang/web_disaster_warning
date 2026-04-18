"""
WebSocket连接管理器
适配数据处理器架构，提供更好的错误处理和重连机制
(已迁移至 aiohttp 实现以获得更好的跨平台兼容性)
"""

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from aiohttp import ClientWebSocketResponse

from astrbot.api import logger

from .websocket_dispatch_service import WebSocketDispatchService
from .websocket_reconnect_service import WebSocketReconnectService
from .websocket_runtime_service import WebSocketRuntimeService


class WebSocketManager:
    """WebSocket连接管理器"""

    def __init__(self, config: dict[str, Any], message_logger=None, telemetry=None):
        # manager 本体保留共享状态与 façade 接口，
        # 实际的生命周期、重连、消息循环处理已分别拆到独立服务。
        self.config = config
        self.message_logger = message_logger
        self._telemetry = telemetry
        self.connections: dict[str, ClientWebSocketResponse] = {}
        self.message_handlers: dict[str, Callable] = {}
        self.reconnect_tasks: dict[str, asyncio.Task] = {}
        self.connection_retry_counts: dict[str, int] = {}
        self.fallback_retry_counts: dict[str, int] = {}  # 兜底重试计数
        self.connection_info: dict[
            str, dict
        ] = {}  # 连接元信息，供状态展示/重连/通知复用
        self.running = False
        self.session: aiohttp.ClientSession | None = None
        self.heartbeat_tasks: dict[str, asyncio.Task] = {}  # 心跳任务
        self.last_heartbeat_time: dict[str, float] = {}  # 最近活跃时间/心跳时间
        self._stop_lock = asyncio.Lock()
        self._stopping = False
        self._offline_notify_callback: (
            Callable[[dict[str, Any]], Awaitable[None]] | None
        ) = None
        self._reconnect_service = WebSocketReconnectService(self)
        self._runtime_service = WebSocketRuntimeService(self)
        self._dispatch_service = WebSocketDispatchService(self)

    def register_handler(self, connection_name: str, handler: Callable):
        """注册消息处理器"""
        self.message_handlers[connection_name] = handler
        logger.debug(f"[灾害预警] 注册处理器: {connection_name}")

    async def connect(
        self,
        name: str,
        uri: str,
        headers: dict | None = None,
        is_retry: bool = False,
        connection_info: dict[str, Any] | None = None,
    ):
        """建立WebSocket连接 - aiohttp版本

        Args:
            name: 连接名称
            uri: WebSocket URI
            headers: 可选的HTTP头
            is_retry: 是否为重试连接
            connection_info: 可选的连接元数据（如 backup_url 等）
        """
        # 确保 session 存在；多个连接共享同一个 aiohttp session，便于统一管理超时与资源回收。
        if not self.session or self.session.closed:
            logger.warning(f"[灾害预警] WebSocket会话未就绪，正在重新初始化: {name}")
            if self.session and not self.session.closed:
                try:
                    await self.session.close()
                except Exception:
                    pass
            # 复用 http_timeout 配置，避免 ws/http 两套连接超时策略完全脱节。
            timeout_val = self.config.get("http_timeout", 30)
            timeout = aiohttp.ClientTimeout(total=timeout_val)
            self.session = aiohttp.ClientSession(timeout=timeout)

        try:
            # 记录连接信息。
            # 这份元数据既用于 get_connection_status()，也用于后续断线重连和离线通知。
            self.connection_info[name] = {
                "uri": uri,
                "headers": headers,
                "connection_type": "websocket",
                "established_time": None,
                "retry_count": 0,
                **(connection_info or {}),
            }

            # 如果是重试连接，记录重试次数
            if is_retry:
                current_retry = self.connection_retry_counts.get(name, 0) + 1
                self.connection_retry_counts[name] = current_retry
            else:
                logger.debug(f"[灾害预警] 正在连接 {name}")
                # 首次连接时重置重试计数
                self.connection_retry_counts[name] = 0

            # aiohttp ws_connect 配置
            conn_timeout = self.config.get("connection_timeout", 30)
            connect_kwargs = {
                "url": uri,
                "headers": headers or {},
                "heartbeat": self.config.get("heartbeat_interval", 60),
                "timeout": conn_timeout,  # aiohttp 内部握手超时
                "max_msg_size": self.config.get("max_message_size", 2**20),  # 1MB默认
            }

            # 添加SSL配置（如果需要）
            if self.config.get("ssl_verify", True) is False:
                connect_kwargs["ssl"] = False

            # 显式使用 wait_for 包裹连接过程，确保不被卡死
            # 注意：ws_connect 返回的是一个 ClientWebSocketResponse，它是一个异步上下文管理器
            # 但 wait_for 返回的是 ws_connect 的结果（即 ClientWebSocketResponse 对象）
            # 所以我们需要先获取 websocket 对象，然后再使用 async with 管理它
            websocket = await asyncio.wait_for(
                self.session.ws_connect(**connect_kwargs),
                timeout=conn_timeout + 5,  # 略大于内部超时
            )

            async with websocket:
                self.connections[name] = websocket
                self.connection_info[name]["established_time"] = (
                    asyncio.get_running_loop().time()
                )
                logger.info(f"[灾害预警] WebSocket连接成功: {name}")
                # 连接成功，重置所有重试计数
                self.connection_retry_counts[name] = 0
                self.fallback_retry_counts[name] = 0
                self.last_heartbeat_time[name] = asyncio.get_running_loop().time()

                # 启动心跳任务
                self.heartbeat_tasks[name] = asyncio.create_task(
                    self._heartbeat_loop(name, websocket)
                )

                await self._dispatch_service.handle_connection_session(
                    name=name,
                    uri=uri,
                    headers=headers,
                    websocket=websocket,
                )

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            # 网络层面的错误
            logger.warning(f"[灾害预警] 连接中断或失败 {name}: {e}")
            self._handle_connection_error(name, uri, headers, e)

        except asyncio.CancelledError:
            # 任务被取消（通常在 stop() 时），不触发重连
            logger.info(f"[灾害预警] WebSocket连接任务被取消: {name}")
            self.connections.pop(name, None)
            self.connection_info.pop(name, None)
            raise  # 正常传播取消信号
        except Exception as e:
            logger.error(f"[灾害预警] 未知连接错误 {name}: {type(e).__name__} - {e}")
            logger.debug(f"[灾害预警] 异常堆栈: {traceback.format_exc()}")
            # 上报未知 WebSocket 错误到遥测
            if self._telemetry and self._telemetry.enabled:
                asyncio.create_task(
                    self._telemetry.track_error(
                        e, module=f"core.websocket_manager.connect.{name}"
                    )
                )
            self._handle_connection_error(name, uri, headers, e)

    def _log_message(self, name: str, message: Any, uri: str):
        """记录消息辅助方法"""
        self._dispatch_service.log_message(name, message, uri)

    def _handle_connection_error(
        self, name: str, uri: str, headers: dict | None, error: Exception
    ):
        """统一处理连接错误"""
        self._reconnect_service.handle_connection_error(name, uri, headers, error)

    def _is_critical_error(self, error: Exception) -> bool:
        """判断是否为关键错误（需要直接进入兜底重连）"""
        return self._reconnect_service.is_critical_error(error)

    def _get_handler_name_for_connection(self, connection_name: str) -> str:
        """获取连接对应的处理器名称"""
        return self._dispatch_service.get_handler_name_for_connection(connection_name)

    async def _schedule_reconnect(
        self,
        name: str,
        uri: str,
        headers: dict | None = None,
        connection_info: dict[str, Any] | None = None,
        force_fallback: bool = False,
    ):
        """计划重连 - 优化版本，基于配置的固定间隔"""
        await self._reconnect_service.schedule_reconnect(
            name,
            uri,
            headers,
            connection_info,
            force_fallback=force_fallback,
        )

    async def _heartbeat_loop(self, name: str, websocket: ClientWebSocketResponse):
        """应用层心跳循环"""
        await self._runtime_service.heartbeat_loop(name, websocket)

    async def force_reconnect(self, name: str) -> bool:
        """强制立即重连指定连接（跳过等待）

        Returns:
            bool: 是否触发了重连操作
        """
        # 1. 如果当前已连接且正常，跳过
        if name in self.connections and not self.connections[name].closed:
            return False

        # 2. 如果有正在等待的重连任务，取消它
        if name in self.reconnect_tasks:
            task = self.reconnect_tasks[name]
            if not task.done():
                task.cancel()
                logger.debug(f"[灾害预警] 取消了 {name} 正在等待的重连任务 (强制重连)")
            self.reconnect_tasks.pop(name, None)

        # 3. 获取连接信息
        info = self.connection_info.get(name)
        if not info:
            logger.warning(f"[灾害预警] 无法重连 {name}: 找不到连接信息")
            return False

        uri = info.get("uri")
        headers = info.get("headers")

        # 4. 重置重试计数，确保作为一次新的尝试
        self.connection_retry_counts[name] = 0
        self.fallback_retry_counts[name] = 0

        logger.info(f"[灾害预警] 正在手动重连 {name}...")

        # 5. 立即发起连接
        # 使用 create_task 避免阻塞当前调用者
        asyncio.create_task(
            self.connect(
                name,
                uri,
                headers,
                is_retry=False,  # 视为新连接，重置状态
                connection_info=info,
            )
        )
        return True

    async def disconnect(self, name: str):
        """断开连接"""
        await self._runtime_service.disconnect(name)

    async def send_message(self, name: str, message: str):
        """发送消息"""
        if name in self.connections:
            try:
                await self.connections[name].send_str(message)
                logger.debug(f"[灾害预警] 消息已发送到 {name}: {message[:100]}...")
            except Exception as e:
                logger.error(f"[灾害预警] WebSocket管理器发送消息失败 {name}: {e}")
        else:
            logger.warning(f"[灾害预警] WebSocket管理器尝试发送到未连接的连接: {name}")

    def get_connection_status(self, name: str) -> dict[str, Any]:
        """获取连接状态信息"""
        status = {
            "connected": name in self.connections and not self.connections[name].closed,
            "retry_count": self.connection_retry_counts.get(name, 0),
            "has_handler": name in self.message_handlers,
        }

        if name in self.connection_info:
            info = self.connection_info[name]
            status.update(
                {
                    "uri": info.get("uri"),
                    "established_time": info.get("established_time"),
                    "connection_type": info.get("connection_type"),
                }
            )

        # 添加最后心跳时间
        if name in self.last_heartbeat_time:
            status["last_active"] = self.last_heartbeat_time[name]

        return status

    def get_all_connections_status(self) -> dict[str, dict[str, Any]]:
        """获取所有连接的状态信息"""
        return {
            name: self.get_connection_status(name)
            for name in self.connection_info.keys()
        }

    async def start(self):
        """启动管理器"""
        await self._runtime_service.start()

    async def _cancel_and_wait(self, tasks: list[asyncio.Task]) -> None:
        """取消并等待任务结束。"""
        await self._runtime_service.cancel_and_wait(tasks)

    async def stop(self):
        """停止管理器"""
        await self._runtime_service.stop()

    def set_offline_notify_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]] | None
    ) -> None:
        """设置离线通知回调"""
        self._offline_notify_callback = callback

    def _emit_offline_notification(
        self,
        connection_name: str,
        stage: str,
        reason: str,
        next_retry_in: str | None = None,
        retry_count: int | None = None,
        fallback_count: int | None = None,
    ) -> None:
        """触发离线通知回调（异步安全）"""
        self._reconnect_service.emit_offline_notification(
            connection_name=connection_name,
            stage=stage,
            reason=reason,
            next_retry_in=next_retry_in,
            retry_count=retry_count,
            fallback_count=fallback_count,
        )

    def _find_handler_by_prefix(self, connection_name: str) -> str | None:
        """通过前缀匹配查找处理器名称"""
        return self._dispatch_service.find_handler_by_prefix(connection_name)


class HTTPDataFetcher:
    """HTTP数据获取器"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        # 采用 async with 语义按次创建短生命周期 session，
        # 适合定时拉取类任务，避免长期闲置连接占用资源。
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.get("http_timeout", 30))
        )
        return self

    async def __aexit__(self, exc_type=None, exc_val=None, exc_tb=None):
        await self.close()  # 调用显式的 close

    async def close(self):
        """显式关闭 Session"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_json(self, url: str, headers: dict | None = None) -> dict | None:
        """获取JSON数据"""
        if not self.session:
            return None

        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning(f"[灾害预警] HTTP请求失败 {url}: {response.status}")
        except Exception as e:
            logger.error(f"[灾害预警] HTTP请求异常 {url}: {e}")

        return None
