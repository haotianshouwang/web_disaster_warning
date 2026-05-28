"""
消息组件兼容层（Comp）。

提供与原 AstrBot API 兼容的 Plain、Image、Reply、Nodes、Node 等消息组件。
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field


# ============================================================
# Plain - 纯文本组件
# ============================================================

@dataclass
class Plain:
    """纯文本消息组件。"""
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "plain", "text": self.text}


# ============================================================
# Image - 图片组件
# ============================================================

@dataclass
class Image:
    """图片消息组件。"""

    _type: str = "image"
    data: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def fromBase64(b64_data: str) -> "Image":
        """从 Base64 数据创建图片组件。"""
        return Image(_type="image_base64", data={"base64": b64_data})

    @staticmethod
    def fromURL(url: str) -> "Image":
        """从 URL 创建图片组件。"""
        return Image(_type="image_url", data={"url": url})

    @staticmethod
    def fromFileSystem(path: str) -> "Image":
        """从本地文件系统路径创建图片组件。"""
        return Image(_type="image_file", data={"file_path": path})

    def to_dict(self) -> dict[str, Any]:
        if self._type == "image_base64":
            return {
                "type": "image",
                "subtype": "base64",
                "base64": self.data.get("base64", ""),
            }
        elif self._type == "image_url":
            return {
                "type": "image",
                "subtype": "url",
                "url": self.data.get("url", ""),
            }
        elif self._type == "image_file":
            return {
                "type": "image",
                "subtype": "file",
                "file_path": self.data.get("file_path", ""),
            }
        return {"type": "image", "subtype": "unknown"}


# ============================================================
# Reply - 引用回复组件
# ============================================================

@dataclass
class Reply:
    """引用回复组件。"""
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "reply", "message_id": self.id}


# ============================================================
# Node / Nodes - 合并转发组件
# ============================================================

@dataclass
class Node:
    """合并转发节点。"""
    uin: str = ""
    name: str = ""
    content: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        content_dicts = []
        for item in self.content:
            if hasattr(item, "to_dict"):
                content_dicts.append(item.to_dict())
            elif isinstance(item, dict):
                content_dicts.append(item)
            elif isinstance(item, str):
                content_dicts.append({"type": "plain", "text": item})
        return {
            "type": "forward_node",
            "uin": self.uin,
            "name": self.name,
            "content": content_dicts,
        }


@dataclass
class Nodes:
    """合并转发消息（包含多个节点）。"""
    nodes: list[Node] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "forward_nodes",
            "nodes": [n.to_dict() for n in self.nodes],
        }
