"""
消息推送流处理器。
负责串联去重、执行、结果后处理与错误遥测，
统一承担原 facade 与 result handler 的轻量流程协调职责。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from astrbot.api import logger

from ....models.data_source_config import get_eew_sources
from ....models.models import DisasterEvent, EarthquakeData


class PushFlowHandler:
    """推送流处理器。"""

    def __init__(self, manager):
        self.manager = manager

    async def execute_push(
        self,
        event: DisasterEvent,
        target_sessions: list[str] | None = None,
        session_config_getter=None,
    ) -> bool:
        """执行完整推送流程：去重、会话执行、后处理与异常遥测。"""
        logger.debug(f"[灾害预警] 执行事件推送流程: {event.id}")

        # 总去重发生在最外层，避免同一事件在进入会话级筛选之前就重复消耗资源。
        if not self.manager.deduplicator.should_push_event(event):
            logger.debug(f"[灾害预警] 事件 {event.id} 被去重器过滤")
            return False

        try:
            execution_result = await self.manager._push_execution_service.execute(
                event,
                target_sessions=target_sessions,
                session_config_getter=session_config_getter,
            )
            # 执行服务只关心“怎么发”，最终日志汇总、拆分地图后处理由 flow handler 统一收口。
            return await self.handle_execution_result(event, execution_result)
        except Exception as e:
            logger.error(f"[灾害预警] 推送事件失败: {e}")
            if self.manager._telemetry and self.manager._telemetry.enabled:
                await self.manager._telemetry.track_error(
                    e,
                    module="core.message_manager._execute_push",
                )
            return False

    async def handle_execution_result(
        self,
        event: DisasterEvent,
        execution_result: dict[str, Any],
    ) -> bool:
        """处理推送执行结果并返回最终是否有成功推送。"""
        push_success_count = int(execution_result.get("push_success_count", 0))
        passed_sessions = list(execution_result.get("passed_sessions", []))
        session_message_format_config = dict(
            execution_result.get("session_message_format_config", {})
        )
        filter_reason_stats = dict(execution_result.get("filter_reason_stats", {}))

        await self._dispatch_split_maps(
            event,
            passed_sessions=passed_sessions,
            session_message_format_config=session_message_format_config,
        )

        self._log_filter_summary(
            event,
            push_success_count=push_success_count,
            filter_reason_stats=filter_reason_stats,
        )

        self.manager.last_success_sessions = list(passed_sessions)
        self._log_push_completion(
            event,
            push_success_count=push_success_count,
            filter_reason_stats=filter_reason_stats,
        )
        return push_success_count > 0

    async def _dispatch_split_maps(
        self,
        event: DisasterEvent,
        *,
        passed_sessions: list[str],
        session_message_format_config: dict[str, dict[str, Any]],
    ) -> None:
        """为需要拆分地图的事件按配置分组异步派发地图推送。"""
        source_id = self.manager.resolve_source_id_for_execution(event)
        # 目前仅 EEW 类源采用“文本先发、地图异步补发”的分离策略，Global Quake 走自己的卡片渲染路径。
        split_map_sources = set(get_eew_sources()) - {"global_quake"}
        if source_id not in split_map_sources or not isinstance(
            event.data, EarthquakeData
        ):
            return

        current_report = getattr(event.data, "updates", 1)
        is_final = getattr(event.data, "is_final", False)
        map_push_n = 5
        # 分离地图推送，降低地震预警类型的消息延迟
        # 并且不是每一报都生成，避免高频预警反复渲染地图造成浏览器压力并避免刷屏
        should_gen_map = (
            current_report == 1 or current_report % map_push_n == 0 or is_final
        )
        if not should_gen_map or not passed_sessions:
            return

        logger.debug(f"[灾害预警] 触发异步地图渲染 (第 {current_report} 报)")
        grouped_sessions: dict[str, list[str]] = {}
        grouped_config: dict[str, dict[str, Any]] = {}
        for session in passed_sessions:
            msg_cfg = session_message_format_config.get(session, {})
            if not msg_cfg.get("include_map", False):
                continue

            # 相同地图配置的会话共用一次渲染结果，减少重复截图。
            config_key = json.dumps(msg_cfg, sort_keys=True, ensure_ascii=False)
            if config_key not in grouped_sessions:
                grouped_sessions[config_key] = []
                grouped_config[config_key] = msg_cfg
            grouped_sessions[config_key].append(session)

        for config_key, sessions in grouped_sessions.items():
            asyncio.create_task(
                self.manager._push_split_map(
                    event,
                    sessions,
                    grouped_config[config_key],
                )
            )

    def _log_filter_summary(
        self,
        event: DisasterEvent,
        *,
        push_success_count: int,
        filter_reason_stats: dict[str, int],
    ) -> None:
        """记录会话筛选结果摘要。"""
        if filter_reason_stats:
            summary = "，".join(
                f"{reason} {count} 个会话"
                for reason, count in sorted(filter_reason_stats.items())
            )
            if push_success_count > 0:
                logger.info(
                    f"[灾害预警] 事件 {event.id} 已完成会话筛选，{push_success_count} 个会话通过，另有 {summary} 被拦截"
                )
            else:
                logger.info(
                    f"[灾害预警] 事件 {event.id} 未通过任何会话的推送条件，拦截情况：{summary}"
                )
        elif push_success_count > 0:
            logger.info(
                f"[灾害预警] 事件 {event.id} 已通过全部会话的推送条件，共 {push_success_count} 个会话"
            )

    def _log_push_completion(
        self,
        event: DisasterEvent,
        *,
        push_success_count: int,
        filter_reason_stats: dict[str, int],
    ) -> None:
        """记录推送完成日志。"""
        if filter_reason_stats and push_success_count > 0:
            summary = "，".join(
                f"{reason}×{count}"
                for reason, count in sorted(filter_reason_stats.items())
            )
            logger.debug(f"[灾害预警] 事件 {event.id} 部分会话被过滤: {summary}")

        if push_success_count > 0:
            logger.info(
                f"[灾害预警] 事件 {event.id} 推送完成，成功推送到 {push_success_count} 个会话"
            )
