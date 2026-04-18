"""
灾害预警核心服务
整合所有重构的组件
"""

import asyncio
import os
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from astrbot.api import logger
from astrbot.api.star import StarTools

if TYPE_CHECKING:
    from ..support.telemetry_manager import TelemetryManager

from ...models.data_source_config import (
    DATA_SOURCE_CONFIGS,
    is_source_enabled_in_data_sources,
)
from ...models.models import (
    DATA_SOURCE_MAPPING,
    DisasterEvent,
)
from ...utils.fe_regions import load_data_async
from ...utils.formatters import MESSAGE_FORMATTERS
from ..handlers import DATA_HANDLERS
from ..message.message_logger import MessageLogger
from ..message.message_manager import MessagePushManager
from ..network.handler_registry import WebSocketHandlerRegistry
from ..network.websocket.websocket_manager import HTTPDataFetcher, WebSocketManager
from ..storage.session_config_manager import SessionConfigManager
from ..storage.statistics_manager import StatisticsManager
from .pipeline.event_pipeline import EventPipeline
from .runtime.disaster_service_cache import DisasterServiceCacheService
from .runtime.disaster_service_lifecycle import DisasterServiceLifecycleService
from .runtime.disaster_service_notice import DisasterServiceNoticeService
from .runtime.disaster_service_reconnect import DisasterServiceReconnectService
from .runtime.disaster_service_runtime import DisasterServiceRuntimeService
from .runtime.disaster_service_status import DisasterServiceStatusService
from .runtime.disaster_service_support import DisasterServiceSupportService
from .services.connection_plan_builder import ConnectionPlanBuilder
from .services.earthquake_list_service import EarthquakeListService
from .services.eew_query_state_service import EEWQueryStateService


