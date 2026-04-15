"""
事件处理流水线。
负责串联灾害事件的日志记录、推送、统计与 Web 实时通知，减少 DisasterWarningService 中的编排职责。
"""

from __future__ import annotations

from datetime import datetime

from astrbot.api import logger


class EventPipeline:
    """灾害事件处理流水线。"""

    def __init__(self, service):
        self.service = service

    async def handle(self, event) -> None:
        """执行事件主处理流程。"""
        logger.debug(f"[灾害预警] 处理灾害事件: {event.id}")
        # 第一阶段：先写摘要日志，便于后续定位该事件是否进入过主流程。
        self.service.log_event(event)

        # 第二阶段：按会话级配置执行推送，target_sessions 与 session_config_getter 配合实现多会话差异化策略。
        target_sessions = self.service.session_config_manager.list_target_sessions()
        push_result = await self.service.message_manager.push_event(
            event,
            target_sessions=target_sessions,
            session_config_getter=self.service.session_config_manager.get_effective_config,
        )
        if push_result:
            logger.debug(f"[灾害预警] ✅ 事件推送成功: {event.id}")
        else:
            logger.debug(f"[灾害预警] 事件推送被过滤: {event.id}")

        # 第三阶段：无论是否最终推送成功，都记录统计结果，便于后续分析过滤与命中情况。
        await self.service.statistics_manager.record_push(
            event,
            pushed_sessions=self.service.message_manager.last_success_sessions,
        )

        # 第四阶段：向 Web 管理端广播轻量摘要，避免直接传输完整事件对象。
        if self.service.web_admin_server:
            try:
                event_summary = {
                    "id": event.id,
                    "type": event.disaster_type.value
                    if hasattr(event.disaster_type, "value")
                    else str(event.disaster_type),
                    "source": event.source.value
                    if hasattr(event.source, "value")
                    else str(event.source),
                    "time": datetime.now().isoformat(),
                }
                await self.service.web_admin_server.notify_event(event_summary)
            except Exception as ws_e:
                logger.debug(f"[灾害预警] WebSocket 通知失败: {ws_e}")
