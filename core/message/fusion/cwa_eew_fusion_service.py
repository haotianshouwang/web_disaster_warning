"""
CWA EEW 融合策略服务。
负责处理 Fan CWA EEW 等待 Wolfx 影响区域补充与 Wolfx 到达后的缓存/唤醒流程。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger

from ....models.models import DisasterEvent, EarthquakeData


class CWAEewFusionService:
    """CWA EEW 融合策略服务。"""

    def __init__(self, manager, execute_push):
        self.manager = manager
        self._execute_push = execute_push

    async def intercept_fan_event(
        self,
        event: DisasterEvent,
        timeout: int,
        *,
        target_sessions: list[str] | None = None,
        session_config_getter=None,
    ) -> bool:
        if not isinstance(event.data, EarthquakeData):
            return await self._execute_push(
                event,
                target_sessions=target_sessions,
                session_config_getter=session_config_getter,
            )

        # 与 CENC 融合相同，进入等待前先做状态清理，避免过期的 pending 影响匹配结果。
        self.manager._prune_fusion_states()
        store = self.manager._fusion_state_store

        event_key = self.manager._get_fusion_event_key(event.data)
        report_num = self.manager._get_fusion_report_num(event.data)
        cached_payload = self.manager._select_cached_report_payload(
            store.cwa_eew_wolfx_cache.get(event_key, {}), report_num
        )
        if isinstance(cached_payload, dict) and cached_payload.get("impact_area"):
            impact_area = str(cached_payload["impact_area"]).strip()
            if impact_area:
                # 影响区域优先写入 raw_data，便于格式化层从统一补充字段读取；
                # province 仅作为缺省展示字段补位。
                if not isinstance(event.data.raw_data, dict):
                    event.data.raw_data = {}
                event.data.raw_data["wolfx_impact_area"] = impact_area
                if not getattr(event.data, "province", None):
                    event.data.province = impact_area
                logger.info(
                    f"[灾害预警] 融合策略: Fan CWA EEW 事件 {event.id} 命中 Wolfx 缓存并补充影响区域: {impact_area}"
                )
                return await self._execute_push(
                    event,
                    target_sessions=target_sessions,
                    session_config_getter=session_config_getter,
                )

        logger.info(
            f"[灾害预警] 融合策略: 拦截 Fan CWA EEW 事件 {event.id} (event_key={event_key}, report={report_num})，等待 Wolfx 补充 ({timeout}s)..."
        )

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        pending_key = f"{event_key}#{report_num}#{event.id}#{int(time.time() * 1000)}"

        store.cwa_eew_pending[pending_key] = {
            "event": event,
            "future": future,
            "event_key": event_key,
            "report_num": report_num,
            "created_at": time.time(),
        }

        async def wait_timeout():
            try:
                await asyncio.sleep(timeout)
                if not future.done():
                    future.set_result("timeout")
            except Exception as e:
                if not future.done():
                    future.set_exception(e)

        asyncio.create_task(wait_timeout())

        try:
            result = await future
            store.cwa_eew_pending.pop(pending_key, None)

            if result == "timeout":
                logger.info("[灾害预警] 融合策略: CWA EEW 等待超时，推送原始 Fan 事件")
                return await self._execute_push(
                    event,
                    target_sessions=target_sessions,
                    session_config_getter=session_config_getter,
                )
            if result == "fused":
                logger.info(
                    "[灾害预警] 融合策略: CWA EEW 融合完成，推送补充后的 Fan 事件"
                )
                return await self._execute_push(
                    event,
                    target_sessions=target_sessions,
                    session_config_getter=session_config_getter,
                )
        except Exception as e:
            logger.error(f"[灾害预警] CWA EEW 融合策略处理异常: {e}")
            return await self._execute_push(
                event,
                target_sessions=target_sessions,
                session_config_getter=session_config_getter,
            )

        return False

    def extract_wolfx_impact_area(self, wolfx_earthquake: EarthquakeData) -> str | None:
        raw_data = getattr(wolfx_earthquake, "raw_data", {})
        if not isinstance(raw_data, dict):
            raw_data = {}

        def _normalize_area(value: Any) -> str:
            # Wolfx 不同来源字段形态可能是字符串或数组，这里统一规整为单个展示文本。
            if isinstance(value, list):
                parts = [str(x).strip() for x in value if str(x).strip()]
                return "、".join(parts)
            if isinstance(value, str):
                return value.strip()
            return ""

        # province 是最稳定的上游字段，优先直接使用。
        if getattr(wolfx_earthquake, "province", None):
            province_text = str(wolfx_earthquake.province).strip()
            if province_text:
                return province_text

        candidates: list[Any] = [
            raw_data.get("locationDesc"),
            raw_data.get("impactArea"),
            raw_data.get("ImpactArea"),
            raw_data.get("affectedArea"),
            raw_data.get("AffectedArea"),
            raw_data.get("area"),
            raw_data.get("Area"),
        ]

        warn_area = raw_data.get("WarnArea")
        if isinstance(warn_area, dict):
            candidates.extend(
                [
                    warn_area.get("Chiiki"),
                    warn_area.get("Area"),
                    warn_area.get("AreaName"),
                    warn_area.get("Name"),
                    warn_area.get("County"),
                ]
            )
        else:
            candidates.append(warn_area)

        for candidate in candidates:
            normalized = _normalize_area(candidate)
            if normalized:
                return normalized

        return None

    def handle_wolfx_event(self, wolfx_event: DisasterEvent):
        if not isinstance(wolfx_event.data, EarthquakeData):
            return

        impact_area = self.extract_wolfx_impact_area(wolfx_event.data)
        if not impact_area:
            return

        self.manager._prune_fusion_states()
        store = self.manager._fusion_state_store

        event_key = self.manager._get_fusion_event_key(wolfx_event.data)
        report_num = self.manager._get_fusion_report_num(wolfx_event.data)
        if not event_key:
            return

        event_cache = store.cwa_eew_wolfx_cache.setdefault(event_key, {})
        event_cache[report_num] = {
            "impact_area": impact_area,
            "created_at": time.time(),
        }

        pending_key = self.manager._find_best_pending_key(
            store.cwa_eew_pending, event_key, report_num
        )
        if not pending_key:
            return

        try:
            item = store.cwa_eew_pending.get(pending_key)
            if not isinstance(item, dict):
                return

            fan_event = item.get("event")
            future = item.get("future")
            if not isinstance(fan_event, DisasterEvent) or not isinstance(
                fan_event.data, EarthquakeData
            ):
                return

            if not isinstance(fan_event.data.raw_data, dict):
                fan_event.data.raw_data = {}

            fan_event.data.raw_data["wolfx_impact_area"] = impact_area
            if not getattr(fan_event.data, "province", None):
                fan_event.data.province = impact_area

            logger.info(
                f"[灾害预警] 融合策略: 成功用 Wolfx 补充 Fan CWA EEW 事件 {pending_key} 的影响区域: {impact_area}"
            )

            if future is not None and hasattr(future, "done") and not future.done():
                future.set_result("fused")

            store.cwa_eew_pending.pop(pending_key, None)
        except Exception as e:
            logger.error(f"[灾害预警] CWA EEW 融合操作失败: {e}")
