"""通知数据规范化器。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class NotificationNormalizer:
    """负责校验、规范化与排序远端通知对象。"""

    REQUIRED_KEYS = {"id", "title", "content", "type", "created_at", "is_active"}

    def normalize_item(self, raw: Any) -> dict[str, Any] | None:
        """将单条远端通知转换为前端可消费的稳定结构。"""
        if not isinstance(raw, dict):
            return None
        if not self.REQUIRED_KEYS.issubset(raw.keys()):
            return None

        try:
            notification_id = int(raw.get("id"))
        except (TypeError, ValueError):
            return None

        title = str(raw.get("title", "")).strip()
        content = str(raw.get("content", "")).strip()
        notification_type = str(raw.get("type", "")).strip().upper()
        created_at = str(raw.get("created_at", "")).strip()
        is_active = raw.get("is_active")
        content_format = str(raw.get("content_format", "text")).strip().lower()
        if content_format in {"plain", "plaintext"}:
            content_format = "text"
        if content_format not in {"text", "markdown"}:
            content_format = "text"

        if (
            not title
            or not content
            or not notification_type
            or not isinstance(is_active, bool)
        ):
            return None

        try:
            normalized_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return None

        return {
            "id": notification_id,
            "app_id": raw.get("app_id"),
            "title": title,
            "content": content,
            "type": notification_type,
            "created_at": normalized_dt.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "is_active": is_active,
            "content_format": content_format,
        }

    def normalize_items(self, raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """规范化通知列表，并仅保留激活通知。"""
        normalized_items = []
        for raw in raw_items:
            item = self.normalize_item(raw)
            if not item or not item.get("is_active", False):
                continue
            normalized_items.append(item)
        return self.sort_items(normalized_items)

    @staticmethod
    def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按创建时间与 ID 倒序排列通知。"""
        return sorted(
            items,
            key=lambda item: (item.get("created_at", ""), item.get("id", 0)),
            reverse=True,
        )
