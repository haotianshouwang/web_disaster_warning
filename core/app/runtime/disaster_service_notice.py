"""
灾害服务通知与文本展示服务。
负责离线通知编排与 EEW 查询文本生成，
进一步减少 DisasterWarningService 中的展示与通知职责。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...support.config_validator import ConfigValidator


class DisasterServiceNoticeService:
    """灾害服务通知与文本展示服务。"""

    _OFFLINE_STAGE_MAP = {
        "fallback": "进入兜底重试",
        "stop": "停止重连",
    }

    def __init__(self, service):
        self.service = service

    async def handle_offline_notification(self, payload: dict[str, Any]) -> None:
        """处理 WebSocket 管理器离线通知回调。"""
        await self.notify_data_source_offline(
            connection_name=payload.get("connection_name", "unknown"),
            data_source=payload.get("data_source", "unknown"),
            stage=payload.get("stage", "unknown"),
            reason=payload.get("reason", "未知原因"),
            next_retry_in=payload.get("next_retry_in"),
            retry_count=payload.get("retry_count"),
            fallback_count=payload.get("fallback_count"),
        )

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
        """推送数据源离线通知（兜底重试/停止重连）。"""
        if not self.service.message_manager:
            return False

        key = f"{connection_name}:{stage}"
        now = asyncio.get_running_loop().time()
        state = self.service._offline_notification_state.get(key, {})
        last_ts = state.get("last_ts", 0.0)
        ttl_seconds = 1800
        # 相同连接在同一 stage 下 30 分钟内最多通知一次，避免离线抖动造成刷屏。
        if now - last_ts < ttl_seconds:
            return False

        message = self.build_offline_notification_message(
            connection_name=connection_name,
            data_source=data_source,
            stage=stage,
            reason=reason,
            next_retry_in=next_retry_in,
            retry_count=retry_count,
            fallback_count=fallback_count,
        )
        offline_sessions = self.resolve_offline_notification_sessions()
        success = await self.service.message_manager.push_system_message(
            message,
            target_sessions=offline_sessions,
        )
        if success:
            self.service._offline_notification_state[key] = {"last_ts": now}
        return bool(success)

    def resolve_offline_notification_sessions(self) -> list[str]:
        """解析离线通知目标会话列表。"""
        # 优先使用独立的离线通知会话；若未配置，则回退到普通推送会话。
        offline_sessions = ConfigValidator._validate_target_sessions(
            self.service.config.get("offline_notification_sessions", []),
            key_name="offline_notification_sessions",
        )
        if offline_sessions:
            return offline_sessions
        return ConfigValidator._validate_target_sessions(
            self.service.config.get("target_sessions", []),
            key_name="target_sessions",
        )

    def build_offline_notification_message(
        self,
        *,
        connection_name: str,
        data_source: str,
        stage: str,
        reason: str,
        next_retry_in: str | None,
        retry_count: int | None,
        fallback_count: int | None,
    ) -> str:
        """构建离线通知文本。"""
        stage_text = self._OFFLINE_STAGE_MAP.get(stage, stage)
        retry_part = (
            f"短时重试: {retry_count}" if retry_count is not None else "短时重试: 未知"
        )
        fallback_part = (
            f"兜底重试: {fallback_count}"
            if fallback_count is not None
            else "兜底重试: 未知"
        )
        next_retry_part = (
            f"下一次重试: {next_retry_in}" if next_retry_in else "下一次重试: 未知"
        )

        message_lines = [
            "⚠️ 数据源离线通知",
            f"📡 连接: {connection_name}",
            f"🧩 数据源: {data_source}",
            f"⛔ 状态: {stage_text}",
            f"📝 原因: {reason}",
            f"🔁 {retry_part}",
            f"🛟 {fallback_part}",
        ]
        if stage == "fallback":
            message_lines.append(f"⏳ {next_retry_part}")
        return "\n".join(message_lines)

    def get_eew_query_text(self) -> str:
        """生成 /地震预警查询 文本。"""
        status_data = self.service.get_eew_query_status_data()
        institutions = status_data.get("institutions", [])

        # 先按“正在生效 / 已启用但暂无数据 / 未启用”分组，最后再拼接成对用户友好的查询文本。
        active_lines: list[str] = []
        inactive_items: list[tuple[int, str]] = []
        no_data_lines: list[str] = []
        unavailable_lines: list[str] = []

        for item in institutions:
            display_name = item.get("display_name", "未知机构")
            active_name = item.get("active_name", display_name)
            status = item.get("status")

            if status == "unavailable":
                unavailable_lines.append(
                    f"- {display_name}：未启用对应数据源开关，无法计算无 EEW 时间"
                )
                continue

            if status == "no_data":
                no_data_lines.append(
                    f"- {display_name}：已启用数据源，但暂无可计算历史数据"
                )
                continue

            if status == "active":
                magnitude = item.get("magnitude")
                place = item.get("place") or "未知地点"
                mag_text = self._format_magnitude(magnitude)
                active_lines.append(
                    f"[{active_name}] 当前正在发布地震预警：M {mag_text} {place}"
                )
                continue

            elapsed = int(item.get("elapsed_seconds") or 0)
            inactive_items.append(
                (
                    elapsed,
                    f"{self.service.eew_query_service.format_elapsed_seconds(elapsed)} 无 {display_name}",
                )
            )

        inactive_lines = [
            line for _, line in sorted(inactive_items, key=lambda item: item[0])
        ]

        lines: list[str] = []
        if active_lines:
            lines.extend(active_lines)
            if inactive_lines:
                lines.append("")
                lines.extend(inactive_lines)
        else:
            lines.append("当前没有正在生效的地震预警")
            if inactive_lines:
                lines.append("")
                lines.extend(inactive_lines)

        if no_data_lines:
            lines.append("")
            lines.append("以下机构暂无可计算的历史 EEW 数据：")
            lines.extend(no_data_lines)

        if unavailable_lines:
            lines.append("")
            lines.append("以下机构因数据源开关未启用，无法参与计算：")
            lines.extend(unavailable_lines)

        if not lines:
            lines.append("当前没有正在生效的地震预警")

        return "\n".join(lines)

    @staticmethod
    def _format_magnitude(magnitude: Any) -> str:
        """格式化震级显示文本。"""
        if magnitude is None:
            return "?"
        try:
            return f"{float(magnitude):.1f}"
        except Exception:
            return str(magnitude)
