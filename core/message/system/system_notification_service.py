"""
系统消息推送服务。
"""

from __future__ import annotations

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain


class MessageSystemNotificationService:
    """系统提示消息推送服务。"""

    def __init__(self, manager):
        self.manager = manager

    async def push_system_message(
        self,
        message: str,
        target_sessions: list[str] | None = None,
    ) -> int:
        """推送系统提示消息（不走事件过滤）。"""
        sessions = (
            target_sessions
            if target_sessions is not None
            else self.manager.config.get("target_sessions", [])
        )
        if not sessions:
            logger.warning("[灾害预警] 没有配置目标会话，系统提示消息未发送")
            return 0

        # 系统消息不绑定 DisasterEvent，因此只构造最小 Plain 消息链并直发。
        msg_chain = MessageChain([Comp.Plain(message)])
        success_count = 0
        for session in sessions:
            try:
                await self.manager.send_message(session, msg_chain)
                success_count += 1
            except Exception as e:
                logger.error(f"[灾害预警] 系统提示消息发送到 {session} 失败: {e}")

        if success_count > 0:
            logger.info(f"[灾害预警] 系统提示消息已发送到 {success_count} 个会话")
        return success_count
