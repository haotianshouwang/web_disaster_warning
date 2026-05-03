"""
network 子系统导出。
统一收口 source 消息路由与网络基础设施的公共入口。
"""

from .source_message_router import SourceMessageRouter

__all__ = ["SourceMessageRouter"]
