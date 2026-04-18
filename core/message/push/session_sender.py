"""
会话消息发送适配器。
封装 AstrBot 上下文发送能力，便于后续扩展重试、限速与回执统计。
"""

from __future__ import annotations


class SessionSender:
    """会话发送器。"""

    def __init__(self, context):
        self.context = context

    async def send(self, session: str, message):
        """发送消息到指定会话。"""
        await self.context.send_message(session, message)