class DisasterWarningService:
    """灾害预警核心服务"""

    EEW_VALID_DURATION_SECONDS = 300

    _EEW_QUERY_INSTITUTIONS: dict[str, dict[str, Any]] = {
        "china": {
            "display_name": "中国地震预警网 EEW",
            "active_name": "中国地震预警网",
            "source_ids": ["cea_fanstudio", "cea_pr_fanstudio", "cea_wolfx"],
        },
        "japan": {
            "display_name": "日本気象庁 EEW",
            "active_name": "日本気象庁",
            "source_ids": ["jma_fanstudio", "jma_p2p", "jma_wolfx"],
        },
        "taiwan": {
            "display_name": "中央氣象署 EEW",
            "active_name": "中央氣象署",
            "source_ids": ["cwa_fanstudio", "cwa_wolfx"],
        },
    }

    def __init__(self, config: dict[str, Any], context):
        # 主服务保留 facade 身份：持有全局依赖、共享状态与少量兼容入口，
        # 具体生命周期、运行时调度、通知与缓存逻辑则委托到拆分服务。
        self.config = config
        self.context = context
        self.running = False
        self._start_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._stopping = False

        self.message_logger = MessageLogger(config, "disaster_warning")
        self.statistics_manager = StatisticsManager(config)
        self._telemetry: TelemetryManager | None = None
        self.session_config_manager = SessionConfigManager(config)

        # WebSocketManager / MessagePushManager 属于核心基础设施，需在初始化阶段提前装配。
        self.ws_manager = WebSocketManager(
            config.get("websocket_config", {}),
            self.message_logger,
            telemetry=self._telemetry,
        )
        self.http_fetcher: HTTPDataFetcher | None = None
        self.message_manager = MessagePushManager(
            config, context, telemetry=self._telemetry
        )
        # 用于离线通知节流，避免同一连接异常在短时间内反复刷屏。
        self._offline_notification_state: dict[str, dict[str, float]] = {}

        self.handlers = {}
        self._initialize_handlers()

        # 这些运行时容器会被 lifecycle / runtime 服务共同维护。
        self.connections = {}
        self.connection_tasks = []
        self.scheduled_tasks = []
        self.background_tasks: set[asyncio.Task] = set()
        self.web_admin_server = None

        self.earthquake_lists = {"cenc": {}, "jma": {}}
        self.earthquake_list_service = EarthquakeListService(self.earthquake_lists)

        self.storage_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        self.cache_file = os.path.join(self.storage_dir, "earthquake_lists_cache.json")
        self.eew_query_cache_file = os.path.join(
            self.storage_dir, "eew_query_cache.json"
        )
        self.eew_query_state: dict[str, dict[str, Any]] = {}
        self.eew_query_service = EEWQueryStateService(
            institutions=self._EEW_QUERY_INSTITUTIONS,
            valid_duration_seconds=self.EEW_VALID_DURATION_SECONDS,
            source_enabled_checker=is_source_enabled_in_data_sources,
        )
        # 以下服务分别承接事件流水线、生命周期、运行时调度、缓存、状态投影、通知与重连编排。
        self.event_pipeline = EventPipeline(self)
        self.lifecycle_service = DisasterServiceLifecycleService(self)
        self.runtime_service = DisasterServiceRuntimeService(self)
        self.cache_service = DisasterServiceCacheService(self)
        self.status_service = DisasterServiceStatusService(self)
        self.notice_service = DisasterServiceNoticeService(self)
        self.reconnect_service = DisasterServiceReconnectService(self)
        self.support_service = DisasterServiceSupportService(self)

    def _initialize_handlers(self):
        """初始化数据处理器"""
        for source_id, handler_class in DATA_HANDLERS.items():
            self.handlers[source_id] = handler_class(self.message_logger)

    def _check_registry_integrity(self):
        """检查各注册表的一致性"""
        handler_ids = set(DATA_HANDLERS.keys())
        formatter_ids = set(MESSAGE_FORMATTERS.keys())
        config_ids = set(DATA_SOURCE_CONFIGS.keys())
        mapping_ids = set(DATA_SOURCE_MAPPING.keys())

        missing_formatters = handler_ids - formatter_ids
        if missing_formatters:
            logger.warning(
                f"[灾害预警] 以下数据源缺少格式化器注册: {missing_formatters}"
            )

        missing_configs = handler_ids - config_ids
        if missing_configs:
            logger.warning(f"[灾害预警] 以下数据源缺少配置定义: {missing_configs}")

        missing_mappings = handler_ids - mapping_ids
        if missing_mappings:
            logger.warning(
                f"[灾害预警] 以下数据源缺少 ID-枚举 映射: {missing_mappings}"
            )

        if not missing_formatters and not missing_configs and not missing_mappings:
            logger.debug("[灾害预警] 注册表完整性自检通过")

    def set_telemetry(self, telemetry: Optional["TelemetryManager"]):
        """设置遥测管理器引用"""
        self._telemetry = telemetry
        if self.ws_manager:
            self.ws_manager._telemetry = telemetry
        if self.message_manager:
            self.message_manager._telemetry = telemetry
            if self.message_manager.browser_manager:
                self.message_manager.browser_manager._telemetry = telemetry

    async def initialize(self):
        """初始化服务"""
        try:
            logger.info("[灾害预警] 正在初始化灾害预警服务...")
            # 初始化阶段只做“静态装配”：校验注册表、加载基础数据、注册处理器、生成连接计划。
            self._check_registry_integrity()
            await load_data_async()
            self.http_fetcher = HTTPDataFetcher(self.config)
            self._register_handlers()
            self._configure_connections()
            logger.info("[灾害预警] 灾害预警服务初始化完成")

        except Exception as e:
            logger.error(f"[灾害预警] 初始化服务失败: {e}")
            if self._telemetry and self._telemetry.enabled:
                await self._telemetry.track_error(
                    e, module="core.disaster_service.initialize"
                )
            raise

    def _register_handlers(self):
        """注册消息处理器"""
        registry = WebSocketHandlerRegistry(self)
        registry.register_all(self.ws_manager)
        self.ws_manager.set_offline_notify_callback(self._handle_offline_notification)

    def _configure_connections(self):
        """配置连接 - 适配数据源配置"""
        self.connections = ConnectionPlanBuilder.build(self.config)

    async def start(self):
        """启动服务"""
        await self.lifecycle_service.start()

    async def _cancel_and_wait(self, tasks: list[asyncio.Task]) -> None:
        """取消并等待任务结束。"""
        await self.lifecycle_service.cancel_and_wait(tasks)

    def register_background_task(self, task: asyncio.Task) -> None:
        """注册服务级后台任务，确保停机时可统一回收。"""
        if task is None:
            return
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def stop(self):
        """停止服务"""
        await self.lifecycle_service.stop()

    async def _establish_websocket_connections(self):
        """建立WebSocket连接 - 使用WebSocket管理器功能"""
        await self.runtime_service.establish_websocket_connections()

    def get_data_source_from_connection(self, connection_name: str) -> str:
        """从连接名称获取数据源 ID。"""
        return self.support_service.get_data_source_from_connection(connection_name)

    def is_fan_studio_source_enabled(self, source_key: str) -> bool:
        """检查特定的 FAN Studio 数据源是否启用。"""
        return self.support_service.is_fan_studio_source_enabled(source_key)

    def is_wolfx_source_enabled(self, source_key: str) -> bool:
        """检查特定的 Wolfx 数据源是否启用。"""
        return self.support_service.is_wolfx_source_enabled(source_key)

    async def _start_scheduled_http_fetch(self):
        """启动定时HTTP数据获取"""
        await self.runtime_service.start_scheduled_http_fetch()

    async def _start_cleanup_task(self):
        """启动清理任务"""
        await self.runtime_service.start_cleanup_task()

    def update_earthquake_list(self, list_type: str, data: dict[str, Any]):
        """更新内存中的地震列表"""
        self.earthquake_list_service.update_earthquake_list(list_type, data)

    def _load_earthquake_lists_cache(self):
        """从文件加载地震列表缓存"""
        self.cache_service.load_earthquake_lists_cache()

    def _save_earthquake_lists_cache(self):
        """保存地震列表缓存到文件"""
        self.cache_service.save_earthquake_lists_cache()

    def get_formatted_list_data(self, source_type: str, count: int) -> list[dict]:
        """获取格式化后的地震列表数据（用于卡片渲染）。兼容保留在主服务层，实际委托列表服务。"""
        return self.earthquake_list_service.get_formatted_list_data(source_type, count)

    def is_in_silence_period(self) -> bool:
        """检查是否处于启动后的静默期"""
        if not hasattr(self, "start_time"):
            return False

        debug_config = self.config.get("debug_config", {})
        silence_duration = debug_config.get("startup_silence_duration", 0)

        if silence_duration <= 0:
            return False

        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        return elapsed < silence_duration

    async def _handle_disaster_event(self, event: DisasterEvent):
        """处理灾害事件"""
        try:
            # EEW 查询状态更新属于轻量级旁路状态维护，即使失败也不阻断主流程。
            self._update_eew_query_state(event)
        except Exception as e:
            logger.debug(f"[灾害预警] 更新 EEW 查询状态失败（已忽略）: {e}")

        if self.is_in_silence_period():
            debug_config = self.config.get("debug_config", {})
            silence_duration = debug_config.get("startup_silence_duration", 0)
            elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
            logger.debug(
                f"[灾害预警] 处于启动静默期 (剩余 {silence_duration - elapsed:.1f}s)，忽略事件: {event.id}"
            )
            return

        try:
            # 真正的日志记录、推送、统计与 Web 管理端通知由事件流水线统一处理。
            await self.event_pipeline.handle(event)

        except Exception as e:
            logger.error(f"[灾害预警] 处理灾害事件失败: {e}")
            logger.error(
                f"[灾害预警] 失败的事件ID: {event.id if hasattr(event, 'id') else 'unknown'}"
            )
            logger.error(f"[灾害预警] 异常堆栈: {traceback.format_exc()}")
            if self._telemetry and self._telemetry.enabled:
                asyncio.create_task(
                    self._telemetry.track_error(
                        exception=e,
                        module="disaster_service._handle_disaster_event",
                    )
                )

    def log_event(self, event: DisasterEvent) -> None:
        """记录事件日志。"""
        self.support_service.log_event(event)

    async def _handle_offline_notification(self, payload: dict[str, Any]) -> None:
        """处理 WebSocket 管理器离线通知回调"""
        await self.notice_service.handle_offline_notification(payload)

    async def notify_data_source_offline(
        self,
        connection_name: str,
        data_source: str,
        stage: str,
        reason: str,
        next_retry_in: str | None = None,
        retry_count: int | None = None,
        fallback_count: int | None = None,
    ) -> bool:
        """推送数据源离线通知（兜底重试/停止重连）"""
        return await self.notice_service.notify_data_source_offline(
            connection_name=connection_name,
            data_source=data_source,
            stage=stage,
            reason=reason,
            next_retry_in=next_retry_in,
            retry_count=retry_count,
            fallback_count=fallback_count,
        )

    async def reconnect_all_sources(self) -> dict[str, str]:
        """
        强制重连所有已启用但离线的数据源
        返回: dict {connection_name: status_message}
        """
        return await self.reconnect_service.reconnect_all_sources()

    def get_service_status(self) -> dict[str, Any]:
        """获取服务状态 - 增强版本"""
        return self.status_service.get_service_status()

    def _update_eew_query_state(self, event: DisasterEvent) -> None:
        """更新地震预警查询状态（机构级，跨源去重）。兼容保留在主服务层，实际委托查询状态服务。"""
        self.eew_query_state = self.eew_query_service.update_state(
            self.eew_query_state,
            event,
        )

    def get_eew_query_status_data(self) -> dict[str, Any]:
        """获取地震预警查询的结构化状态数据（供 Web 与指令复用）。兼容保留在主服务层，实际委托查询状态服务。"""
        return self.eew_query_service.build_status_data(
            self.eew_query_state,
            self.config.get("data_sources", {}),
        )

    def get_eew_query_text(self) -> str:
        """生成 /地震预警查询 文本。兼容保留在主服务层，实际委托通知服务。"""
        return self.notice_service.get_eew_query_text()

    def _load_eew_query_cache(self):
        """从文件加载 EEW 查询状态缓存。"""
        self.cache_service.load_eew_query_cache()

    def _save_eew_query_cache(self):
        """保存 EEW 查询状态缓存到文件。"""
        self.cache_service.save_eew_query_cache()


_disaster_service: DisasterWarningService | None = None


async def get_disaster_service(
    config: dict[str, Any], context
) -> DisasterWarningService:
    """获取灾害预警服务实例"""
    global _disaster_service

    if _disaster_service is None:
        _disaster_service = DisasterWarningService(config, context)
        await _disaster_service.initialize()

    return _disaster_service


async def stop_disaster_service():
    """停止灾害预警服务"""
    global _disaster_service

    if _disaster_service:
        await _disaster_service.stop()
        _disaster_service = None
