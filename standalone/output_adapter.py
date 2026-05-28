"""
消息输出适配器。

在独立模式下，消息输出到控制台 / 日志文件，而非 QQ/微信等平台。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger("astrbot_plugin_disaster_warning")


class OutputAdapter:
    """消息输出适配器基类。"""

    async def send(self, session: str, message) -> None:
        raise NotImplementedError


class ConsoleOutputAdapter(OutputAdapter):
    """控制台输出适配器 —— 将消息内容打印到标准输出。"""

    def __init__(self, *, show_images: bool = False, output_dir: str | None = None):
        self.show_images = show_images
        self.output_dir = Path(output_dir) if output_dir else None

    async def send(self, session: str, message) -> None:
        # 统一使用 MessageChain.to_plain_text() 提取文本
        if hasattr(message, "to_plain_text"):
            text = message.to_plain_text()
        elif hasattr(message, "chain"):
            # 手动处理
            parts = []
            for item in message.chain:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif isinstance(item, str):
                    parts.append(item)
                elif hasattr(item, "to_dict"):
                    d = item.to_dict()
                    if d.get("type") == "plain":
                        parts.append(d.get("text", ""))
                else:
                    parts.append(str(item))
            text = "".join(parts)
        elif isinstance(message, str):
            text = message
        else:
            text = str(message)

        if not text.strip():
            return

        print(f"\n{'='*60}")
        print(f"📨 [消息输出]")
        print(text.strip())
        print(f"{'='*60}\n")

    def _format_component(self, comp: dict) -> str:
        ctype = comp.get("type", "unknown")
        if ctype == "plain":
            return comp.get("text", "")
        elif ctype == "image":
            subtype = comp.get("subtype", "unknown")
            if subtype == "base64":
                b64 = comp.get("base64", "")
                size_hint = f" ({len(b64)} bytes base64)" if b64 else ""
                if self.show_images and self.output_dir:
                    return self._save_image(b64, comp)
                return f"[Image:{subtype}{size_hint}]"
            elif subtype == "url":
                return f"[Image: {comp.get('url', '')}]"
            elif subtype == "file":
                return f"[Image: {comp.get('file_path', '')}]"
            return f"[Image:{subtype}]"
        elif ctype == "reply":
            return f"[Reply to msg_id={comp.get('message_id', '')}]"
        elif ctype == "forward_nodes":
            nodes = comp.get("nodes", [])
            lines = ["--- 合并转发消息 ---"]
            for n in nodes:
                name = n.get("name", "")
                content = n.get("content", [])
                texts = [c.get("text", "") for c in content if c.get("type") == "plain"]
                lines.append(f"  [{name}]: {''.join(texts)}")
            lines.append("--- 合并转发结束 ---")
            return "\n".join(lines)
        elif ctype == "forward_node":
            return f"[ForwardNode: {comp.get('name', '')}]"
        return json.dumps(comp, ensure_ascii=False)

    def _save_image(self, b64: str, comp: dict) -> str:
        import base64
        try:
            data = base64.b64decode(b64)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            import time
            filename = f"img_{int(time.time() * 1000)}.png"
            path = self.output_dir / filename
            path.write_bytes(data)
            return f"[Image saved: {path}]"
        except Exception as e:
            return f"[Image decode error: {e}]"


class FileOutputAdapter(OutputAdapter):
    """文件输出适配器 —— 将消息内容写入日志文件。"""

    def __init__(self, log_file: str = "disaster_warning_output.log"):
        self.log_file = Path(log_file)

    async def send(self, session: str, message) -> None:
        text = ""
        chain = None
        if hasattr(message, "chain"):
            chain = message.chain
        elif hasattr(message, "to_plain_text"):
            text = message.to_plain_text()
        elif isinstance(message, str):
            text = message

        if chain:
            parts = []
            for item in chain:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif isinstance(item, dict) and item.get("type") == "plain":
                    parts.append(item["text"])
            text = "".join(parts)

        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            import datetime
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] [{session}] {text}\n")


# ============================================================
# 全局输出适配器实例
# ============================================================

_output_adapter: OutputAdapter = ConsoleOutputAdapter()


def get_output_adapter() -> OutputAdapter:
    return _output_adapter


def set_output_adapter(adapter: OutputAdapter) -> None:
    global _output_adapter
    _output_adapter = adapter
