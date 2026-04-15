"""
消息推送管理器
实现优化的报数控制、拆分过滤器和改进的去重逻辑
"""

import os
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.star import StarTools

from ...models.models import (
    DisasterEvent,
    EarthquakeData,
)
from ..support.event_metadata import (
    resolve_report_num,
    resolve_source_id,
)
from .fusion.cenc_fusion_service import CENCFusionService
from .fusion.cwa_eew_fusion_service import CWAEewFusionService
from .push.message_build_service import MessageBuildService
from .push.push_execution_service import PushExecutionService
from .push.push_flow_handler import PushFlowHandler
from .push.push_orchestrator import PushOrchestrator
from .push.push_policy import should_push_event_with_components
from .push.session_sender import SessionSender
from .render.render_cache import RenderImageCache
from .runtime.bootstrap_service import MessageManagerBootstrapService
from .runtime.fusion_state_store import FusionStateStore
from .runtime.remote_media_service import MessageRemoteMediaService
from .runtime.resource_cleanup_service import MessageResourceCleanupService
from .runtime.runtime_component_factory import MessageRuntimeComponentFactory
from .runtime.support_service import MessageManagerSupportService
from .system.system_notification_service import MessageSystemNotificationService


class MessagePushManager:
    """消息推送管理器"""

    def __init__(self, config: dict[str, Any], context, telemetry=None):
        # manager 自身只保留 façade 所需的全局依赖与少量共享状态，
        # 实际初始化细节交给 bootstrap / runtime 服务完成。
        self.config = config
        self.context = context
        self._telemetry = telemetry
        # 统一的会话发送适配器，隔离 AstrBot 上下文的直接调用。
        self.session_sender = SessionSender(context)
        self.plugin_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        self.storage_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        self.temp_dir = self.storage_dir / "temp"
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir, exist_ok=True)

        # 兼容旧逻辑中对插件数据目录的访问方式。
        self.data_dir = self.plugin_root

        # 第一阶段：先创建运行时工厂与装配服务，便于后续统一初始化过滤器和浏览器。
        self._runtime_component_factory = MessageRuntimeComponentFactory()
        self._bootstrap_service = MessageManagerBootstrapService(self)
        self._bootstrap_service.setup_filters(config)
        self._bootstrap_service.setup_browser(config, telemetry=telemetry)

        # 融合等待态与渲染缓存都属于跨事件共享状态，需要在 manager 生命周期内常驻。
        self._fusion_state_store = FusionStateStore(ttl_seconds=120)
        self.last_success_sessions: list[str] = []
        self._render_cache = RenderImageCache(ttl_seconds=180)

        # 远程媒体抓取会话运行时状态。
        # 重构后虽然由 service 负责生命周期，但底层 session 句柄仍挂在 manager 上，
        # 这样可以兼容旧入口与多个服务之间的共享访问。
        self._remote_media_session = None
        self._remote_media_session_timeout_seconds = 15

        # 第二阶段：装配资源清理、远程媒体等基础设施服务。
        self._resource_cleanup_service = MessageResourceCleanupService(self)
        self._remote_media_service = MessageRemoteMediaService(self)
        # 启动时顺便清理旧的去重记录和临时图片，避免历史残留影响新会话。
        self.cleanup_old_records()

        # 第三阶段：装配具体的消息构建器、推送流程和融合策略服务。
        self._bootstrap_service.setup_message_components()
        self._push_execution_service = PushExecutionService(self)
        self._push_flow_handler = PushFlowHandler(self)
        self._message_build_service = MessageBuildService(self)
        self._cenc_fusion_service = CENCFusionService(
            self,
            # 融合完成后统一回落到 execute_push，保证去重/执行/后处理路径一致。
            execute_push=self._push_flow_handler.execute_push,
        )
        self._cwa_eew_fusion_service = CWAEewFusionService(
            self,
            execute_push=self._push_flow_handler.execute_push,
        )
        self._support_service = MessageManagerSupportService(self)
        self._system_notification_service = MessageSystemNotificationService(self)
        self._push_orchestrator = PushOrchestrator(
            config=self.config,
            # 对外 push_event() 最终由 orchestrator 统一决策是否进入融合或直推路径。
            execute_push=self._push_flow_handler.execute_push,
            cenc_fusion_service=self._cenc_fusion_service,
            cwa_eew_fusion_service=self._cwa_eew_fusion_service,
        )

    async def get_remote_media_session(self, timeout_seconds: int | None = None):
        """获取可复用的远程媒体抓取 Session。"""
        return await self._remote_media_service.get_session(timeout_seconds)

    async def close_remote_media_session(self) -> None:
        """关闭远程媒体抓取 Session。"""
        await self._remote_media_service.close_session()

    @staticmethod
    def _is_http_url(url: str | None) -> bool:
        """判断是否为可抓取的 HTTP(S) URL。"""
        return MessageManagerSupportService.is_http_url(url)

    @staticmethod
    def _is_image_content_type(content_type: str | None) -> bool:
        """判断响应 Content-Type 是否为图片。"""
        return MessageRemoteMediaService.is_image_content_type(content_type)

    @staticmethod
    def _guess_image_content_type(url: str) -> str | None:
        """根据 URL 后缀猜测图片 MIME。"""
        return MessageRemoteMediaService.guess_image_content_type(url)

    async def fetch_remote_media(
        self,
        url: str,
        *,
        expected_kind: str,
        timeout_seconds: int = 15,
        max_bytes: int = 15 * 1024 * 1024,
    ) -> dict[str, Any] | None:
        """抓取远程媒体并返回结构化结果。"""
        return await self.remote_media_fetcher.fetch(
            url,
            expected_kind=expected_kind,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )

    async def _append_remote_image_component(
        self,
        chain: MessageChain,
        image_url: str,
        *,
        media_label: str,
        allow_url_fallback: bool = True,
    ) -> bool:
        """将远程图片优先转为 Base64 附加到消息链，失败时可回退 URL。"""
        return await self._support_service.append_remote_image_component(
            chain,
            image_url,
            media_label=media_label,
            allow_url_fallback=allow_url_fallback,
        )

    @property
    def remote_media_fetcher(self):
        """远程媒体抓取器。"""
        return self._bootstrap_service.remote_media_fetcher

    @property
    def text_message_builder(self):
        """文本消息构建器。"""
        return self._bootstrap_service.text_message_builder

    @property
    def card_message_builder(self):
        """卡片消息构建器。"""
        return self._bootstrap_service.card_message_builder

    @property
    def map_attachment_builder(self):
        """地图附件构建器。"""
        return self._bootstrap_service.map_attachment_builder

    @property
    def global_quake_card_builder(self):
        """Global Quake 卡片构建器。"""
        return self._bootstrap_service.global_quake_card_builder

    def resolve_source_id_for_execution(self, event: DisasterEvent) -> str:
        """统一解析执行路径中的 source_id。"""
        return resolve_source_id(event)

    def _build_runtime_components(
        self,
        runtime_config: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """基于运行时配置构建过滤组件（支持会话级配置）。"""
        return self._runtime_component_factory.build(
            runtime_config,
            session_id=session_id,
            default_report_controller=self.report_controller,
        )

    def _cleanup_render_image_cache(self):
        """清理过期或失效的渲染缓存。"""
        self._render_cache.cleanup()

    async def _render_with_cache(
        self,
        cache_key: str,
        renderer: Callable[[], Awaitable[str | None]],
    ) -> str | None:
        """带去重与缓存的渲染包装器。"""
        return await self._render_cache.render(cache_key, renderer)

    @staticmethod
    def _build_map_cache_key(lat: float, lon: float, config: dict[str, Any]) -> str:
        """构建地图渲染缓存键。"""
        return MessageManagerSupportService.build_map_cache_key(lat, lon, config)

    @staticmethod
    def _build_global_quake_card_cache_key(
        earthquake: EarthquakeData,
        message_format_config: dict[str, Any],
        display_timezone: str,
    ) -> str:
        """构建 Global Quake 卡片缓存键。"""
        return MessageManagerSupportService.build_global_quake_card_cache_key(
            earthquake,
            message_format_config,
            display_timezone,
        )

    @staticmethod
    def _build_message_build_cache_key(
        event: DisasterEvent,
        runtime_config: dict[str, Any],
    ) -> str:
        """构建消息构建缓存键（同事件+同渲染参数复用）。"""
        return MessageManagerSupportService.build_message_build_cache_key(
            event,
            runtime_config,
        )

    def should_push_event(
        self,
        event: DisasterEvent,
        runtime_config: dict[str, Any] | None = None,
        session_id: str | None = None,
        filter_reason_out: list[str] | None = None,
        emit_filter_log: bool = True,
        commit_state: bool = True,
    ) -> bool:
        """判断是否应该推送事件。"""
        # 这里仍保留 manager facade，方便旧调用方继续从 manager 进行统一判定；
        # 真正的策略细节已经沉到底层策略函数与运行时组件工厂中。
        runtime_config = runtime_config or self.config
        runtime_components = self._build_runtime_components(runtime_config, session_id)
        return should_push_event_with_components(
            event,
            runtime_config=runtime_config,
            runtime_components=runtime_components,
            session_id=session_id,
            filter_reason_out=filter_reason_out,
            emit_filter_log=emit_filter_log,
            commit_state=commit_state,
            logger_instance=logger,
        )

    async def push_event(
        self,
        event: DisasterEvent,
        target_sessions: list[str] | None = None,
        session_config_getter=None,
    ) -> bool:
        """推送事件入口。兼容保留在 manager 层，对外统一委托给推送编排器。"""
        # manager 只暴露稳定入口；
        # 后续无论融合策略还是普通直推，都由 orchestrator 决定调度到哪条路径。
        return await self._push_orchestrator.push_event(
            event,
            target_sessions=target_sessions,
            session_config_getter=session_config_getter,
        )

    @staticmethod
    def _get_fusion_event_key(data: EarthquakeData) -> str:
        """融合事件键：优先 event_id，回退 id。"""
        return str(getattr(data, "event_id", "") or getattr(data, "id", "")).strip()

    @staticmethod
    def _get_fusion_report_num(data: EarthquakeData) -> int:
        """融合报次：统一走事件元数据解析，非正整数时回退为 1。"""
        event = DisasterEvent(
            id=getattr(data, "id", "")
            or getattr(data, "event_id", "")
            or "fusion-event",
            data=data,
            source=data.source,
            disaster_type=data.disaster_type,
        )
        return resolve_report_num(event) or 1

    def _select_cached_report_payload(
        self,
        reports: dict[int, dict[str, Any]],
        target_report: int,
    ) -> dict[str, Any] | None:
        """按报次精确匹配缓存（仅同报次融合）。"""
        return self._fusion_state_store.select_cached_report_payload(
            reports, target_report
        )

    def _find_best_pending_key(
        self, pending_dict: dict[str, dict[str, Any]], event_key: str, report_num: int
    ) -> str | None:
        """在同 event_key 的 pending 中按报次精确匹配。"""
        return self._fusion_state_store.find_best_pending_key(
            pending_dict,
            event_key,
            report_num,
        )

    def _prune_fusion_states(self) -> None:
        """清理融合 pending 与缓存中过期条目。"""
        self._fusion_state_store.prune()

    def _handle_cenc_wolfx_fusion(self, wolfx_event: DisasterEvent):
        """处理 Wolfx CENC 消息融合（缓存优先 + 精确匹配）。"""
        self._cenc_fusion_service.handle_wolfx_event(wolfx_event)

    def _extract_cwa_wolfx_impact_area(
        self, wolfx_earthquake: EarthquakeData
    ) -> str | None:
        """提取 Wolfx CWA EEW 影响区域字段。"""
        return self._cwa_eew_fusion_service.extract_wolfx_impact_area(wolfx_earthquake)

    def _handle_cwa_wolfx_fusion(self, wolfx_event: DisasterEvent):
        """处理 Wolfx CWA EEW 消息融合（缓存优先 + 精确匹配）。"""
        self._cwa_eew_fusion_service.handle_wolfx_event(wolfx_event)

    async def _push_split_map(
        self, event: DisasterEvent, target_sessions: list[str], config: dict
    ):
        """后台渲染并发送分离的地图图片。兼容保留在 manager 层，实际委托构建服务。"""
        await self._message_build_service.push_split_map(event, target_sessions, config)

    def build_message(self, event: DisasterEvent) -> MessageChain:
        """构建消息。兼容保留在 manager 层，实际委托消息构建服务。"""
        return self._message_build_service.build_message(event)

    async def build_message_async(
        self,
        event: DisasterEvent,
        runtime_config: dict[str, Any] | None = None,
    ) -> MessageChain:
        """构建消息（异步版本）。兼容保留在 manager 层，实际委托消息构建服务。"""
        return await self._message_build_service.build_message_async(
            event,
            runtime_config=runtime_config,
        )

    async def render_earthquake_list_card(
        self, events: list[dict], source_name: str
    ) -> str | None:
        """渲染地震列表卡片"""
        return await self.card_message_builder.render_earthquake_list_card(
            events, source_name
        )

    async def send_message(self, session: str, message: MessageChain):
        """发送消息到指定会话。兼容保留在 manager 层，实际委托会话发送器。"""
        await self.session_sender.send(session, message)

    async def push_system_message(
        self, message: str, target_sessions: list[str] | None = None
    ) -> int:
        """推送系统提示消息（不走事件过滤）"""
        return await self._system_notification_service.push_system_message(
            message,
            target_sessions=target_sessions,
        )

    async def cleanup_browser(self):
        """清理浏览器与远程媒体抓取资源。兼容保留在 manager 层，实际委托资源清理服务。"""
        await self._resource_cleanup_service.cleanup_browser()

    def cleanup_old_records(self):
        """清理旧记录。兼容保留在 manager 层，实际委托资源清理服务。"""
        self._resource_cleanup_service.cleanup_old_records()
