"""
统计记录合并器。
负责 recent_pushes / major_events 中已有记录的匹配、合并与更新。
"""

from __future__ import annotations

from typing import Any

from ....models.models import (
    DisasterEvent,
    EarthquakeData,
    TsunamiData,
    WeatherAlarmData,
)
from .event_record_factory import EventRecordFactory


class EventRecordMerger:
    """事件记录合并器。"""

    @staticmethod
    def merge_existing_record(
        target_list: list[dict[str, Any]],
        event: DisasterEvent,
        *,
        source_id: str,
        event_unique_id: str,
        current_time: str,
        description: str,
        earthquake_level: float | None = None,
    ) -> dict[str, Any] | None:
        if isinstance(event.data, EarthquakeData):
            return EventRecordMerger._merge_earthquake_record(
                target_list,
                event,
                source_id=source_id,
                event_unique_id=event_unique_id,
                current_time=current_time,
                description=description,
                earthquake_level=earthquake_level,
            )

        if isinstance(event.data, (WeatherAlarmData, TsunamiData)):
            return EventRecordMerger._merge_non_earthquake_record(
                target_list,
                event,
                source_id=source_id,
                event_unique_id=event_unique_id,
                current_time=current_time,
                description=description,
            )

        return None

    @staticmethod
    def _merge_earthquake_record(
        target_list: list[dict[str, Any]],
        event: DisasterEvent,
        *,
        source_id: str,
        event_unique_id: str,
        current_time: str,
        description: str,
        earthquake_level: float | None,
    ) -> dict[str, Any] | None:
        real_event_id = event.data.event_id
        if not real_event_id:
            return None

        for i, record in enumerate(target_list):
            rec_source = record.get("source")
            rec_real_id = record.get("real_event_id")
            rec_legacy_id = record.get("event_id")
            # 同一来源下才尝试合并，避免不同数据源恰好 event_id 相同造成误命中。
            if rec_source != source_id:
                continue

            rec_unique_id = record.get("unique_id")
            is_match = False
            if rec_real_id and rec_real_id == real_event_id:
                is_match = True
            elif not rec_real_id and rec_legacy_id == real_event_id:
                is_match = True
            elif rec_unique_id and rec_unique_id == event_unique_id:
                is_match = True

            if not is_match:
                continue

            old_record = record.copy()
            old_record.pop("history", None)
            if "history" not in record:
                record["history"] = []
            record["history"].insert(0, old_record)
            if len(record["history"]) > 50:
                record["history"] = record["history"][:50]

            EventRecordFactory.apply_common_fields(
                record,
                event,
                current_time=current_time,
                event_unique_id=event_unique_id,
                description=description,
                source_id=source_id,
                update_count=record.get("update_count", 1) + 1,
            )
            record["real_event_id"] = real_event_id
            EventRecordFactory.apply_earthquake_fields(
                record,
                event,
                earthquake_level=earthquake_level,
            )

            updated_record = target_list.pop(i)
            target_list.insert(0, updated_record)
            return updated_record

        return None

    @staticmethod
    def _merge_non_earthquake_record(
        target_list: list[dict[str, Any]],
        event: DisasterEvent,
        *,
        source_id: str,
        event_unique_id: str,
        current_time: str,
        description: str,
    ) -> dict[str, Any] | None:
        for i, record in enumerate(target_list):
            rec_source = record.get("source")
            rec_unique_id = record.get("unique_id")
            if rec_source != source_id or rec_unique_id != event_unique_id:
                continue

            EventRecordFactory.apply_common_fields(
                record,
                event,
                current_time=current_time,
                event_unique_id=event_unique_id,
                description=description,
                source_id=source_id,
                update_count=1,
            )
            record["subtitle"] = ""
            record["weather_detail"] = ""
            record.pop("history", None)

            if isinstance(event.data, WeatherAlarmData):
                EventRecordFactory.apply_weather_fields(record, event)
            elif isinstance(event.data, TsunamiData):
                EventRecordFactory.apply_tsunami_fields(record, event)

            updated_record = target_list.pop(i)
            target_list.insert(0, updated_record)
            return updated_record

        return None
