"""
事件统计聚合器。
负责处理 StatisticsManager 中的写模型聚合逻辑，
包括接收计数、唯一事件识别、按源统计、类型统计与时间序列更新。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ....models.models import DisasterEvent, EarthquakeData, WeatherAlarmData
from ...support.event_metadata import resolve_source_id


class EventStatsAggregator:
    """事件统计聚合器。"""

    def __init__(self, manager):
        self.manager = manager

    async def aggregate_event(self, event: DisasterEvent) -> dict[str, object]:
        """聚合一次事件写入前的统计状态。"""
        # 聚合器先更新内存统计主状态，再把关键上下文返回给后续 record/persist 阶段复用。
        current_time = datetime.now(timezone.utc).isoformat()
        stats = self.manager.stats
        stats["last_updated"] = current_time

        if "total_received" not in stats:
            stats["total_received"] = stats.get("total_pushes", 0)
        stats["total_received"] += 1

        source_id = resolve_source_id(event)
        source_for_display = source_id

        event_unique_id = self.manager.get_unique_event_id(event)
        source_event_unique_id = f"{source_id}:{event_unique_id}"

        # by_source 应按“同一来源下的唯一事件集合”统计，不能把同一事件多报重复累计
        if source_event_unique_id not in self.manager._recorded_source_event_ids:
            stats["by_source"][source_id] += 1
            self.manager._recorded_source_event_ids.add(source_event_unique_id)
            stats["recent_source_event_ids"].append(source_event_unique_id)
            if len(stats["recent_source_event_ids"]) > 2000:
                stats["recent_source_event_ids"] = stats["recent_source_event_ids"][
                    -2000:
                ]

        is_new_event = event_unique_id not in self.manager._recorded_event_ids
        if is_new_event:
            stats["total_events"] += 1
            self.manager._recorded_event_ids.add(event_unique_id)
            stats["recent_event_ids"].append(event_unique_id)
            if len(stats["recent_event_ids"]) > 500:
                stats["recent_event_ids"] = stats["recent_event_ids"][-500:]

            d_type = event.disaster_type.value
            stats["by_type"][d_type] += 1

            if isinstance(event.data, EarthquakeData):
                self.manager.rule_service.record_earthquake_stats(event.data)
            elif isinstance(event.data, WeatherAlarmData):
                weather_stats_recorded = (
                    await self.manager.rule_service.record_weather_stats(event.data)
                )
                if not weather_stats_recorded:
                    self.manager.rule_service.log_weather_stats_skip()

            self.manager.rule_service.record_time_series(event)

        return {
            "current_time": current_time,
            "source_id": source_id,
            "source_for_display": source_for_display,
            "event_unique_id": event_unique_id,
            "is_new_event": is_new_event,
        }
