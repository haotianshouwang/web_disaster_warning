"""
MessageChain, filter 和 AstrMessageEvent 兼容层。
"""

from __future__ import annotations

from typing import Any


class MessageChain:
    """兼容 AstrBot 的 MessageChain。"""

    def __init__(self, chain: list[Any] | None = None):
        self.chain: list[Any] = chain or []

    def to_dict(self) -> dict[str, Any]:
        components = []
        for item in self.chain:
            if hasattr(item, "to_dict"):
                components.append(item.to_dict())
            elif isinstance(item, dict):
                components.append(item)
            elif isinstance(item, str):
                components.append({"type": "plain", "text": item})
            else:
                components.append({"type": "unknown", "data": str(item)})
        return {"type": "message_chain", "components": components}

    def to_plain_text(self) -> str:
        """提取纯文本内容（递归处理合并转发、图片等所有组件类型）。"""
        texts: list[str] = []
        for item in self.chain:
            _extract_text(item, texts)
        return "".join(texts)


def _extract_text(item, texts: list[str]) -> None:
    """递归提取组件树中的文本。"""
    if hasattr(item, "text") and isinstance(item.text, str):
        texts.append(item.text)
    elif isinstance(item, str):
        texts.append(item)
    elif hasattr(item, "to_dict"):
        _extract_text_from_dict(item.to_dict(), texts)


def _extract_text_from_dict(d: dict, texts: list[str]) -> None:
    t = d.get("type", "")
    if t == "plain":
        texts.append(d.get("text", ""))
    elif t == "reply":
        pass
    elif t == "image":
        subtype = d.get("subtype", "")
        if subtype == "base64":
            texts.append(f"[图片: {len(d.get('base64', ''))}B]")
        elif subtype == "url":
            texts.append(f"[图片: {d.get('url', '')}]")
        elif subtype == "file":
            texts.append(f"[图片文件: {d.get('file_path', '')}]")
        else:
            texts.append("[图片]")
    elif t == "forward_nodes":
        for n in d.get("nodes", []):
            if isinstance(n, dict):
                _extract_text_from_dict(n, texts)
    elif t == "forward_node":
        name = d.get("name", "")
        texts.append(f"\n{'─' * 50}\n")
        texts.append(f"  [{name}]\n")
        texts.append(f"{'─' * 50}\n")
        for c in d.get("content", []):
            if isinstance(c, dict):
                _extract_text_from_dict(c, texts)
        texts.append("\n")
    elif t == "message_chain":
        for c in d.get("components", []):
            if isinstance(c, dict):
                _extract_text_from_dict(c, texts)
    else:
        texts.append(f"[{t}]")


class _Filter:
    """兼容 @filter.command() 装饰器。"""

    @staticmethod
    def command(name: str, alias: dict[str, str] | None = None):
        def decorator(func):
            func._is_command = True
            func._command_name = name
            func._command_aliases = alias or {}
            return func
        return decorator

    @staticmethod
    def on_astrbot_loaded():
        def decorator(func):
            func._is_lifecycle_hook = True
            func._hook_name = "on_astrbot_loaded"
            return func
        return decorator


filter = _Filter()


class AstrMessageEvent:
    """兼容 AstrBot 的 AstrMessageEvent。"""

    def __init__(
        self,
        sender_id: str = "cli",
        session_umo: str = "standalone:Message:cli",
        self_id: str = "disaster_warning_bot",
        is_admin: bool = True,
    ):
        self._sender_id = sender_id
        self._self_id = self_id
        self._is_admin = is_admin
        self.unified_msg_origin = session_umo
        self.message_obj = None

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_self_id(self) -> str:
        return self._self_id

    def is_admin(self) -> bool:
        return self._is_admin

    def set_admin(self, is_admin: bool) -> None:
        self._is_admin = is_admin

    def plain_result(self, text: str) -> MessageChain:
        from .message_components import Plain
        return self.chain_result([Plain(text)])

    def chain_result(self, chain: list | MessageChain) -> MessageChain:
        if isinstance(chain, MessageChain):
            return chain
        if isinstance(chain, list):
            return MessageChain(chain)
        return MessageChain([chain])
