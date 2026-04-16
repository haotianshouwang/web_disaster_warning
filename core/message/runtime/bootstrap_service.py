"""
消息管理器初始化装配服务。
负责根据插件配置创建 MessagePushManager 运行所需的过滤器、浏览器、构建器与基础依赖，
减少 MessagePushManager.__init__() 中的大段对象装配代码。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from ...support.config_accessor import ConfigAccessor
from ...support.event_deduplicator import EventDeduplicator
from ..builders.card_message_builder import CardMessageBuilder
from ..builders.global_quake_card_builder import GlobalQuakeCardBuilder
from ..builders.map_attachment_builder import MapAttachmentBuilder
from ..builders.text_message_builder import TextMessageBuilder
from ..render.remote_media_fetcher import RemoteMediaFetcher
from .browser_manager import BrowserManager


class MessageManagerBootstrapService:
    """消息管理器初始化装配服务。"""

    def __init__(self, manager):
        self.manager = manager
        self.config_accessor = ConfigAccessor(manager.config)
        self.map_attachment_builder = None
        self.text_message_builder = None
        self.card_message_builder = None
        self.global_quake_card_builder = None
        self.remote_media_fetcher = None

    def setup_filters(self, config: dict[str, Any]) -> None:
        """初始化全局过滤器与去重器。"""
        event_deduplication_config = self.config_accessor.event_deduplication_config()
        shared_components = (
            self.manager._runtime_component_factory.build_shared_components(
                config,
                emit_weather_enable_log=True,
            )
        )

        # 这一组共享组件与会话无关，保留在 manager 上供兼容路径与默认全局判定直接复用。
        self.manager.keyword_filter = shared_components["keyword_filter"]
        self.manager.intensity_filter = shared_components["intensity_filter"]
        self.manager.scale_filter = shared_components["scale_filter"]
        self.manager.usgs_filter = shared_components["usgs_filter"]
        self.manager.global_quake_filter = shared_components["global_quake_filter"]
        self.manager.local_monitor = shared_components["local_monitor"]
        self.manager.weather_filter = shared_components["weather_filter"]
        self.manager.report_controller = (
            self.manager._runtime_component_factory.build_report_controller(config)
        )

        # 去重器属于全局入口级保护，尽量在进入更昂贵的渲染/发送逻辑前完成拦截。
        self.manager.deduplicator = EventDeduplicator(
            time_window_minutes=event_deduplication_config.get(
                "time_window_minutes", 1
            ),
            location_tolerance_km=event_deduplication_config.get(
                "location_tolerance_km", 20.0
            ),
            magnitude_tolerance=event_deduplication_config.get(
                "magnitude_tolerance", 0.5
            ),
        )

    def setup_browser(self, config: dict[str, Any], telemetry=None) -> None:
        """初始化浏览器管理器并按需预热。"""
        msg_config = self.config_accessor.message_format_config()
        raw_pool_size = msg_config.get("browser_pool_size", 2)
        try:
            pool_size = int(raw_pool_size)
        except (TypeError, ValueError):
            pool_size = 2
        else:
            if pool_size < 1:
                pool_size = 2

        playwright_mode = msg_config.get("playwright_mode", "local")
        playwright_server_url = msg_config.get("playwright_server_url", "")
        self.manager.browser_manager = BrowserManager(
            pool_size=pool_size,
            telemetry=telemetry,
            mode=playwright_mode,
            server_url=playwright_server_url,
        )

        # 仅当本地模式且确实需要渲染地图/卡片时才后台预热，平衡启动速度与首渲染时延。
        if playwright_mode == "local" and (
            msg_config.get("include_map", False)
            or msg_config.get("use_global_quake_card", False)
        ):
            logger.debug("[灾害预警] 检测到已启用卡片渲染功能，正在后台预热浏览器...")
            asyncio.create_task(self.manager.browser_manager.initialize())

    def setup_message_components(self) -> None:
        """初始化消息构建与远程媒体依赖。"""
        # 这些组件都依赖前面已准备好的 plugin_root / temp_dir / browser_manager 等运行时基础设施。
        self.map_attachment_builder = MapAttachmentBuilder(
            plugin_root=self.manager.plugin_root,
            temp_dir=str(self.manager.temp_dir),
            browser_manager=self.manager.browser_manager,
            default_config=self.manager.config,
        )
        self.text_message_builder = TextMessageBuilder(
            default_config=self.manager.config
        )
        self.card_message_builder = CardMessageBuilder(
            plugin_root=self.manager.plugin_root,
            temp_dir=str(self.manager.temp_dir),
            browser_manager=self.manager.browser_manager,
        )
        self.global_quake_card_builder = GlobalQuakeCardBuilder(
            plugin_root=self.manager.plugin_root,
            temp_dir=str(self.manager.temp_dir),
            browser_manager=self.manager.browser_manager,
        )
        self.remote_media_fetcher = RemoteMediaFetcher(
            # fetcher 本身不感知 aiohttp 细节，通过回调注入 session 与 MIME 判定能力。
            session_getter=self.manager.get_remote_media_session,
            image_type_checker=self.manager._remote_media_service.is_image_content_type,
            content_type_guesser=self.manager._remote_media_service.guess_image_content_type,
        )
