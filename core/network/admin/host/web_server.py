"""
灾害预警插件 Web 管理服务器。
负责创建管理端宿主应用，并装配路由、实时广播、健康探测与静态页面入口。
"""

import asyncio
import secrets
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .....utils.geolocation import close_geoip_session
from ....services.config.config_service import ConfigAccessor
from ...dashboard.dashboard_connector import DashboardConnector
from ...onebot.manager import OneBotManager
from ...onebot.protocols import OneBotConfig
from ...monitoring.source_health_monitor import SourceHealthMonitor
from ...websocket.websocket_hub import WebSocketHub
from ..payloads.api_response import ApiResponse
from ..payloads.config_payload_builder import ConfigPayloadBuilder
from ..payloads.connections_payload_builder import ConnectionsPayloadBuilder
from ..payloads.realtime_payload_builder import RealtimePayloadBuilder
from .web_server_runtime_service import WebServerRuntimeService

# 动态探测 FastAPI 与 Uvicorn 环境
try:
    import uvicorn
    from fastapi import FastAPI, Request, WebSocket
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    logger.warning(
        "[灾害预警] FastAPI 未安装，Web 管理端功能不可用。请运行: pip install fastapi uvicorn"
    )


class WebAdminServer:
    """Web 管理端服务器。"""

    def __init__(self, disaster_service, config: dict[str, Any]):
        """初始化管理端宿主，并装配运行时依赖。"""
        self.disaster_service = disaster_service
        self.config = config
        self.config_accessor = ConfigAccessor(config)
        self.app = None
        self.server = None
        self._server_task = None
        self._broadcast_task = None
        self._ping_task = None
        self._ws_hub = WebSocketHub()

        # 仪表盘连接器（独立于管理端 WebSocket）
        self.dashboard_connector = DashboardConnector(disaster_service)

        # 延迟缓存容器，用于在健康监控与连接面板展示之间共享探测数值
        self._latency_cache: dict[str, float | None] = {}
        self.dashboard_connector.set_latency_cache(self._latency_cache)
        self._source_health_monitor = SourceHealthMonitor(self._latency_cache)

        # 注入各不同职责的 Payload 生成器
        self._connections_payload_builder = ConnectionsPayloadBuilder(
            disaster_service=self.disaster_service,
            config=self.config,
            latency_cache=self._latency_cache,
        )
        self._config_payload_builder = ConfigPayloadBuilder(self.config)
        self._realtime_payload_builder = RealtimePayloadBuilder(
            disaster_service=self.disaster_service,
            config=self.config,
            latency_cache=self._latency_cache,
        )
        self._auth_enabled = False
        self._auth_token: str | None = None

        # OneBot 11 管理器（按 notification_channels 配置启动）
        self._onebot_manager: OneBotManager | None = None

        # 注入后台运行时调度管理服务
        self._runtime_service = WebServerRuntimeService(self)

        if not FASTAPI_AVAILABLE:
            return

        self._setup_app()

    def _setup_app(self):
        """配置 FastAPI 应用。"""
        self.app = FastAPI(
            title="灾害预警管理端",
            description="灾害预警插件 Web 管理界面",
            version="1.0.0",
        )

        # 鉴权配置先于路由注册完成，确保中间件与 WebSocket 端点复用同一套运行时状态。
        self._runtime_service.configure_auth()

        @self.app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            """拦截管理端 API 请求并执行令牌鉴权。"""
            # 若未设置鉴权密码，直接放行
            if not self._auth_enabled:
                return await call_next(request)

            # 放行非 API 接口及登录鉴权专有端点
            path = request.url.path
            if path in {"/api/login", "/api/auth-info"}:
                return await call_next(request)
            if not path.startswith("/api"):
                return await call_next(request)

            # 从 HTTP query params 或 Authorization Bearer 头部提取 Token 字段
            token = request.query_params.get("token", "")
            if not token:
                auth_header = request.headers.get("Authorization", "")
                auth_parts = auth_header.split(" ", 1)
                token = (
                    auth_parts[1].strip()
                    if len(auth_parts) == 2 and auth_parts[0].lower() == "bearer"
                    else ""
                )

            # 时序安全防爆破校验
            if not self._auth_token or not secrets.compare_digest(
                token, self._auth_token
            ):
                return ApiResponse.error("未授权，请先登录", status_code=401)

            return await call_next(request)

        # 跨域访问（生产部署应限制为具体前端域名）
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self._register_routes()

        # 装载管理端静态网页资源目录
        admin_dir = Path(__file__).resolve().parents[4] / "admin"
        if admin_dir.exists():
            self.app.mount(
                "/", StaticFiles(directory=admin_dir, html=True), name="admin"
            )

        # 注入 OneBot 热重启回调，供 notification_channel_routes 调用
        self.app.state.restart_onebot = self._start_onebot_manager
        # 注入 OneBot 管理器引用，供测试接口使用
        self.app.state.onebot_manager_ref = lambda: self._onebot_manager

    def _register_routes(self):
        """注册 API 路由。"""
        # 装载全部 HTTP 端点路由定义
        self._runtime_service.register_routes()

        # 仪表盘连接器配置
        dash_cfg = self.config.get("dashboard", {})
        if isinstance(dash_cfg, dict):
            self.dashboard_connector.configure(
                enabled=dash_cfg.get("enabled", True),
                key=dash_cfg.get("key", ""),
            )

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """管理端 WebSocket 实时推送端点。"""
            await self._runtime_service.handle_websocket(websocket)

        @self.app.websocket("/dashboard/ws")
        async def dashboard_ws_endpoint(websocket: WebSocket):
            """仪表盘 WebSocket 端点。使用独立鉴权密钥。"""
            connector = self.dashboard_connector
            if not connector.enabled:
                await websocket.close(code=1008, reason="仪表盘已禁用")
                return

            # 鉴权：从 query param 或 header 获取 key
            key = websocket.query_params.get("key", "")
            if not key:
                auth_header = websocket.headers.get("authorization", "")
                if auth_header.lower().startswith("bearer "):
                    key = auth_header[7:].strip()

            expected = connector.auth_key
            if expected and not secrets.compare_digest(key, expected):
                await websocket.close(code=1008, reason="仪表盘密钥错误")
                return

            await websocket.accept()
            connector.add(websocket)
            logger.info(f"[仪表盘] 客户端已连接 (共 {connector.count()} 个)")

            try:
                # 发送全量快照
                await connector.send_full_update(websocket)

                # 消息循环
                while True:
                    try:
                        data = await websocket.receive_json()
                    except Exception:
                        break
                    t = data.get("type", "") if isinstance(data, dict) else ""
                    if t == "ping":
                        await websocket.send_json({"type": "pong"})
                    elif t == "refresh":
                        await connector.send_full_update(websocket)
                    elif t == "query_events":
                        await connector.send_event(
                            websocket,
                            page=data.get("page", 1),
                            limit=data.get("limit", 50),
                            event_type=data.get("event_type", ""),
                            sources=data.get("sources", ""),
                            min_magnitude=data.get("min_magnitude", ""),
                            magnitude_order=data.get("magnitude_order", ""),
                            keyword=data.get("keyword", ""),
                            level_filter=data.get("level_filter", ""),
                            qid=data.get("qid", ""),
                        )
            except Exception:
                pass
            finally:
                connector.remove(websocket)
                logger.debug(f"[仪表盘] 客户端已断开 (剩余 {connector.count()} 个)")

    async def _send_full_update(self, websocket: WebSocket):
        """向单个客户端发送完整数据更新。"""
        await self._runtime_service.send_full_update(websocket)

    async def _broadcast_data(self):
        """向所有已连接客户端广播数据更新。"""
        await self._runtime_service.broadcast_data()

    async def get_realtime_data(self) -> dict:
        """获取 WebSocket 推送所需的实时数据。"""
        return await self._runtime_service.get_realtime_data()

    def get_expected_data_sources(self) -> dict[str, str]:
        """获取所有支持的数据源列表，不区分当前是否启用。"""
        return self._source_health_monitor.get_expected_data_sources()

    async def _broadcast_loop(self):
        """后台广播循环，作为保底同步机制定期推送快照。"""
        await self._runtime_service.run_broadcast_loop(interval_seconds=30)

    async def notify_event(self, event_data: dict = None):
        """当有新灾害事件时，立即向所有客户端推送事件更新。"""
        await self._runtime_service.notify_event(event_data)

    def _get_data_source_host(self, source_name: str) -> str | None:
        """获取数据源的主机名，供延迟探测使用。"""
        return self._source_health_monitor.get_data_source_host(source_name)

    async def _ping_host(
        self, host: str, port: int = 443, timeout: float = 3.0
    ) -> float | None:
        """使用 TCP 连接测试主机延迟。"""
        return await self._source_health_monitor.ping_host(
            host, port=port, timeout=timeout
        )

    async def _background_ping_loop(self):
        """后台定期更新延迟缓存。"""
        await self._source_health_monitor.run_background_ping_loop(interval_seconds=30)

    @property
    def onebot_manager(self) -> OneBotManager | None:
        """OneBot 11 管理器，供事件管道调用发送消息。"""
        return self._onebot_manager

    async def start(self):
        """启动 Web 服务器。"""
        if not FASTAPI_AVAILABLE:
            logger.error("[灾害预警] 无法启动 Web 管理端: FastAPI 未安装")
            return

        web_config = self.config_accessor.web_admin_config()
        host = web_config.get("host", "0.0.0.0")
        port = web_config.get("port", 8089)

        # 构造 Uvicorn 运行配置
        config = uvicorn.Config(
            self.app, host=host, port=port, log_level="warning", access_log=False
        )
        self.server = uvicorn.Server(config)

        logger.info(f"[灾害预警] Web 管理端已启动: http://{host}:{port}")

        # 将服务进程、心跳/延迟检测循环与广播事件的协程单独开启 Task 挂载
        self._server_task = asyncio.create_task(self.server.serve())
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        self._ping_task = asyncio.create_task(self._background_ping_loop())

        # 启动仪表盘连接器的定时广播
        await self.dashboard_connector.start_broadcast_loop()
        logger.info(
            f"[仪表盘] 连接器已启动 (key={'已设置' if self.dashboard_connector.auth_key else '无需鉴权'})"
        )

        # 启动 OneBot 11 协议适配器（按 notification_channels 配置）
        await self._start_onebot_manager()

    async def _start_onebot_manager(self) -> None:
        """读取配置并启动 OneBot 11 管理器（支持热重启）。"""
        # 先停止旧实例
        if self._onebot_manager:
            try:
                await self._onebot_manager.stop()
            except Exception:
                pass
            self._onebot_manager = None

        channels_cfg = self.config.get("notification_channels") or {}
        ob_cfg = channels_cfg.get("onebot11") if isinstance(channels_cfg, dict) else {}
        if not isinstance(ob_cfg, dict) or not ob_cfg:
            return

        def _b(v): return bool(v) if v is not None else False
        def _s(v, default=""): return str(v) if v else default
        def _p(v, default=0): return int(v) if v is not None else default

        onebot_config = OneBotConfig(
            http_server_enabled=_b(ob_cfg.get("http_server_enabled")),
            http_server_host=_s(ob_cfg.get("http_server_host"), "0.0.0.0"),
            http_server_port=_p(ob_cfg.get("http_server_port"), 5700),
            http_server_path=_s(ob_cfg.get("http_server_path"), "/onebot"),
            http_server_token=_s(ob_cfg.get("http_server_token")),
            http_client_enabled=_b(ob_cfg.get("http_client_enabled")),
            http_client_url=_s(ob_cfg.get("http_client_url"), "http://127.0.0.1:3000"),
            http_client_token=_s(ob_cfg.get("http_client_token")),
            ws_server_enabled=_b(ob_cfg.get("ws_server_enabled")),
            ws_server_host=_s(ob_cfg.get("ws_server_host"), "0.0.0.0"),
            ws_server_port=_p(ob_cfg.get("ws_server_port"), 5701),
            ws_server_token=_s(ob_cfg.get("ws_server_token")),
            ws_client_enabled=_b(ob_cfg.get("ws_client_enabled")),
            ws_client_url=_s(ob_cfg.get("ws_client_url"), "ws://127.0.0.1:3001"),
            ws_client_token=_s(ob_cfg.get("ws_client_token")),
            access_token=_s(ob_cfg.get("access_token")),
        )

        any_enabled = (
            onebot_config.http_server_enabled
            or onebot_config.http_client_enabled
            or onebot_config.ws_server_enabled
            or onebot_config.ws_client_enabled
        )
        if not any_enabled:
            return

        try:
            self._onebot_manager = OneBotManager(onebot_config)
            await self._onebot_manager.start()
            logger.info("[OneBot] 协议适配器已启动")
        except Exception as e:
            logger.warning(f"[OneBot] 启动失败: {e}")

    async def stop(self):
        """停止 Web 服务器。"""
        # -1. 停止 OneBot 管理器
        if self._onebot_manager:
            await self._onebot_manager.stop()
            logger.info("[OneBot] 协议适配器已停止")

        # 0. 停止仪表盘连接器
        await self.dashboard_connector.stop()

        # 1. 终止后台延迟 TCP ping 检测循环
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        # 2. 终止定时数据广播推送循环
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

        # 3. 强行断开并清理所有前端 websocket 句柄连接
        await self._runtime_service.close_all_websockets()

        # 4. 释放全局的 GeoIP 会话
        try:
            await close_geoip_session()
        except Exception as e:
            logger.debug(f"[灾害预警] 关闭 GeoIP 会话时出错: {e}")

        # 5. 退出 Uvicorn Web 服务器实例并等待服务 Task 彻底终止
        if self.server:
            self.server.should_exit = True
            if self._server_task:
                try:
                    await asyncio.wait_for(self._server_task, timeout=5.0)
                except asyncio.TimeoutError:
                    self._server_task.cancel()
            logger.info("[灾害预警] Web 管理端已停止")

        # 6. 关闭 Playwright 浏览器实例（防止 EPIPE）
        try:
            ds = self.disaster_service
            mgr = getattr(ds, "message_manager", None)
            bm = getattr(mgr, "browser_manager", None) if mgr else None
            if bm and hasattr(bm, "cleanup"):
                await bm.cleanup()
                logger.debug("[灾害预警] 浏览器资源已清理")
        except Exception:
            pass
